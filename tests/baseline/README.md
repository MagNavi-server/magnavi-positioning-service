# 기존 예측 결과 비교 기준

작성·검증일: 2026-10-15 (한국 시간)

현재 `services/model_service.py`의 계산을 바꾸기 전에, 같은 입력을 다시 넣었을 때
위치 코드·후보 순서·점수가 유지되는지 확인하기 위한 **로컬 회귀 기준**이다.
회귀 검증은 코드 변경으로 기존 동작이 의도치 않게 달라졌는지 확인하는 작업이다.
실제 위치 정답이 없는 합성 입력이므로 위치 정확도나 부하 성능의 기준은 아니다.

## 1. 확보한 자료

| 파일 | 역할 |
|---|---|
| [synthetic_inputs.json](synthetic_inputs.json) | 고정 합성 입력 3종, 각각 48개씩 총 144개 샘플 |
| [reference.json](reference.json) | 실제 실행으로 얻은 모든 샘플의 결과·모델 해시·실행 환경·경고 |
| [prediction_baseline.py](prediction_baseline.py) | 기존 추론 함수 호출, 기준 생성과 비교 |
| [test_prediction_baseline.py](test_prediction_baseline.py) | 재현·변경 감지·기준 파일 보존 검증 9개 |
| [requirements.txt](requirements.txt) | 기존 의존성에서 가져온 검증용 버전 19개 |

기준을 처음 확보할 때 모델 6개와 기존 추론 코드, 최상위 requirements.txt는 변경하지 않았다.
기준 코드의 Git HEAD는 `d82ac754b69496f6921e9954b0ea1d1dd373a0d0`이며, 실제 읽은
소스의 SHA-256도 기준 파일에 남겼다. 이번 PR은 기준 확보 단계만 포함한다. 후속 세션 분리·모델 준비·gRPC 작업은 별도 PR로 게시한다.
SHA-256은 파일의 내용이 바뀌었는지 식별하는 값이다.

## 2. 입력과 실행 방법

- `constant`: 자기장·방향이 일정한 입력으로 초기 채움과 이후 동작을 확인한다.
- `smooth_variation`: 여섯 값이 연속으로 변하는 입력으로 전처리와 과거 샘플의 영향을 확인한다.
- `step_changes`: 15·16·17번째와 이후 구간에서 부호·크기가 바뀌는 입력을 사용한다.
- 필드 순서는 Mag_X, Mag_Y, Mag_Z, Ori_X, Ori_Y, Ori_Z다.
- 각 사례는 빈 버퍼에서 시작해 48개 샘플을 순서대로 처리한다.
- 입력은 고정된 JSON이며 실행 때 난수를 생성하지 않는다. 실제 사용자 데이터는 포함하지 않는다.
- 숫자는 회귀 검증용이다. 실제 앱 센서의 단위·좌표축·정상 범위·측정 주파수를 정의하지 않는다.

현재 모델 묶음에서 읽은 설정은 `window_size=16`, `warmup_policy=pad`,
`wavelet=db4`, `level=1`, `strategy=adaptive`, `border_mode=symmetric`,
`thresh_mode=soft`, `hop_size=1`, `min_fill_ratio=0.5`다.
현재 코드는 hop_size로 호출 간격을 제어하지 않으며 고정 sigma 파일은 로딩하지 않는다.

`pad`는 초기 샘플이 부족하면 값을 채워 계산한다. 따라서 이번 기준은 첫 샘플부터
예측 결과가 있으며, `wait` 정책의 미정의 response/status 오류는 실행하지 않았다.
1·15·16·17번째 샘플의 실제 버퍼 길이는 각각 1·15·16·16이었다.
`wait` 등 다른 정책을 임의로 적용해 현재 설정의 기준과 섞지 않았다.

## 3. 실행 환경과 명령

기준 환경은 **CPython 3.12.14 / macOS arm64**이며, NumPy 2.3.0, SciPy 1.15.3,
scikit-learn 1.7.0, PyWavelets 1.9.0, joblib 1.5.1, Pydantic 2.11.7을 사용했다.
전체 검증 의존성은 이 디렉터리의 requirements.txt와 reference.json에 기록했다.
사용자 확인에 따라 현재 저장소에 선언된 버전으로 구성했으며, 과거 실제 운영 환경은 미확인이다.

프로젝트 루트의 `.venv`에 검증 환경을 구성했다. 서버 실행용 전체 의존성은 설치하지 않았다.
`.venv`는 기존 .gitignore 규칙으로 Git에서 제외된다.

기존 환경에서 다시 검증하려면 프로젝트 루트에서 실행한다.

```bash
.venv/bin/python tests/baseline/prediction_baseline.py verify
.venv/bin/python -m unittest discover -s tests/baseline -p 'test_*.py' -v
.venv/bin/python -m pip check
```

환경을 새로 구성할 경우 CPython 3.12.14를 준비하고 다음 순서로 실행한다.
이미 다른 용도로 사용하는 `.venv`가 있다면 먼저 해당 환경을 보존해야 한다.

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r tests/baseline/requirements.txt
.venv/bin/python tests/baseline/prediction_baseline.py verify
```

Python 패치 버전·운영체제·CPU 아키텍처·검증 라이브러리 버전이 기준과 다르면
비교 전에 실패한다. Linux 운영 환경과 학습 환경의 검증은 별도로 수행해야 한다.
실행 환경 차이를 무시하려고 기준 파일을 수동 수정하지 않는다.

기준 생성 명령은 기존 파일을 덮어쓰지 않는다. 아래 명령은 이미 생성했으므로
현재 다시 실행하면 의도대로 실패한다.

```bash
.venv/bin/python tests/baseline/prediction_baseline.py capture --output tests/baseline/reference.json
```

새 기준이 필요한 변경은 이유를 검토한 뒤 별도 출력 경로에 생성하고 기존 기준과 비교한다.
`verify`는 기준 파일을 읽기만 하며 자동 갱신하지 않는다.

## 4. 비교 범위

- 모델 6개의 해시와 고정 입력 파일의 해시가 같아야 한다.
- 실제 전처리 설정, 사례 ID와 개수, 샘플 개수, 위치 코드, 후보 순서와 개수,
  로그용 후보, 버퍼 길이를 비교한다.
- 위치 코드와 구조는 정확히 일치해야 한다. 실수에는 상대 오차 `1e-9`, 절대 오차 `1e-12`를 적용한다.
  이는 작은 계산 반올림 차이만 허용하는 기준이며 실제 위치 정확도의 허용 오차가 아니다.
- 소스 해시는 생성 시점 추적용이다. 후속 리팩터링으로 소스가 바뀌었다는 이유만으로 실패하지 않는다.
- 함수 위치나 세션 구조가 바뀌면 실행 도구의 `run_cases`를 새 진입점에 연결한다.
  비교를 통과시키기 위해 reference.json의 예상 결과를 다시 만들지 않는다.
- 경고는 요약·기록하지만 그 자체를 예측 불일치로 판정하지 않는다. 새로운 경고는 별도로 검토해야 한다.

## 5. 실제 검증 결과와 남은 경고

실행 결과:

- 기준 생성과 별도 프로세스의 반복 비교에서 3개 시퀀스 / 144개 샘플 모두 일치했다.
- 자동 검증 9개가 통과했다. 위치 코드·점수·후보 순서 변경, 샘플 누락,
  모델·입력·환경 식별 차이를 감지하고 기존 기준 파일 덮어쓰기를 거절했다.
- 사례 실행 순서를 반대로 바꿔도 각 사례의 결과가 같았다. 이는 테스트 사례 간 버퍼 초기화를 확인한 것이다.
- 모델 파일 해시는 작업 전후 동일하며 `pip check`에서 의존성 충돌이 없었다.
- app·database·routers 모듈을 불러오지 않았음을 확인했다. HTTP 서버 기동이나 DB 연결은 수행하지 않았다.

한 번의 기준 생성에서 아래 경고가 발생했다. 모두 reference.json에 횟수와 내용을 보존했다.

| 경고 | 확인된 사실 | 상태 |
|---|---|---|
| InconsistentVersionWarning 4회 | 모델 내부의 저장 버전은 scikit-learn 1.5.1, 현재 실행은 1.7.0 | 학습 환경 호환성 미검증 |
| RuntimeWarning 3,456회 | sklearn의 행렬 곱에서 divide by zero·overflow·invalid 경고가 각각 1,152회 발생 | 원인 미확정·후속 조사 필요 |
| UserWarning 144회 | 학습 때 특성 이름을 사용한 StandardScaler에 현재 코드가 이름 없는 배열을 전달 | 기존 동작으로 기록, 이번 단계에서 수정하지 않음 |

기록한 모든 예측·후보 수치는 유한하며 반복 실행 결과가 일치했다. 그렇더라도
수치 연산 경고가 무해하거나 학습 당시와 같은 결과라는 뜻은 아니다.
이 기준의 완료 범위는 **현재 선언된 버전의 로컬 환경에서 동작을 재현하고 비교할 수 있는 상태**다.

기준 확보 단계에서 검증하지 않은 항목은 학습 당시 환경의 결과, 실제 위치 정답에 대한 정확도,
Linux/EC2 환경, 여러 사용자 동시 처리, gRPC·HTTP 응답, 부하 성능이다.
전역 버퍼 분리와 위 경고의 해소, 성능 최적화는 기준 확보 작업에 포함하지 않았다.


## 6. 작업별 PR 정리(2026-10-28)

후속 변경이 섞이지 않도록 최신 upstream/develop에서 분리한 작업 폴더에서 원래 전역 버퍼 API를 호출하도록 검증 도구를 구성했다. 기준 결과·합성 입력·모델 파일은 보존했다. 서비스 코드는 이 단계에서 변경하지 않는다. 이후 세션 분리 PR에서 호출부를 새 API로 전환한다.
