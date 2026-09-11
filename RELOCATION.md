# 페이드 자율 재배치

기존 자율 이동은 `Wander`가 20~60초 휴식 뒤 30~100 논리 픽셀 목적지를 정하고 4~7초 동안 창 좌표를 smoothstep 보간했다. 캐릭터가 마우스로 끌려가듯 보였고, 완료 시 현재 위치를 저장해 사용자가 직접 놓은 시작 위치도 덮어썼다.

현재 `Relocator`는 같은 20~60초 휴식 주기를 사용하며 `RESTING → FADING_OUT → RELOCATING → FADING_IN → RESTING` 순서만 수행한다. 창 좌표 보간과 보행용 몸 흔들림은 제거했다.

## 동작과 설정

현재 `profiles/<id>.json`의 `behavior.relocation`에서 다음 값을 조절한다.

| 설정 | 기본값 |
| --- | --- |
| fade out | 0.35~0.45초 |
| fade in | 0.45~0.55초 |
| 최소 거리 기준 | 캐릭터 최대 변의 1.0배와 화면 이동 가능 대각선의 18% 중 큰 값 |
| 목적지 후보 수 | 24개 |

현재 펫 중심이 속한 모니터의 `availableGeometry()`를 사용해 창 전체가 들어가는 좌상단 범위를 계산한다. 24개 후보 중 최소 거리 조건을 만족하는 위치를 고르고, 작은 화면처럼 조건을 만족하기 어려우면 가장 먼 후보로 완화한다. 좌표는 Qt 논리 픽셀이므로 기존 DPI·다중 모니터 계산을 그대로 따른다.

최상위 투명 창의 `windowOpacity`를 smoothstep으로 바꾼다. fade out이 끝난 틱에는 opacity 0인 채 기존 위치를 유지하고, 다음 별도 `RELOCATING` 틱에서만 좌표를 한 번 변경한다. 새 위치에서도 opacity 0으로 시작한 뒤 fade in한다. 모델·텍스처·OpenGL 렌더러는 변경하지 않는다.

## 충돌과 취소

드래그, 쓰다듬기 후보/반응, 수면, ambient/negative 표정, 메뉴, 개발 패널, 자율 이동 OFF 상태에서는 시작하지 않는다. 마우스가 현재 창의 실제 마스크 입력 영역 위에 있어도 다음 휴식 구간으로 연기한다.

fade 중 마우스 이동이나 클릭 등 사용자 입력이 들어오면 `cancel_relocation()`이 상태를 `RESTING`으로 만들고 `windowOpacity(1.0)`을 즉시 복구한다. fade out 중이면 기존 위치, 이미 옮긴 뒤면 새 위치를 유지한다. 드래그와 쓰다듬기는 이어서 기존 입력 경로로 처리된다.

## 위치 저장

`manual_position`은 시작 시 저장 파일에서 읽고 사용자가 드래그를 끝낼 때만 현재 좌표로 갱신한다. 크기 변경 시에는 같은 바닥 위치를 기준으로 수동 좌표만 안전 영역에 맞춘다. 자율 재배치 좌표는 현재 세션의 창에만 적용하며 `save_state()`와 종료 시에는 `manual_position`을 기록한다.

## 검증

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest test_pet_behavior -v
.\.venv\Scripts\python.exe -X utf8 probe_relocation.py
.\.venv\Scripts\python.exe -X utf8 app.py --verify-pet full --state artifacts/relocation/regression-state.json
.\.venv\Scripts\python.exe -X utf8 app.py --verify-pet restore --state artifacts/relocation/regression-state.json
```

실제 Windows/PySide6/OpenGL 창에서 5회 연속 재배치를 검사했다. 모든 위치 기록은 기존 위치와 새 위치 두 개뿐이었고, 이동은 opacity 0 상태에서만 발생했다. 측정된 fade out은 0.365~0.404초, fade in은 0.457~0.535초, 이동 거리는 433~1085px였다. OFF·실제 hit hover·메뉴·negative·sleep 차단, fade 양쪽의 Qt 클릭 취소, opacity 1 복구, 수동 위치 파일 보존도 통과했다.

화면 캡처에서 fade 중 투명 배경이 유지됐고 opacity 0인 이전/새 위치에 검은 사각형이나 잔상이 보이지 않았다. 한 대의 150% DPI 모니터에서 확인했으며 물리적 다중 모니터 간 DPI 전환과 사람이 느끼는 fade 속도는 별도 확인이 필요하다.
