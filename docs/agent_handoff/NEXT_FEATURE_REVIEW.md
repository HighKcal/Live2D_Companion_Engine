# Next Feature Reconnaissance Independent Review

> **Review Metadata**  
> - **Reviewer**: Antigravity (Independent Code Reviewer / Cross-Verifier)  
> - **Review Type**: Independent source-code verification of `NEXT_FEATURE_RECON.md`  
> - **Target Document**: `docs/agent_handoff/NEXT_FEATURE_RECON.md`  
> - **Authority**: Current project source code (`desktop_pet.py`, `app.py`, `model_profiles.py`, runtime assets, etc.)  
> - **Constraints**: Review only; no feature implementation, no bug fixing, no modifications to source/profiles/tests/assets/Git. Only `docs/agent_handoff/NEXT_FEATURE_REVIEW.md` is created/updated.

---

## 1. Executive Summary

`NEXT_FEATURE_RECON.md`에서 제시된 Luna의 차기 기능(더블클릭 깜짝 놀람, 메뉴 인사 모션, 희귀 쓰다듬기 강한 긍정/MeiYan 모션)에 대한 사전 정찰 결과는 **전반적으로 소스 코드 및 Live2D 런타임 에셋과 높은 일치도**를 보입니다.

특히 다음 핵심 판단들이 실제 코드와 에셋 검증을 통해 확인되었습니다:
1. **더블클릭-Poke 상호작용**: 현재 소스에서는 더블클릭 시 두 번의 릴리즈가 모두 즉시 `register_poke()`로 처리됩니다. 따라서 더블클릭에 "깜짝 놀람(Surprise)"을 매핑할 경우, 첫 번째 클릭이 이미 Poke 카운트 증가 및 표정 반응을 일으키는 시맨틱 충돌(Semantic Collision)이 실재합니다.
2. **인사 및 MeiYan 모션 수명주기**: IceGirl의 `HuiShou.motion3.json`과 `MeiYan.motion3.json` 모두 파일 내부에 `"Loop": true` 메타데이터를 포함하고 있어, 네이티브 SDK의 `IsMotionFinished()`나 `onFinish` 콜백으로는 절대 종료되지 않습니다. 파이썬 단의 명시적 데드라인 타이머와 `StopAllMotions()` 호출이 필수적입니다.
3. **단일 반응 슬롯과 Persistent NEGATIVE 충돌**: 현재 엔진은 단일 `idle_expression`/`idle_kind` 슬롯만 사용하므로, Persistent NEGATIVE(화남/脸黑) 상태에서 일시적 깜짝 놀람이나 인사를 무심코 덮어쓰면 NEGATIVE 상태가 유실되거나 중립으로 조기 복귀하는 불변성 훼손이 발생합니다.

Luna는 불필요한 FSM이나 이벤트 버스 도입을 지양하고, 기존 단일 슬롯 구조를 해치지 않는 **최소한의 가드 및 프로필 주도형 확장(Minimal Profile-Driven Extensions)**을 권고하고 있어 방향성 또한 타당합니다.

---

## 2. Verification of Luna's Three Main Risks

### 2.1 Double-Click vs. Poke Interaction

- **Luna의 주장**:  
  "Both releases from a double-click are immediately counted by the existing poke system."  
  더블클릭의 두 번째 프레스가 Qt에서 유실된다는 이전 우려는 오판(False Positive)이었으나, 현 코드에서는 더블클릭 시 두 번의 릴리즈가 모두 즉시 Poke 카운트를 올리므로, 차기 더블클릭 깜짝 반응 추가 시 시맨틱 충돌이 발생한다.
- **Antigravity 검증 결과: CONFIRMED**
  - `desktop_pet.py`에는 `mouseDoubleClickEvent` 오버라이드가 없으므로, Qt 기본 위젯 로직에 의해 `MouseButtonDblClick` 이벤트가 `mousePressEvent`로 전달됩니다.
  - 이벤트 시퀀스:
    1. Click 1 Press -> `begin_pointer_press()` (`pointer_pressed = True`)
    2. Click 1 Release -> `end_pointer_release()` -> `register_poke()` (**Annoyance 카운트 즉시 1 증가**)
    3. Click 2 DblClick -> `mousePressEvent()` 포워딩 -> `begin_pointer_press()` (`pointer_pressed = True`)
    4. Click 2 Release -> `end_pointer_release()` -> `register_poke()` (**Annoyance 카운트 즉시 1 증가, 임계값 도달 시 표정 변경**)
  - 따라서 더블클릭을 수행하면 **순식간에 2회의 Poke가 등록**됩니다.
- **차기 기능 충돌 위험**:
  - 만약 더블클릭 시점에 "깜짝 놀람(Surprise)"을 발동시키고자 할 때, 첫 번째 릴리즈에서 이미 카운트가 올라가고 심지어 2/4/6회 임계값인 경우 `疑惑` 등의 표정이 이미 시작되어 버립니다.
  - 뒤이어 두 번째 클릭에서 깜짝 놀람을 띄우려면 이미 발생한 Poke 반응과 카운트를 롤백해야 하거나, 깜짝 놀람과 Poke가 뒤섞이게 됩니다.
  - **결론**: Luna가 제안한 **지연 보류(Pending single-click candidate)** 방식이나 **명확한 상호 배타성 정책**이 반드시 필요합니다.

### 2.2 Greeting / MeiYan Motion Lifecycle

- **Luna의 주장**:  
  IceGirl의 `HuiShou`와 `MeiYan` 모션은 모두 `Loop=True`이므로 `IsMotionFinished()`로 자동 종료되지 않으며, Live2D 래퍼는 전역적인 `StopAllMotions()`만 제공하므로 모션 소유권의 상호 배타성과 데드라인 타이머 관리가 필수적이다.
- **Antigravity 검증 결과: CONFIRMED**
  - 실제 에셋 파일 확인:
    - `models/a3e8788671ce/runtime/motion_01.motion3.json` (`HuiShou`): `"Meta": { "Duration": 7, "Loop": true }`
    - `models/a3e8788671ce/runtime/motion_02.motion3.json` (`MeiYan`): `"Meta": { "Duration": 3.983, "Loop": true }`
  - `research/PyLAppModel.cpp` 소스 확인:
    - 모션 중지 API는 오직 `StopAllMotions()` 및 `ClearMotions()`만 바인딩되어 있으며, 특정 모션 그룹이나 인덱스만 선택적으로 중지하는 API가 존재하지 않습니다.
  - **위험 평가**:
    - 만약 수면(`sleep`) 중에 인사가 실행되거나, 반대로 인사를 종료하려고 `StopAllMotions()`를 호출할 때 수면 모션이 돌고 있다면 수면이 강제 중단됩니다.
    - 또한 루프 모션이므로 파이썬 단에서 `QTimer.singleShot` 등의 데드라인 없이 방치하면 영원히 손을 흔들거나 윙크를 반복하게 됩니다.
  - **최소 위험 모델**: Luna의 진단은 과장이 아니며 정확합니다. 단일 모션 소유권(Single Motion Owner)과 명시적 duration 데드라인 관리가 필요합니다.

### 2.3 Reaction Lifecycle and Persistent NEGATIVE

- **Luna의 주장**:  
  "A single reaction slot can conflict with the persistent NEGATIVE restoration invariant."  
  단일 반응 슬롯 구조에서 깜짝 놀람이나 인사 모션이 실행되면 Persistent NEGATIVE(얼굴 찌푸림/脸黑) 상태를 덮어쓰거나 완료 후 중립으로 오복귀시킬 위험이 있다.
- **Antigravity 검증 결과: CONFIRMED**
  - `desktop_pet.py`의 상태 변수 확인:
    - `self.idle_expression`, `self.idle_kind`, `self.idle_previous_expression`, `self.idle_until`이 단 1벌만 존재합니다.
    - Persistent NEGATIVE 상태는 `idle_kind == 'negative'`, `idle_until == inf`로 유지됩니다.
    - `cancel_idle_reaction()`은 기본적으로 `idle_kind == 'negative'`일 때 취소를 거부하여 이 상태를 보호합니다.
  - **구체적 파괴 시나리오**:
    1. 캐릭터가 8회 Poke로 Persistent NEGATIVE에 도달한 상태.
    2. 사용자가 더블클릭을 하여 `start_idle_reaction(..., kind='surprise')`가 호출됨.
    3. `self.idle_previous_expression`에 현재 표정인 `脸黑`가 들어가고, `self.idle_kind`가 `'surprise'`로 변경됨 -> **Persistent NEGATIVE 상태 유실!**
    4. 1.5초 후 깜짝 놀람이 만료되어 `cancel_idle_reaction()`이 호출되면, `self.idle_kind`는 `None`이 되고 표정은 중립으로 풀리거나 영구 표정 관리에서 이탈함.
  - **결론**: "성공적인 쓰다듬기(Petting)만이 Persistent NEGATIVE를 해제할 수 있다"는 제품 불변성을 유지하려면, **Persistent NEGATIVE 상태에서는 더블클릭 깜짝 놀람을 억제(Suppress)**하거나 별도의 스택 없이 안전하게 무시하는 정책이 절대적으로 요구됩니다.

---

## 3. Corrections to NEXT_FEATURE_RECON.md

문서 전체적으로 기술적 오류는 거의 없으나, 구현 시 혼선을 방지하기 위해 다음 사항들을 명확히 정정/보완합니다:

1. **더블클릭 시 보류 지연(Pending Delay)의 UX 체감 (보완)**:
   - Luna는 1회 클릭 시 즉시 `register_poke()`를 부르지 않고 더블클릭 간격(Qt 기본 약 400ms) 동안 대기하는 방식을 제안했습니다.
   - **주의점**: 이 방식은 클릭 후 반응이 400ms 지연되므로 단발성 쿡 찌르기(Poke)의 즉각적인 타격감/반응성을 저하시킬 수 있습니다.
   - **대안 고려**: 1회 클릭 시 카운트 증가 및 표정을 즉시 띄우되, 더블클릭 발생 시 카운트는 유지하면서 표정만 일시적으로 `surprise`로 덮어쓰고 복귀하는 정책이 UX상 더 경쾌할 수 있습니다. 이는 구현 시 프로토타입 단계에서 검증되어야 합니다.
2. **`apply_behavior()`와 모션 파라미터 우선순위 (정밀화)**:
   - Luna는 `apply_behavior()`가 `model.Update()` 전에 실행되므로 모션이 중복 파라미터를 덮어쓴다고 설명했습니다.
   - 코드 확인 결과 정확하며, IceGirl의 `MeiYan` 모션은 `ParamAngleZ`, `Param60`, `Param58`, `Param59` 등을 건드립니다. 쓰다듬기 강한 긍정의 볼홍조(`Param31`) 및 하트눈(`Param37`)은 `MeiYan` 모션에 포함되어 있지 않으므로 모션 재생 중에도 볼홍조/하트눈이 완벽히 공존합니다.

---

## 4. Newly Discovered Risks

기존 정찰 문서에서 다소 간과되었거나 추가로 주의해야 할 리스크입니다:

1. **[NEW-RISK-1] 모션 재생 중 자율 이동(Relocation) 및 대기 반응(Idle) 방어 누락 위험**:
   - 현재 `desktop_pet.py`의 `tick()`에서 `idle_blocked` 및 relocation `blocked` 조건(라인 706, 723)은 `self.canvas.sleeping`, `self.detector.active`, `self.reaction_level > 0`, `self.idle_expression is not None` 등만 검사합니다.
   - 만약 인사하기(`greeting`) 모션을 순수 모션으로만 실행하고 `idle_expression`을 세팅하지 않는다면, **인사 손을 흔드는 도중에(7초간) 펫이 화면 다른 곳으로 페이드아웃하며 텔레포트(Relocation)**해버리는 기괴한 현상이 발생할 수 있습니다.
   - **대책**: 활성 사용자 모션 플래그(예: `self.motion_active` 또는 `self.active_motion_deadline > now`)를 `blocked` 조건에 반드시 포함해야 합니다.
2. **[NEW-RISK-2] 컨텍스트 메뉴 내 모션 트리거의 `menu_active` 타이밍 락업**:
   - `open_menu()`는 메뉴가 열려 있는 동안 `self.menu_active = True`를 유지합니다.
   - `build_menu()`의 액션 `triggered` 시그널은 `menu.exec()`가 완전히 반환되기 직전에 동기 호출될 수 있습니다. 만약 인사 액션 핸들러 내부에서 `self.menu_active`를 검사하면 실행이 거부될 수 있습니다.
   - **대책**: Luna가 언급한 대로 `QTimer.singleShot(0, self.start_greeting)`을 사용하여 메뉴 이벤트 루프가 완전히 종료되고 `menu_active = False`가 된 직후 모션이 시작되도록 스케줄링해야 합니다.

---

## 5. Final Recommended Implementation Order

선행 필수 버그 수정(B-2, NEW-2)이 완료되었다는 전제 하에 권장하는 작업 순서입니다:

```
[Phase 1: 기반 인프라]
  1. Profile 검증 스키마 확장 (optional 'motions' 및 candidate 'motion' 필드)
  2. Canvas / PetWindow에 단일 소유자 기반 모션 시작/데드라인 중지 헬퍼 구현
  3. tick()의 blocked 조건에 motion_active 연동 (자율이동/아이들 방지)

[Phase 2: 인사 모션 (Greeting)]
  4. 우클릭 메뉴에 '인사하기' 액션 추가 (capability 기반 활성화/비활성화)
  5. singleShot(0) 기반 메뉴 탈출 후 7초 데드라인 HuiShou 모션 재생 및 StopAllMotions 연동
  6. Persistent NEGATIVE 상태 보존 검증

[Phase 3: 더블클릭 깜짝 놀람 (Surprise)]
  7. 더블클릭 시맨틱 중재 로직 구현 (단발 poke와의 카운트/표정 충돌 방지)
  8. 惊讶 표정 일시 표시 후 이전 표정 복구
  9. Persistent NEGATIVE 중 더블클릭 억제(Suppress) 정책 적용

[Phase 4: 쓰다듬기 강한 긍정 (MeiYan)]
  10. positive candidates 목록에 가중치 기반 희귀 MeiYan 모션 후보 추가
  11. 쓰다듬기 성공 시 MeiYan 모션 동시 시작 및 파라미터 레이어(볼홍조/하트눈) 블렌딩 검증
  12. NEGATIVE -> Strong POSITIVE 직접 전이 및 종료 후 baseline 복원 불변성 검증
```

---

## 6. What Codex Should Trust from Luna

다음 내용들은 소스 코드 및 런타임 에셋과 100% 일치하므로 Codex/Sol이 신뢰하고 구현에 착수해도 좋습니다:

1. **`HuiShou` 및 `MeiYan`의 `Loop=True` 사실**: SDK의 `IsMotionFinished()`를 기다리면 안 되며, 반드시 시간 기반 데드라인(각 7.0초, 3.98초 등)으로 `StopAllMotions()`를 호출해야 합니다.
2. **`StopAllMotions()`의 전역성**: 모션별 개별 중지 API가 없으므로, 사용자 모션끼리 동시 실행을 금지하고 단일 모션 소유권 모델을 취해야 합니다.
3. **공통 엔진 무하드코딩 원칙**: `icegirl`, `HuiShou`, `MeiYan`, `惊讶` 등의 문자열은 엔진에 들어가선 안 되며, `profiles/icegirl.json`의 시맨틱 매핑(`motions.greeting`, `expressions.surprise`)을 통해서만 소비되어야 합니다.
4. **Persistent NEGATIVE 보호 원칙**: 더블클릭이나 인사 모션이 Persistent NEGATIVE를 neutral로 풀어서는 안 되며, 오직 쓰다듬기만이 해제할 수 있어야 합니다.

---

## 7. What Codex Should Ignore or Re-check

다음 내용들은 무비판적으로 수용하지 말고 구현 시 재확인하거나 재검토해야 합니다:

1. **"더블클릭 프레스 유실" 우려**: 이미 검증 완료된 False Positive입니다. `mouseDoubleClickEvent`에서 `mousePressEvent`를 수동 재호출하는 불필요한 코드를 작성하지 마십시오.
2. **"단일 클릭 400ms 무조건 지연" 방안의 맹신**: Luna의 보류 지연(Pending) 설계는 시맨틱상 안전하지만 단발 poke 클릭의 반응성을 해칠 수 있습니다. 단발 poke는 즉시 반응하고 더블클릭 시 깜짝 표정으로 덮어쓰는 대체 설계의 타당성을 사용자 경험 관점에서 재검토하십시오.
3. **FSM / 복잡한 상태 머신 도입 시도**: Luna가 올바르게 경고했듯, 대규모 상태 머신이나 이벤트 버스는 과도한 오버엔지니어링입니다. 기존 `idle_expression`과 타이머 구조 내에서 최소한의 플래그로 구현해야 합니다.

---

## 8. Files Modified During This Task

본 감사 및 검증 작업 중 생성/수정된 파일은 오직 다음 1개입니다:

- `docs/agent_handoff/NEXT_FEATURE_REVIEW.md` (새로 생성됨)

*프로젝트 소스 코드(`*.py`), 프로필(`*.json`), 테스트, 프로브, 모델 에셋, 런타임 에셋, Git 설정 등은 일체 수정되지 않았습니다.*
