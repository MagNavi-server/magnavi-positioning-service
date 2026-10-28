# 모델 준비 상태와 센서 입력·오류 처리 보완

작업일: 2026-10-27(한국 시간) · 대상: Python · 로컬 구현·검증 완료, 2026-10-28 [PR #4](https://github.com/MagNavi-server/magnavi-positioning-service/pull/4) 머지, 미배포

게시·후속 상태(2026-10-28): [PR #6](https://github.com/MagNavi-server/magnavi-positioning-service/pull/6)에서 실제 gRPC 연결까지 구현·검증했다. 아래 검증과 남은 작업은 해당 구현일의 기록이며, 현재 범위는 [구현 계획](../../IMPLEMENTATION_PLAN.md)과 [gRPC 검증 기록](grpc-first-prediction.md)을 따른다.

## 문제와 변경

모델 모듈을 import하면 파일을 읽고 실패해도 출력만 남겼다. `MODEL_LOADED`를 다른 모듈에 복사한 값은
모델 수명 변경을 반영하기 어려웠다. HTTP의 샘플 대기 분기는 정의되지 않은 `response`·`status`를 사용했다.
입력은 일반 float여서 NaN·무한대와 실패한 계산의 센서 이력을 막는 경계가 없었다.

| 변경 파일 | 책임과 변경 이유 |
|---|---|
| [model_loader.py](../../services/model_loader.py) | 필수 파일 6개를 모두 확인한 뒤 로딩하고 6차원 입력·보정 행렬·클래스 순서·전처리 설정을 검증 |
| [model_runtime.py](../../services/model_runtime.py) | 검증 중에는 요청을 거절하고 독립 세션의 시험 예측 성공 후 준비 상태 공개, 종료 시 세션 정리 |
| [model_service.py](../../services/model_service.py) | 기존 계산 순서와 진입점을 유지하고 준비된 모델 묶음을 명시적으로 사용 |
| [model_lifecycle.py](../../services/model_lifecycle.py), [app.py](../../app.py) | FastAPI 시작 때 모델 준비 후 기존 DB 초기화, 종료·후속 초기화 실패 시 정리 |
| [prediction.py](../../schemas/prediction.py), [prediction_session.py](../../services/prediction_session.py) | 유한 입력 검증과 실패한 계산의 버퍼 복구 |
| [prediction_router.py](../../routers/prediction_router.py) | 정상 응답 유지, 오류 상태 구분, 비유한 입력의 오류 JSON 안전 처리, 원본 센서·상세 후보 로그 제거 |

모델 준비는 `not_started → loading → ready` 순서다. 로딩·구조·시험 예측 실패는 `failed`로 기록하고
예외를 서버 시작 바깥으로 전달한다. 종료는 `stopped`로 바꾸며 이미 계산 중인 이전 결과도 거절한다.
새 세션은 현재 모델에서 생성한 객체여야 하며 재시작 전 세션을 재사용할 수 없다.
시험 예측은 합성 입력으로 윈도우 하나를 채운 뒤 이력을 폐기하므로 사용자 버퍼를 오염시키지 않는다.

`preproc_params.pkl`도 필수 묶음으로 취급하며 누락 시 기본 설정으로 조용히 시작하지 않는다.
현재 사용 중인 adaptive·pad·16개 설정과 모델 파일은 유지한다. `hop_size`는 실제 구현된 1만 허용하고,
fixed 설정을 사용하는 별도 묶음은 세 축의 유효한 `wavelet_sigma.pkl`까지 있어야 시작할 수 있다.

## HTTP 계약과 입력 검증

센서 6개 필드와 정상 응답 `prediction`·`confidence`·`top_k_results`의 값·형식은 유지한다.
숫자로 변환 가능한 입력의 기존 변환 동작은 유지하며 임의의 센서 물리 범위는 추가하지 않았다.

| 상황 | HTTP | 응답 |
|---|---:|---|
| 정상 예측 | 200 | 기존 위치 코드·점수·문자열 후보 |
| 모델 미준비·종료 | 503 | 기존 `prediction=-1`, `confidence=0.0` 오류 본문 |
| wait 정책에서 샘플 부족 | 202 | 기존 의도대로 `prediction=-2`, `confidence=0.0` 대기 본문 |
| 누락·잘못된 형식·NaN·무한대 | 422 | `detail`의 검증 오류 목록, 필드 위치·종류·설명만 포함 |
| 보정·전처리·스케일링 결과의 비유한 값 | 422 | `detail`의 고정된 입력 처리 오류 설명 |
| 모델 계산 예외·유효하지 않은 후보 점수 | 500 | `{"detail":"Prediction failed."}` |

모델 미준비가 기존 200에서 503으로 바뀌므로 호출 앱은 비정상 상태를 분기해야 한다.
센서 부족의 202는 현재 기본 pad 설정에서는 발생하지 않으며 wait 설정의 별도 테스트에서 확인했다.
입력 오류 응답에서 원본 `input`과 내부 `ctx`를 제외해 센서값 노출과 NaN JSON 직렬화 실패를 방지했다.
기존 HTTP 공유 세션 문제는 남아 있으므로 이번 변경을 HTTP 사용자 격리 완료로 보지 않는다.

Pydantic을 우회해 만든 객체도 공용 추론 진입점에서 재검증한다. 계산이 버퍼 갱신 후 실패하면
가장 오래된 샘플까지 포함해 이전 윈도우를 복구한다. 그 사이 세션이 종료되면 빈 상태를 유지한다.

## 검증 방법과 결과

기존 CPython 3.12.14·macOS arm64·scikit-learn 1.7.0 검증 환경을 사용했다. 의존성 추가·버전 변경은 없다.
검증 환경이 없는 경우 [기존 준비 방법](../../tests/baseline/README.md)에 따라 만든 환경에
`tests/session/requirements.txt`를 설치한다. 실제 업무 서버의 전체 의존성 구성과는 구분한다.

```sh
.venv/bin/python -m unittest discover -s tests/runtime -p 'test_*.py' -v
.venv/bin/python -m unittest discover -s tests/session -p 'test_*.py' -v
.venv/bin/python -m unittest discover -s tests/baseline -p 'test_*.py' -v
.venv/bin/python tests/baseline/prediction_baseline.py verify
.venv/bin/python -m pip check
```

- 런타임·앱 수명 **21개 통과**: 파일 누락·손상·구조 불일치, 시험 예측 실패·재시도, 준비 중 요청, 한 번 로딩, 종료 경합, 재시작, 내부 입력 우회, 넘침, 버퍼 복구 등.
- 세션·HTTP **22개 통과**: 기존 세션 격리·동시 처리와 정상 응답 회귀, 503·202·422·500, 오류 후 복구, 비유한 원본을 포함하지 않는 JSON 오류 응답 등.
- 기존 기준 **9개 통과**: 별도 프로세스 두 번에서 기존 144개 결과 일치, 모델·입력·환경 및 결과 변경 감지, 기준 파일 보존.
- 모델 6개·합성 입력·기준 결과는 변경하지 않았다. 기준 파일 SHA-256은 `7f381acd5fa8e260c0bbad5748ba8d070d68627e5d5e6fb011e8fbc68106333c`다.

HTTP는 예측 라우터의 임시 앱에 ASGI 메시지를 전달했다. 실제 `app.py` 수명 연결은 DB와 업무 라우터를
명시적으로 대체해 검사했다. 실제 DB 연결·초기화·네트워크 서버·운영 환경 실행은 수행하지 않았다.
앱 import 자체는 DB 쓰기를 하지 않지만 업무 DB 모듈의 설정·엔진 구성은 남아 있다.

## 남은 경고와 후속 작업

모델 저장 버전 1.5.1과 실행 버전 1.7.0의 차이 및 기존 수치 경고는 해결하지 않았다.
시작 시험 예측에서 버전 경고 4회·연산 경고 384회·특성 이름 경고 16회를 확인했고,
종류별 횟수를 로그와 `get_model_status()`의 `startup_warnings`에 남긴다.
준비 완료는 현재 환경에서 로딩·구조·시험 예측을 통과했다는 뜻이며 학습 버전 호환성이나 정확도 보장은 아니다.
실행 사례 144개에서는 기존 연산 경고 3,456회·특성 이름 경고 144회가 계속 발생한다.

다음은 센서 단위·축·주파수와 Spring 계약 확인, gRPC 스트림별 세션 연동이다.
순번·중복·시간 공백 정책, 작업자·큐·연결 한도, 전송 직전 종료 확인, Linux·부하 검증은 후속이다.
기존 HTTP·업무 DB 제거는 앱 전환 후 진행한다. 성능 최적화나 성능 향상 수치를 이번 작업에 포함하지 않았다.

포트폴리오 기록에는 준비 실패의 조용한 통과를 차단한 과정, 샘플 대기 분기 오류 수정,
입력 거절 이후에도 기존 윈도우를 보존하는 처리와 52개 테스트·144개 회귀 결과를 사용할 수 있다.
