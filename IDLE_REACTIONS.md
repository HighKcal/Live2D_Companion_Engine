# 평상시 표정·major idle 반응

`PetWindow`는 기존 드래그, 쓰다듬기, 자율 재배치, 수면 구조 위에서 ambient와 major idle 반응을 별도로 예약한다. 우선순위는 사용자 드래그/쓰다듬기, 수면, major negative, ambient, 평상/자율 재배치 순서다. 재배치는 표정 반응 중 시작되지 않는다.

## 마지막 상호작용

드래그 시작, 성공한 쓰다듬기, 자율 이동 설정·크기·수면 상태 변경, 개발 제어판 열기, 트레이의 화면 복구가 마지막 상호작용 시각을 갱신한다. 단순 hover, 쓰다듬기 후보 진입, ambient 표정은 갱신하지 않는다. 의미 있는 상호작용 뒤에는 major idle 시점을 새로 60~90초 뒤에 예약한다.

negative 상태에서는 hover와 진행 중인 쓰다듬기 후보가 표정을 해제하지 않는다. 쓰다듬기 성공 순간에만 negative 상태를 복원 없이 분리하고 positive 절대 파라미터 레이어를 시작한다. 이때 기본 expression을 선택하는 중간 호출이 없으며, positive 종료 시 negative 이전의 expression으로 복귀한다. 표정 파라미터는 전환 시 기본값으로 정리하므로 `Add` 값도 남지 않는다.

## 모델별 기본값

모든 값은 현재 캐릭터의 `profiles/<id>.json` 안 `behavior.idle`에 있다.

| 종류 | 설정 | 기본값 |
| --- | --- | --- |
| ambient | 검사 간격 / 확률 | 10~25초 / 0.65 |
| ambient | 유지 시간 | 8~12초 |
| ambient | 표정 | 원본 `Expressions/05 ＞＜.exp3.json` 가중치 3, `Expressions/07 星星眼.exp3.json` 가중치 1 |
| major idle | 무상호작용 예약 | 60~90초 |
| major idle | 행동 | negative 1, sleep 1 (각 50%) |
| negative | 유지 시간 | 쓰다듬기 성공 전까지 지속 |
| negative | 표정 | 원본 `Expressions/01黑脸.exp3.json` 가중치 3, `Expressions/03 生气.exp3.json` 가중치 1 |
| positive | 반응 | 눈 감김·눈웃음·`key2` 홍조/하트·작은 고개 기울임 |
| positive | 유지/복귀 | 3~4초 유지, 0.65초 페이드 복귀 |

ambient가 끝난 시각을 기준으로 다음 10~25초 간격을 예약하므로 종료 직후 다른 ambient가 이어지지 않는다. 후보가 둘 이상이면 같은 ambient asset을 연속 선택하지 않는다. 전체 표정 캡처와 실제 파라미터를 확인해 ambient에는 밝은 표정만 사용했다. negative는 어두운 삐짐을 강한 화남보다 3배 자주 선택한다.

major idle 시점에는 `major.actions`의 가중치로 negative 또는 sleep 하나만 선택한다. 별도의 180초 deterministic 수면 타이머는 없다. negative는 쓰다듬기 성공 전까지 유지되고 다른 major idle과 자율 이동을 막는다. sleep은 profile이 연결한 원본 수면 motion을 사용하며 사용자가 깨울 때까지 유지한다. 지원되지 않는 negative나 sleep capability는 시작 전에 후보군에서 제외된다.

## 상태 충돌과 깨우기

- major negative가 ambient 도중 도래하면 ambient를 정리하고 negative를 적용한다.
- negative와 positive 동안 ambient는 시작되지 않는다.
- 드래그는 ambient와 major 예약을 갱신하지만 persistent negative를 해제하지 않는다. 성공한 쓰다듬기만 negative를 positive로 전환하고 major idle 타이머를 처음부터 센다.
- ambient, negative, 쓰다듬기 반응, 메뉴, 개발 제어판, 수면 중에는 자율 이동하지 않는다.
- 수면 중에는 ambient, negative, 쓰다듬기, 자율 이동이 모두 차단된다.
- 메뉴의 `수면 해제`와 잠든 캐릭터 왼쪽 클릭은 같은 `wake()` 경로를 사용한다. 이 경로가 profile 수면 motion 중지, 모든 수면 플래그 해제, 모델 파라미터 초기화, 자동 눈 깜빡임·시선·물리 복귀, interaction/major 예약 초기화를 수행한다.

## 검증

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest test_pet_behavior -v
.\.venv\Scripts\python.exe -u -X utf8 probe_idle.py
.\.venv\Scripts\python.exe -u -X utf8 probe_petting.py
.\.venv\Scripts\python.exe -u -X utf8 app.py --verify-pet full --state artifacts/major-idle/regression-state.json
.\.venv\Scripts\python.exe -u -X utf8 app.py --verify-pet restore --state artifacts/major-idle/regression-state.json
```

`probe_idle.py`는 profile의 semantic asset mapping을 사용하고 예약 시각과 행동 가중치만 주입해 negative와 sleep 경로를 각각 확정적으로 실행한다. 실제 Live2D/OpenGL 상태에서 ambient 유지·완료 후 간격, 비연속 선택, persistent negative, hover/후보 중 negative 유지, 성공 시 중간 기본 expression 호출 없는 positive 전환, positive 복귀, 자동 수면 메뉴 깨우기, 수동 수면 왼쪽 클릭 깨우기 등 12개 항목이 통과했다.

쓰다듬기 전용 실제 이벤트 검사 7개, 전체 펫 회귀 30단계, 새 프로세스 위치·크기 복원, profile 및 동작 단위 검사 16개도 통과했다. 사람이 직접 판단해야 하는 부분은 persistent negative의 장시간 체감, negative에서 positive로 넘어가는 장면의 자연스러움, 실제 손의 쓰다듬기 감도, 60~90초 방치 흐름이다.
