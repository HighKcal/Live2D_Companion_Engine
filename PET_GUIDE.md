# 펫 구현과 검증 상세

무상호작용 반응과 자동 수면은 [IDLE_REACTIONS.md](IDLE_REACTIONS.md)를 참고한다.

## 실행과 저장

`run.cmd`는 콘솔 없이 기본 펫을 띄운다. PowerShell 실행은 `./.venv/Scripts/python.exe -X utf8 app.py`, 일반 실험실은 같은 명령 뒤 `--lab`이다. 최초 모델 준비 방법은 README를 참고한다.

사용자가 마지막으로 드래그해 놓은 위치·캐릭터 높이·자율 이동 여부는 `local/pet-state.json`에 저장된다. 자율 재배치 위치는 저장하지 않는다. 파일은 시작할 때 읽고 변경 후 400ms 지연 저장, 종료 시 즉시 저장한다. 임시 파일을 교체하는 방식으로 저장 중 손상을 줄인다. 프레임마다 파일을 읽거나 저장하지 않는다. 모니터가 바뀌거나 저장 좌표가 화면 밖이면 가장 가까운 현재 모니터의 사용 가능 영역으로 복구한다. `local/`은 Git·소스 배포에서 제외했다.

## 구조와 우선순위

`app.py`의 Canvas를 `desktop_pet.py`의 PetCanvas가 확장한다. 개발용 제어판은 같은 Canvas를 참조하여 추가 모델·GL 컨텍스트를 만들지 않는다. `pet_behavior.py`에는 렌더링과 파일 I/O에 의존하지 않는 이동/왕복 감지 로직이 있다.

자율 이동은 현재 화면에서 0.35~0.45초 fade out하고, opacity 0인 별도 틱에서 작업 표시줄 제외 영역의 먼 위치로 옮긴 뒤 0.45~0.55초 fade in한다. 드래그 시작은 즉시 fade를 취소하고 opacity를 복구한 다음 마우스를 캡처한다. 자세한 내용은 [RELOCATION.md](RELOCATION.md)를 참고한다.

쓰다듬기는 머리 영역 내부에서 일정 거리 이상의 방향 전환이 3.2초 안에 3회 쌓였을 때 인식한다. 순간적인 큰 점프, 오래 멈춤, 머리 밖 이동, 버튼 입력은 연속 동작에서 제외한다. 반응은 0.35초에 걸쳐 시작하고 마지막 유효 이동 후 프로필에서 선택한 3~4초 동안 유지한 뒤 0.65초에 걸쳐 사라진다. 계속 같은 위치에 머무는 것으로 유지 시간이 늘어나지 않는다. 고해상도 이벤트 시계와 2초 cooldown, 드래그 직후 차단을 적용했으며 자세한 수정 원인·설정은 [PETTING_FIX.md](PETTING_FIX.md)를 참고한다.

반응 중 기존 표정 큐를 비우고 매 프레임 기준값에서 계산한 절대 파라미터를 자동 깜빡임·시선 위에 적용한 뒤 물리·메시를 업데이트한다. 반응이 끝나면 이전 표정을 재적용한다. Add 파라미터를 이전 반응값에 더하지 않는다. 수면은 반응과 자율 이동을 취소하며 원래 수면 모션이 얼굴을 제어한다.

## 캐릭터 프로필

`profiles/<id>.json`에 창 비율, 모델 배율, 머리 영역, semantic 반응, 실제 asset/parameter mapping과 재배치/휴식 설정이 있다. 지원하지 않는 optional 파라미터는 시작할 때 필터링하고 실제 런타임 범위 안으로 값을 제한한다. 모델별 실제 ID는 profile에만 기록하며 워터마크는 유지한다.

`head_rect_model`은 `[left, bottom, right, top]` 형식의 모델 공간 사각형이다. 현재 MVP의 역행렬로 마우스를 모델 공간으로 옮기므로 화면 위치와 크기에 맞춰 판정 영역이 변한다. 머리 영역을 바꾸려면 JSON을 조정한 뒤 재실행한다. 기본 머리 주변을 위한 조절 가능한 사각형이며 개발용 제어판에서 얼굴을 극단적으로 변형했을 때까지 따라가는 메시 히트 테스트는 아니다.

## 투명 입력 영역과 성능

Qt/Windows 조합에서 투명하게 렌더링된 모서리도 창 입력 대상으로 남는 것이 실제 검사에서 발견됐다. 따라서 기존 GL 프레임의 알파를 최대 5Hz로 읽고 4 논리 픽셀을 확장한 윤곽을 창 영역으로 적용한다. 완전히 빈 공간은 아래 앱이 받으며 캐릭터와 좁은 가장자리 여유는 펫이 받는다. 이 처리를 위해 추가 모델 업데이트나 framebuffer 재렌더링을 호출하지 않는다.

33ms 타이머로 약 30fps를 요청한다. 모델·프로필·표정 목록·런타임 범위는 메모리에 보관한다. 입력 윤곽은 최대 200ms 간격으로 갱신하므로 빠른 리깅 변경 직후 경계에 짧은 지연이 있을 수 있다. 장시간 FPS와 전력 소비는 아직 측정하지 않았다.

## 검증 재현

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest test_pet_behavior -v
.\.venv\Scripts\python.exe -X utf8 app.py --verify-pet full --state artifacts/pet/test-state.json
.\.venv\Scripts\python.exe -X utf8 app.py --verify-pet restore --state artifacts/pet/test-state.json
.\.venv\Scripts\python.exe -X utf8 app.py --verify
```

첫 통합 검사는 창을 띄워 얼굴 반응과 이동을 검사하고 400px 크기 및 위치를 저장한 뒤 종료한다. 다음 검사는 별도 프로세스에서 저장 상태 복원을 확인한다. 사용자 기본 위치 파일은 건드리지 않는다. 결과는 `artifacts/pet/`에 저장된다.

| 항목 | 확인 수준 |
| --- | --- |
| 투명 표시·캐릭터 잘림 | 실제 Windows 캡처와 GL 알파 검사, 통과 |
| 드래그·우클릭 종료 | computer-use의 실제 마우스 입력, 통과 |
| 빈 영역 클릭 통과/몸통 입력 | 실제 Win32 WindowFromPoint 결과, 통과 |
| 창 표시 시 포커스 유지 | 실제 Win32 foreground HWND 전후 동일, 통과 |
| 왕복·단발·정지·버튼 구분 | 시간 간격을 둔 Qt 콜백과 단위 검사, 통과 |
| 쓰다듬기·기존 표정·물리·수면 | 실제 네이티브 파라미터와 GL 캡처, 통과 |
| 자율 재배치와 사용자 개입 | 실제 fade 양쪽 Qt 클릭 취소, opacity 복구, 5회 연속 재배치 통과 |
| 화면 경계·위치 복원 | 현재 모니터 실제 실행, 재시작 및 가상 모니터 단위 검사, 통과 |
| 실제 사람의 쓰다듬기 감도 | 미확인 |
| 물리적 다중 모니터/혼합 DPI/케이블 분리 | 미확인, 현재 모니터는 150% 단일 화면 |
| 다른 GPU·원격 세션·장시간 안정성 | 미확인 |

동일 오류가 수정 후 3회 반복되어 중단한 항목은 없다. 투명 입력 영역 오류는 첫 발견 후 윤곽 방식으로 수정하여 통과했다.

참고: [Qt QOpenGLWidget 투명 창 설명](https://doc.qt.io/qt-6/qopenglwidget.html), [Microsoft layered window 입력 설명](https://learn.microsoft.com/en-us/windows/win32/winmsg/window-features).
