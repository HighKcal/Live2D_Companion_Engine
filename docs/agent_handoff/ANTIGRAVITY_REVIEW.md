# Antigravity Independent Review

> **Review Metadata**  
> - **Reviewer**: Antigravity (Independent Code Reviewer / Cross-Verifier)  
> - **Review Type**: Independent cross-review and empirical verification  
> - **Methodology**: Source code as final authority; Luna reports (`POKE_HANDOFF.md`, `BUG_AUDIT.md`, `CURRENT_STATUS.md`) treated as hypotheses, not ground truth.  
> - **Scope**: Source code inspection, targeted unit tests, runtime verifiers, and isolated scratch probes. No production source, tests, or profiles modified.  
> - **Notice**: This document is also a point-in-time snapshot.

---

## 1. Overall Assessment

현재 Live2D desktop pet 코드베이스는 기본적인 아키텍처(투명 OpenGL 위젯, Live2D v3 래퍼, 프로필 기반 파라미터/모션 매핑, 실루엣 기반 윈도우 마스크)가 매우 견고하게 구축되어 있습니다. 기존 Hibana, Tsubaki 모델에 대한 회귀 검증(`verify_pet.py`, `verify_runtime.py`, `test_model_profiles`, `test_pet_behavior`)은 모두 100% Green을 유지하고 있습니다.

IceGirl에 도입된 **Poke / Annoyance Progression** 기능 역시 엔진 공통 코드(`desktop_pet.py`, `app.py`, `pet_behavior.py`, `model_profiles.py`)에 하드코딩 없이 `profiles/icegirl.json`의 `behavior.poke` 설정을 기반으로 깔끔하게 프로필 주도형(profile-driven)으로 설계되었습니다.

그러나 본 독립 검증 결과, **다음 세션(Sol)이 바로 출시하거나 머지하기 전에 반드시 해결해야 하는 치명적인 상태 트랩(State Trap) 2건과 검증 블로커 1건**이 발견되었습니다:
1. **[B-2 Confirmed]** 일시적 poke 반응(疑惑/白眼/生气) 중에 쓰다듬기(petting)가 성공하면, 쓰다듬기가 끝난 뒤 이전 poke 표정이 복원되고 `idle_kind`가 소실되어 영구 고착되는 버그.
2. **[NEW-2 Found]** 좌클릭 드래그 중에 우클릭 컨텍스트 메뉴를 열면 드래그 상태가 해제되지 않아, 메뉴를 닫은 뒤 버튼을 누르지 않아도 마우스 커서를 따라 펫이 영구 이동하는 고착 버그.
3. **[NEW-4 Found]** `probe_poke.py`가 중간의 QTest hover petting(라인 99) 실패로 조기 중단되어, 정작 검증 대상인 1~8회 poke 시퀀스 전체가 자동 검증되지 못하고 있는 테스트 결함.

반면, Luna가 C-1으로 제기한 "더블클릭 미정의로 인한 입력 누락"은 Qt 기본 이벤트 포워딩 덕분에 실제로는 두 번 모두 정상 인식되는 **False Positive**로 확인되었습니다.

---

## 2. Poke / Annoyance Verification

### 정상 확인된 부분 (Verified & Working)

1. **Short Click 및 Drag Threshold 판정**:
   - `PetCanvas.mousePressEvent`와 `PetCanvas.mouseReleaseEvent`를 통해 `begin_pointer_press` / `end_pointer_release`가 호출됨.
   - 누른 위치와 뗀 위치 모두 실루엣(`silhouette_contains`) 내부여야 하며, 경과 시간 `elapsed <= max_click_seconds` (기본 0.5초), 이동 거리 `< drag_threshold_pixels` (기본 8.0px)일 때만 poke로 인식.
   - 드래그 거리 >= 8px 발생 시 `begin_drag`가 트리거되며, `was_dragging` 플래그로 인해 마우스를 뗄 때 `register_poke`가 절대 호출되지 않음.
2. **Threshold Crossing 및 단계별 진행 (1 -> 8)**:
   - 1회: count만 증가 (표정 변화 없음).
   - 2회: `疑惑.exp3.json` (transient poke, 1.2~1.8초 유지).
   - 3회: threshold 없음 -> 기존 `疑惑` 표정 및 타이머 유지 (중간 재시작 방지 정상).
   - 4회: `白眼.exp3.json` (transient poke).
   - 5회: threshold 없음 -> 기존 `白眼` 유지.
   - 6회: `生气.exp3.json` (transient poke).
   - 7회: threshold 없음 -> 기존 `生气` 유지.
   - 8회: `脸黑.exp3.json` (persistent negative, `idle_until = inf`).
   - 위 전 과정이 `scratch/test_poke_progression.py` 격리 실행을 통해 Live2D 렌더러 상에서 완벽히 확인됨.
3. **Transient 표정 만료 및 Annoyance Inactivity Timeout**:
   - 일시적 표정(2/4/6회)은 `idle_until` 경과 시 `cancel_idle_reaction()`에 의해 정상적으로 baseline 표정으로 복귀.
   - 15초(`reset_seconds`) 동안 추가 poke가 없으면 `tick()` 및 `register_poke()`에서 `reset_annoyance()`를 호출하여 카운트가 0으로 초기화됨.
4. **Persistent NEGATIVE 불변성 (Invariance)**:
   - 8회 도달 후에는 `idle_kind == 'negative'`, `idle_until == inf`로 설정됨.
   - `tick()`의 15초 타이머 및 `idle_until` 타이머에서 `self.idle_kind != 'negative'` 조건으로 보호되어 시간 경과로 절대 자동 해제되지 않음.
   - persistent negative 상태에서 추가 poke는 `register_poke()` 진입 시 `if self.idle_kind == 'negative': return False`로 즉각 거절되어 카운트 증가나 표정 재시작이 발생하지 않음.
5. **Successful Petting -> Direct POSITIVE 전이**:
   - persistent negative 상태에서 쓰다듬기 성공(`start_reaction`) 시, `recovered_negative`가 True가 되어 `cancel_idle_reaction(restore=False)`로 baseline 표정을 거치지 않고 곧바로 POSITIVE 표정/파라미터 레이어로 직행함.
   - `self.reset_annoyance()`가 호출되어 annoyance 카운트도 0으로 정상 리셋됨.
6. **Optional Capability 및 타 모델 영향성 격리**:
   - 엔진 공통 코드에 `icegirl`이나 중국어 표정 파일명 하드코딩 전무.
   - `poke` 키가 없는 `hibana`, `tsubaki`는 `pointer_move`에서 기본 8px 폴백, `register_poke`에서 `self.note_interaction(now)`만 수행 후 False 반환. 기존 동작과 100% 호환.

### 의심되는 부분 (Suspicious / Edge Case Risks)

1. **[Critical] Transient Poke 상태에서 쓰다듬기 시 표정 고착 (B-2)**:
   - 일시적 poke(2/4/6회) 표정이 재생 중일 때 쓰다듬기가 성공하면, `start_reaction`이 이전 표정(`self.reaction_expression`)으로 해당 poke 표정을 저장함.
   - 쓰다듬기가 끝날 때 poke 표정을 다시 띄우지만, `idle_kind`는 이미 None으로 소멸되어 표정이 영원히 만료되지 않고 고착됨.
2. **[High] 드래그 중 우클릭 시 Sticky Drag 고착 (NEW-2)**:
   - 좌클릭 드래그 도중 우클릭 메뉴가 열리면 드래그가 취소되거나 완료되지 않고 `self.dragging = True`로 유지되어, 메뉴를 닫은 후 마우스만 움직여도 펫이 따라다님.
3. **[High] 초대형 창 크기(>2M pixels)에서 Poke 전면 비활성화 (NEW-1)**:
   - 창 크기가 커져 200만 픽셀을 초과하면 `glReadPixels`를 건너뛰고 `refresh_scaled_input_region`만 호출되는데, 이때 `_silhouette_region`이 갱신되지 않아(초기 None) 모든 클릭이 실루엣 외부로 판정됨.

### 미검증 부분 (Unverified via Existing Probes)

1. **Native OS Hover Petting 이벤트 경로**:
   - `probe_poke.py`와 `probe_petting.py`가 Qt QTest 이벤트 전달 한계로 인해 `mouseMove` 이벤트를 위젯으로 전달하지 못함 (`EventProbe` 수신 0건).
   - 실기 네이티브 마우스 호버로만 검증 가능한 상태.

---

## 3. Luna Audit Cross-Check

Luna가 `BUG_AUDIT.md`에서 보고한 15개 항목을 실제 소스 코드 및 런타임 동작과 1:1 대조한 결과입니다.

| 항목 | Luna 주장 요약 | Antigravity 판정 | 근거 및 심각도 의견 |
|---|---|---|---|
| **A-1** | sleep/wake가 persistent NEGATIVE를 해제함 | **PARTIALLY CONFIRMED / DESIGN DECISION** | 소스 상 `desktop_pet.py` L89, L100에서 `include_negative=True`를 전달하여 해제하는 것은 맞음. 그러나 이는 기존 코드의 의도된 sleep 정책이며, 사용자 지침상 버그가 아닌 정책/UX 결정 사안으로 분류됨. |
| **A-2** | `app-state.json`이 비-객체(null, [], string)일 때 시작 크래시 | **CONFIRMED** | `model_profiles.py` L241 `data.get('active_profile')`에서 `except (OSError, ValueError, TypeError)`만 처리하여 `AttributeError`가 잡히지 않음. (Medium) |
| **A-3** | `unittest discover` 실행 시 `research._v3cpp` 임포트 실패 | **CONFIRMED** | `research/__init__.py` L2의 `from ._v3cpp import *` 파일 부재로 디스커버리 에러 발생. 런타임 펫 동작과는 무관한 테스트 인프라 문제. (Test Infra) |
| **B-1** | 프로필 전환 시 이전 모델의 `visual_bounds` 잔여 | **CONFIRMED** | `desktop_pet.py` L300 `load_state()`가 `profile_id` 일치 여부를 검사하지 않고 무조건 bounds를 복원함. 화면 경계 재클램프 누락. (High) |
| **B-2** | transient poke 중 쓰다듬기 발생 시 poke 표정 영구 고착 | **CONFIRMED** | `desktop_pet.py` L557-567에서 `previous_expression`을 `note_interaction()` 전에 캡처하여 poke 표정이 복원되고 영구 고착됨. 격리 테스트로 100% 재현 확인. (High) |
| **B-3** | positive candidate 부재 시 no-op positive 진입 | **PARTIALLY CONFIRMED** | 코드는 no-op 반응 레이어로 들어가지만, 현재 3종 모델 모두 positive가 존재하여 실질적 위험은 낮음. (Low) |
| **B-4** | 머리 영역 내부의 투명 픽셀이 native click-through되지 않음 | **CONFIRMED** | `apply_input_region`에서 `head_input_region` 사각형을 mask에 union함. 머리카락 사이 hover 단절을 막기 위한 의도된 트레이드오프. (Design Trade-off) |
| **B-5** | `model_profiles.py`의 중첩 behavior 검증 누락 | **CONFIRMED** | 최상위 컬렉션 타입만 검사하고 `rest_seconds`, `relocation`, `petting` 세부 수치 배열의 길이/범위 검증이 누락됨. (Medium) |
| **B-6** | 프로필 전환 프로세스 실패 시 롤백 핸드셰이크 부재 | **PARTIALLY CONFIRMED** | `startDetached` 호출 성공 후 즉시 종료하는 구조적 특성. 일반적인 파이썬 앱의 단순 프로세스 교체 패턴임. (Low) |
| **B-7** | Native hover/petting 자동 테스트 불가 | **CONFIRMED** | QTest 무버튼 `mouseMove`가 Windows 비활성 투명 위젯에 전달되지 않는 테스트 하네스 한계임. 실 제품 코드 버그 근거로는 부족함. (Test Harness) |
| **C-1** | 더블클릭 시 두 번째 클릭이 poke로 처리되지 않을 위험 | **FALSE POSITIVE** | PySide6/Qt 기본 구현상 `mouseDoubleClickEvent`는 내부적으로 `mousePressEvent`를 호출함. 실제 호출 스택 및 카운트 검증 결과 2회 클릭 시 정상 2회 인식됨. (None) |
| **C-2** | 마우스 릴리즈 이벤트 소실 시 포인터 상태 잔여 | **PARTIALLY CONFIRMED** | Alt-Tab이나 포커스 강제 이동 시 ungrab 처리가 없는 것은 맞으나 일반 데스크톱 사용 중 발생 빈도 극히 낮음. (Low) |
| **C-3** | 투명 relocation 창이 입력을 가로챌 가능성 | **CONFIRMED** | `opacity=0` 단계에서도 윈도우 마스크는 존재함. 단, 커서가 올라오면 `pointer_over_interactive_area`로 인해 즉시 불투명 복귀함. (Low) |
| **C-4** | 레거시 ZIP 인코딩 판별 휴리스틱 오분류 위험 | **CONFIRMED** | `gbk` 우선 시도로 인한 잠재적 모호성. 현재 준비된 모델들에는 영향 없음. (Low) |
| **C-5** | 대형 텍스처 및 대형 캐릭터 크기 스트레스 미검증 | **CONFIRMED (단, 기능 버그 발견)** | Luna는 단순 성능/깜빡임으로 보았으나, 2M 픽셀 초과 시 실루엣 미생성으로 poke가 전면 먹통이 되는 기능 버그가 실존함 (NEW-1 참조). (High) |

---

## 4. Newly Found Issues

Luna 감사 보고서에 누락되었으나 본 검증에서 독립적으로 발견된 문제들입니다.

### NEW-1: 대형 창 크기(>2M 픽셀)에서 Poke 기능 전면 마비
- **Severity**: High
- **Confidence**: High
- **관련 파일 / 함수**: `desktop_pet.py:111-120` (`PetCanvas.paintGL`), `desktop_pet.py:400-409` (`PetWindow.refresh_scaled_input_region`), `desktop_pet.py:410-418` (`PetWindow.silhouette_contains`)
- **Trigger**: 캐릭터 크기를 슬라이더로 1200px 이상으로 키우거나 (DPR 1.5 기준 1800x1800 > 2M), 대형 크기로 저장된 상태에서 시작할 때.
- **Symptom**: `paintGL`에서 `width * height > 2_000_000` 조건에 걸려 `refresh_input_region` 대신 `refresh_scaled_input_region`이 실행됨. 하지만 이 함수는 `_silhouette_region`을 전혀 생성/갱신하지 않음. 결과적으로 `self._silhouette_region`이 None이 되어 `silhouette_contains()`가 무조건 False를 반환하고, 캐릭터를 클릭해도 poke가 100% 무시됨.
- **Evidence**: `desktop_pet.py` 소스 코드 라인 116-119 및 400-418의 데이터 흐름 분석.
- **Test coverage**: 기존 테스트는 default 300px 크기만 검증함.
- **Recommended timing**: FIX SOON

### NEW-2: 좌클릭 드래그 중 우클릭 컨텍스트 메뉴 오픈 시 '영구 드래그(Sticky Drag)' 고착
- **Severity**: High (사용자 인터랙션 치명적 락업)
- **Confidence**: High
- **관련 파일 / 함수**: `desktop_pet.py:75-76` (`PetCanvas.contextMenuEvent`), `desktop_pet.py:863-875` (`PetWindow.open_menu`), `desktop_pet.py:488-490` (`PetWindow.interrupt`)
- **Trigger**: 사용자가 캐릭터를 좌클릭하여 드래그하는 도중에 우클릭을 누름.
- **Symptom**: Qt는 `contextMenuEvent`를 발생시키고 `open_menu()`가 동기 모달 메뉴(`menu.exec()`)를 띄움. 이 과정에서 마우스 grab이 메뉴로 넘어가고, `open_menu`는 `interrupt()`를 호출하지만 `interrupt()`는 `end_drag()`나 `self.dragging = False`를 수행하지 않음. 메뉴가 닫힌 후에도 `self.dragging`은 여전히 True로 남아있음. 이후 사용자가 마우스 버튼을 전혀 누르지 않고 커서만 움직여도 `mouseMoveEvent`가 `drag_to()`를 호출하여 펫이 마우스 커서에 영구적으로 달라붙어 끌려다님. (다시 좌클릭을 눌렀다 떼야만 풀림).
- **Evidence**: `desktop_pet.py`의 `open_menu`, `interrupt`, `mouseMoveEvent` 제어 흐름 분석.
- **Test coverage**: 마우스 복합 버튼 시퀀스 테스트 전무.
- **Recommended timing**: FIX BEFORE CONTINUING

### NEW-3: `start_idle_reaction` 실패 시 이전 표정 스케줄러 상태 유실
- **Severity**: Medium
- **Confidence**: High
- **관련 파일 / 함수**: `desktop_pet.py:618-632` (`PetWindow.register_poke`)
- **Trigger**: Poke 임계값 도달 시점에 모종의 이유(패널 오픈 중, 슬리핑 중, 또는 에셋 인덱스 불일치)로 `start_idle_reaction`이 False를 반환함.
- **Symptom**: 라인 621에서 `self.cancel_idle_reaction(restore=False)`가 `start_idle_reaction` 호출보다 **먼저** 실행되어 `self.idle_kind`와 `self.idle_expression`을 None으로 날려버림. `start_idle_reaction`이 실패하면 복원도 안 되고 새 스케줄도 등록되지 않아, 화면에는 이전 표정이 남은 채 스케줄러에서 완전히 누락됨.
- **Evidence**: `register_poke` L621 및 L625의 호출 순서.
- **Test coverage**: 없음.
- **Recommended timing**: FIX SOON

### NEW-4: `probe_poke.py`가 중간 hover petting 실패로 인해 정작 poke 시퀀스를 전혀 검증하지 못함
- **Severity**: High (검증 무력화)
- **Confidence**: High
- **관련 파일 / 함수**: `probe_poke.py:98-103` (`PokeProbe.start`)
- **Trigger**: `python probe_poke.py` 실행.
- **Symptom**: 라인 99의 `self.pet()`이 QTest mouseMove 이벤트 수신 불가로 AssertionError를 일으키며 즉시 프로브가 종료됨. 이로 인해 정작 라인 111부터 시작되는 poke 1~8회 카운트, 단계별 표정(疑惑/白眼/生气/脸黑), 15초 타임아웃, persistent negative 방어, 쓰다듬기 회복 검증이 단 한 줄도 실행되지 못함.
- **Evidence**: `probe_poke.py` 실행 로그: `POKE_VERIFY False ['transparent pixel...', 'movement beyond logical...']`. 라인 99 이후 도달 불가.
- **Test coverage**: 자체 결함.
- **Recommended timing**: FIX BEFORE CONTINUING

---

## 5. False Positives / Overstated Risks

Luna의 감사 항목 중 과장되었거나 잘못 판단된 부분입니다.

1. **C-1: Double-click semantics are unspecified (FALSE POSITIVE)**
   - **Luna 주장**: `mouseDoubleClickEvent`가 오버라이드되지 않아 더블클릭 시 두 번째 클릭이 poke로 인정되지 않고 유실될 위험이 있다.
   - **실제 검증**: PySide6/Qt의 `QOpenGLWidget` 및 `QWidget` 기본 `mouseDoubleClickEvent`는 내부적으로 `mousePressEvent(event)`를 직접 호출하도록 구현되어 있습니다. 실제로 더블클릭 이벤트를 발생시켜 호출 스택을 추적한 결과, `PetCanvas.mousePressEvent` -> `begin_pointer_press`가 정상 호출되고 릴리즈 시 `register_poke`가 호출되어 연속 2회 poke가 정확히 카운트되었습니다. 따라서 더블클릭으로 인한 입력 누락은 발생하지 않습니다.
2. **A-1: Persistent NEGATIVE cleared by sleep/wake (OVERSTATED AS BUG)**
   - **Luna 주장**: A등급의 치명적 버그로 분류하며 "쓰다듬기만이 유일한 해제 경로라는 제품 규칙과 충돌한다"고 기술함.
   - **실제 검증**: 코드상 `include_negative=True`는 Hibana 개발 초기부터 수면 진입 시 캐릭터의 모든 일시적/영구적 표정을 정리하고 자는 얼굴 모션을 재생하기 위해 의도적으로 작성된 코드입니다. 사용자 지침에서도 명시되었듯, 이는 버그라기보다는 "수면 후 깼을 때 화난 상태를 유지할 것인가, 풀릴 것인가"에 대한 기획/UX 정책(Design Decision)입니다.

---

## 6. Test Results

프로젝트의 기존 테스트 및 검증 도구를 원본 소스 변경 없이 실행한 결과입니다.

| 테스트 / 도구 | 명령어 | 결과 | 비고 / 원인 분석 |
|---|---|---|---|
| **타깃 단위 테스트** | `python -m unittest test_model_profiles test_pet_behavior test_prepare_model -v` | **PASS** (23/23 tests, 0.26s) | 모델 프로필 검증, poke 스키마 검증, 비헤이비어 제스처/스케줄러, ZIP 인코딩 전부 정상 통과. |
| **전체 단위 테스트 디스커버리** | `python -m unittest discover -v` | **FAIL** (1 Error, 23 passed) | `research/__init__.py`에서 존재하지 않는 `research._v3cpp` 임포트 시도로 인한 `ModuleNotFoundError`. 펫 런타임과 무관한 리서치 디렉토리 이슈. |
| **통합 펫 검증기** | `python verify_pet.py` | **PASS** (Exit Code 0) | 투명 프레임버퍼, 네이티브 클릭스루, 드래그 클램핑, 위치 복원, 메뉴 등 전체 회귀 정상. |
| **Poke 프로브** | `python probe_poke.py` | **FAIL** (Line 99 AssertionError) | 2개 항목 통과 후 `self.pet()` 단계에서 QTest mouseMove 이벤트 미전달로 실패. 핵심 poke 시퀀스는 도달도 못함. |
| **Petting 프로브** | `python probe_petting.py` | **FAIL** (Line 86 AssertionError) | 위와 동일하게 QTest 무버튼 mouseMove가 이벤트 필터에 전혀 잡히지 않음 (`counts: {}`). |
| **독립 Poke 시퀀스 검증 (Scratch)** | `python scratch/test_poke_progression.py` | **PASS** (100%) | `w.register_poke()` 및 타이머를 통한 1~8회 시퀀스, 타임아웃, 脸黑 고착, 쓰다듬기 POSITIVE 직행 전부 완벽 검증. |
| **B-2 재현 검증 (Scratch)** | `python scratch/test_b2_repro.py` | **REPRODUCED** | 疑惑 상태에서 petting 후 疑惑 표정으로 복원되고 `idle_kind=None`으로 영구 고착되는 버그 100% 재현 확인. |

---

## 7. Recommended Priority

다음 개발 세션(Sol)을 위한 우선순위 분류입니다.

### FIX BEFORE CONTINUING (즉시 수정 필요)
1. **[B-2] Transient Poke 중 Petting 시 Poke 표정 고착 버그 수정**:
   - `desktop_pet.py`의 `start_reaction()`에서 `if self.idle_kind in ('negative', 'poke'): previous_expression = self.idle_previous_expression`으로 처리하고, `cancel_idle_reaction(restore=False, include_negative=True)`를 수행하도록 수정.
2. **[NEW-2] 드래그 중 우클릭 메뉴 시 Sticky Drag 고착 버그 수정**:
   - `PetCanvas.contextMenuEvent` 또는 `PetWindow.open_menu()` 진입 시 `if self.dragging: self.end_drag()`를 명시적으로 호출하여 마우스 grab 및 드래그 상태 해제.
3. **[NEW-4] `probe_poke.py` 검증 경로 정상화**:
   - `probe_poke.py` 라인 99의 실패하는 `self.pet()`(QTest 의존)을 `verify_pet.py`처럼 직접 `w.hover()` 호출 방식으로 대체하거나 poke 전용 시퀀스를 분리하여 1~8회 시퀀스가 끝까지 자동 검증되도록 수정.

### FIX SOON (다음 기능 작업 전 처리)
1. **[B-1] 프로필 전환 시 Cross-Profile Geometry 잔여 방지**:
   - `desktop_pet.py` `load_state()`에서 `if data.get('profile_id') == self.model_profile.id:` 일치 시에만 `saved_bounds`를 적용하도록 1줄 가드 추가.
2. **[NEW-1] 대형 창 크기(>2M 픽셀) 실루엣 갱신 누락 수정**:
   - `refresh_scaled_input_region`에서도 `_silhouette_region`을 스케일링하여 유지하거나, 다운샘플링된 실루엣을 생성하여 대형 모드에서도 poke가 동작하도록 보장.
3. **[A-2] `ProfileRegistry.active_id` 예외 처리 보강**:
   - `model_profiles.py` `active_id`에서 `isinstance(data, dict)` 체크 추가 (`AttributeError` 방지).
4. **[NEW-3] `register_poke`의 이전 표정 취소 순서 방어**:
   - `start_idle_reaction`이 성공했을 때만 이전 표정 상태를 정리하도록 방어 로직 추가.

### DEFER (추후 여유 시 처리)
1. **[A-3] `research/__init__.py` 디스커버리 에러 정리**:
   - `research/`를 unittest 검색 대상에서 제외하거나 `_v3cpp` 더미/조건부 임포트 처리.
2. **[B-5] 중첩 profile behavior 스키마 검증 강화**:
   - `ModelProfile._validate_assets`에 숫자 범위/배열 길이 세부 assert 추가.
3. **[C-2, C-3, C-4] 엣지 케이스 안정화**:
   - 마우스 릴리즈 소실 대비 `leaveEvent`/`focusOutEvent` 보강, 투명 리로케이션 창 클릭스루, 레거시 ZIP 희귀 인코딩.

### DESIGN DECISION (기획/UX 정책 결정 필요)
1. **[A-1] Sleep / Wake 시 Persistent NEGATIVE 유지 여부**:
   - 캐릭터가 삐진(脸黑) 상태에서 잠들었다가 깼을 때, 화가 풀려있어야 하는지(현재 동작), 아니면 여전히 화나 있어야 하는지 제품 정책 결정 필요.
2. **[B-4] 머리카락 틈새 투명 영역의 입력 정책**:
   - 머리 사각형 내부의 투명 픽셀에 대한 우클릭/드래그 허용이 의도된 트레이드오프인지 재확인.

---

## 8. Sol Handoff

다음 Sol 세션에서 구현/수정에 들어갈 때 가장 먼저 확인해야 할 핵심 5가지:

1. **`start_reaction` 표정 복원 순서 (B-2 해결)**:
   - `desktop_pet.py` 556~565라인: `self.idle_kind == 'poke'`일 때도 `previous_expression`을 `self.idle_previous_expression`으로 복원하고 `cancel_idle_reaction(restore=False)`를 호출해야 합니다.
2. **`open_menu` 드래그 클린업 (NEW-2 해결)**:
   - `desktop_pet.py` 864라인 `open_menu()` 진입부에 `if self.dragging: self.end_drag()`를 한 줄 추가하여 우클릭 시 드래그가 굳어버리는 치명적 UX 락업을 방지하십시오.
3. **`probe_poke.py`의 라인 99 블로커 제거 (NEW-4 해결)**:
   - `probe_poke.py` 라인 99의 `self.pet()`이 실패하여 뒤쪽의 1~8회 시퀀스가 통째로 검증되지 못하고 있습니다. `self.pet()`을 `w.hover()` 기반으로 교체하여 probe 전체가 녹색이 되도록 만드십시오.
4. **`load_state`의 `profile_id` 일치 가드 (B-1 해결)**:
   - `desktop_pet.py` 302라인에서 `data.get('profile_id') == self.model_profile.id` 조건을 확인하여 타 모델의 bounds가 재사용되지 않도록 하십시오.
5. **더블클릭(C-1)은 수정하지 말 것**:
   - Qt 기본 메커니즘으로 더블클릭 이벤트가 이미 `mousePressEvent`로 전달되고 있으므로, 불필요한 더블클릭 핸들러 추가나 구조 변경을 하지 마십시오.
