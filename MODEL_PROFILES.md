# Live2D 모델 프로필과 신규 모델 추가 절차

공통 엔진은 ambient, negative, positive, sleep, petting, head 같은 의미만 사용한다. 실제 expression 파일, motion 파일, parameter ID와 화면 비율은 profiles/<id>.json에서 연결한다. 새 모델을 추가할 때 app.py, desktop_pet.py, pet_behavior.py를 수정하는 방식은 정상 절차가 아니다.

## 프로필 구조

- id, display_name: 저장과 메뉴에 사용하는 안정적인 ID와 표시 이름
- model.path: 프로젝트 루트 기준으로 준비된 model.model3.json 경로
- presentation: 모델 배율, 창 종횡비, 캐릭터 높이 대비 창 높이
- presentation.render_window_scale: 캐릭터 크기는 유지하면서 데스크톱 투명 창만 확대하는 배율. 현재 세 프로필은 2.0이다.
- presentation.bottom_offscreen_fraction: 실루엣 높이 중 화면 아래로 허용할 비율. 생략 시 0.65다.
- hit_areas.head: 모델 좌표의 쓰다듬기 영역. 신뢰할 수 있는 HitArea가 없다면 사람이 화면을 확인해 조정한다.
- parameters: eye_open_left, head_angle_z, heart_blush 같은 공통 의미를 실제 parameter ID에 연결
- controls: 개발 패널에 표시할 semantic parameter와 라벨
- expressions: ambient/negative/positive 후보. asset은 runtime 순번이 아니라 원본 기준 상대 경로다.
- motions.sleep: 수면에 사용할 원본 기준 motion 상대 경로
- behavior: 이동, idle, petting 감도·시간 설정

optional parameter가 모델에 없으면 긍정 반응에서 해당 값만 생략한다. sleep motion이 없으면 메뉴의 수면 항목이 비활성화되고 major idle의 sleep 후보가 제거된다. negative expression이 없으면 negative 후보도 제거된다. 두 major 후보가 모두 없으면 major idle은 아무 행동도 예약하지 않는다. 알 수 없는 asset을 참조하거나 필수 필드가 잘못된 프로필은 discovery에서 제외되고 오류가 보고된다.

UTF-8 filename flag가 없는 legacy ZIP은 기본 CP437 해석에서 상자문자 형태로 깨지는 경우에만 GBK/CP932 후보를 비교한다. 특정 모델명이나 ZIP hash를 사용하지 않으며 UTF-8·ASCII ZIP은 재해석하지 않는다.

현재 실제 프로필:

- `hibana`: 火花. ambient/negative/positive와 전용 sleep motion 지원.
- `tsubaki`: 椿. ambient/negative/positive 지원, sleep 미지원. 눈물 반복 효과 motion은 sleep으로 사용하지 않음.
- `icegirl`: IceGirl. ambient/negative/positive 지원, sleep 미지원. 20개 표정과 3개 motion은 inventory하되 새 행동으로 연결하지 않음.

## 새 모델 onboarding

1. 원본 ZIP을 별도 보관하고 수정하거나 파일명을 변경하지 않는다.
2. `prepare_model.py <zip>`으로 models/<sha>/original과 runtime을 만든다. original은 원본 보존용이며 runtime만 ASCII 파일명 사본을 사용한다. 4096을 넘는 텍스처는 공통 정책으로 runtime 사본만 비율과 alpha를 유지해 축소한다.
3. 다음 명령으로 구조적 inventory를 확인한다: .venv\Scripts\python.exe inspect_model.py models\<sha>\runtime\model.model3.json
4. model3, assets.json, expression/motion JSON, 표시 정보와 실제 렌더링을 확인한다. inventory가 알려 주는 파일·parameter·HitArea 존재 여부와 사람이 판단해야 하는 표정 의미를 구분한다.
5. expression을 직접 화면에서 확인해 ambient, negative, positive 후보를 정한다. 파일명만으로 의미를 확정하지 않는다.
6. 수면에 적합한 motion과 반복 여부를 확인한다. 없다면 motions.sleep을 만들지 않는다.
7. 실제 parameter ID와 범위를 확인하고 필요한 semantic parameter만 parameters에 연결한다. 없는 ID를 만들어 넣지 않는다.
8. profiles/<id>.json을 추가한다. profiles/hibana.json을 스키마 예시로 사용할 수 있지만 asset과 parameter를 그대로 복사하지 않는다.
9. python -m unittest -v test_model_profiles.py test_pet_behavior.py로 parsing과 공통 동작을 검사한다.
10. app.py --profile <id> --lab에서 표정·모션·파라미터를 확인한 뒤 펫 모드에서 투명창, 크기, 드래그, 쓰다듬기, 수면을 사람이 확인한다.

profiles에 valid JSON을 추가하면 다음 실행부터 우클릭 캐릭터 메뉴에 자동 표시된다. 캐릭터 선택은 active profile을 local/app-state.json에 저장한 뒤 애플리케이션을 재시작한다. Live2D renderer와 OpenGL context를 실행 중 교체하지 않아 기존 모델 정리 순서와 안정성을 유지한다.

엔진 변경이 필요한 경우는 새 모델이 현재 semantic capability로 표현할 수 없는 실제 런타임 동작을 요구할 때뿐이다. 그 경우 모델의 특수 파일명이나 parameter ID를 Python에 직접 넣기 전에 재사용 가능한 semantic capability인지 먼저 판단한다.
