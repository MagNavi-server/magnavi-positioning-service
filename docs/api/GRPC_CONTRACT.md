# Spring–Python gRPC 계약과 로컬 실행

최초 계약 확보: 2026-10-27 · 구현·검증: 2026-10-28 (한국 시간)

기존 Python의 `pad` 전처리와 첫 샘플 예측을 유지하고 Spring도 같은 응답을 받도록
계약 버전을 2로 변경했다. Python 실제 모델·스트림별 세션·자원 한도를 연결했다.
운영 센서 규격·TLS·배포·부하 측정은 후속이다. Python 변경은 이 PR에 포함하며 대응하는 Spring 변경은 아직 로컬 미커밋 상태다.

## 1. 단일 원본과 버전 전환

| 항목 | 현재 값 |
|---|---|
| 원본 저장소·경로 | `MagNavi-server/magnavi-api-service`의 `src/main/proto/magnavi/positioning/v1/positioning.proto` |
| 이전 원본 커밋 | `058ca4b584fcd25ddff1ddf2de94ce092fe89475` |
| 이번 원본 | 위 계약에서 수정한 Spring 로컬 파일. 새 커밋은 아직 없음 |
| Python 빌드 사본 | [positioning.proto](../../contracts/magnavi/positioning/v1/positioning.proto) |
| 출처·해시 | [positioning-v1.lock.json](../../contracts/positioning-v1.lock.json), `source_state=local_uncommitted`, `source_commit=null` |
| SHA-256 | `32594fc237e0443c9ddf38ee0593615acd6f3bf8c9a871d8232f567937ee95ac` |
| 서비스 경로 | `magnavi.positioning.v1.PositioningService/PredictStream` |
| 동작 계약 버전 | `contract_version=2` |

메시지 필드 번호·형식·서비스 경로는 그대로여서 protobuf 패키지 경로의 `v1`을 유지한다.
초기 응답의 의미는 바뀌므로 버전 필드 2를 요구하고 이전 1은 거절한다.
`WarmingUp` 메시지는 삭제하거나 번호를 재사용하지 않으며 계약 v2에서는 보내지 않는다.
Spring WebSocket의 `start`·`ready`도 `contractVersion=2`다. 학습 모델 버전과는 별개다.

전환 순서는 양쪽 계약·생성 코드·검증 코드 배포 준비 → 등록값을 맞춘 로컬 연결 검증 →
앱의 WebSocket v2 적용·센서 확인 → 운영 활성화다. 기존 v1과 자동 호환되지 않는다.
앱·운영 설정은 이번에 변경하지 않았으며 Spring 기능은 기본 비활성이다.
Python 사본을 독자적으로 수정하지 않고 Spring 원본과 함께 갱신한다.
Spring 계약 커밋이 게시된 뒤 해당 실제 커밋을 lock에 기록해야 한다. 해시는 변경 감지이며 서명 검증이 아니다.

## 2. 요청·응답 규칙

| 단계 | 규칙 |
|---|---|
| 시작 | 새 UUID `session_id`, 계약 2, 등록된 모델 키·버전·프로필을 `start`로 한 번 전송 |
| 준비 | 파일 6개 해시·구조·기준 추론·지원 라벨·gRPC 시작 후 `ready` 반환 |
| 입력 | ready 이후 자기장·방향 각 3축과 순번·경과 시간을 하나씩 전송 |
| 결과 | 첫 번째부터 모든 입력에 같은 순번의 `prediction` 하나. 1·15·16번째 모두 예측 |
| 윈도우 | 최근 16개, 부족하면 기존 pad 계산 사용. `required_count=16`은 대기 횟수가 아님 |
| 종료 | 취소·오류·입력 종료 시 세션 정리. 재접속은 새 UUID와 빈 버퍼 사용 |

Spring은 서버의 예상하지 못한 정상 종료도 연결 불가로 분류한다. Python은 클라이언트의
입력 종료를 정리하며 Spring 측 정상 측정 종료는 기존처럼 RPC 취소를 사용한다.
모든 이벤트는 시작 요청의 측정 ID를 유지한다. 활성 ID 중복 요청은 기존 연결을 닫지 않고 거절한다.

| 입력 | 검사·대응 |
|---|---|
| `magnetic.x/y/z` | `Mag_X/Mag_Y/Mag_Z`에 순서대로 전달 |
| `orientation.x/y/z` | `Ori_X/Ori_Y/Ori_Z`에 순서대로 전달 |
| `sequence` | 1부터 정확히 +1, 중복·누락·역순 거절 |
| `elapsed_nanos` | 첫 값 0, 이후 엄격히 증가하는 기기 경과 나노초. 서버 시각과 빼지 않음 |
| presence | 벡터·축·순번·시간 누락 거절. 정상 숫자 0과 구분 |
| 센서 숫자 | NaN·무한대 거절. 물리적 단위·축·주파수는 아직 미확정 |

현재 인코더의 65개 라벨, 29개 위치 코드는 모두 정수→문자열 변환으로 표현이 유지된다.
현재 모델의 정수 계산을 유지하고 전송 경계에서만 문자열로 바꾼다.
`007`은 계약 설명용 예시이며 실제 현재 모델에서 발견한 코드가 아니다. 새로운 모델에
이런 표현이 있으면 시작 검증에서 거절하므로 라벨 손실을 조용히 허용하지 않는다.

후보는 실제 등록 코드 1~3개, 점수 내림차순, 중복 없음, 최상위 결과와 첫 후보 일치를 검사한다.
점수는 유한한 0~1이며 실제 위치 정확도와 같지 않다. HTTP의 부족한 후보 채움 `(0, 0.0)`은
실제 코드가 아닐 때 제외한다. HTTP용 오류 값을 정상 위치 코드로 전달하지 않는다.

| 상황 | gRPC 상태 | Spring 분류 |
|---|---|---|
| 누락·순서·수치 오류 | `INVALID_ARGUMENT` | `INVALID_INPUT` |
| 계약·모델·프로필 불일치, 활성 ID 중복 | `FAILED_PRECONDITION` | `INVALID_INPUT` |
| 모델 준비 안 됨 | `UNAVAILABLE` | `UNAVAILABLE` |
| 연결·계산 용량 초과 | `RESOURCE_EXHAUSTED` | `OVERLOADED` |
| 시작·입력·계산·송신 시간 초과 | `DEADLINE_EXCEEDED` | `TIMED_OUT` |
| 취소 | `CANCELLED` | `CANCELLED` |
| 내부 실패 | `INTERNAL` | `INTERNAL` |

상세 예외·센서 원문·예측 상세는 기본 로그나 오류 응답에 남기지 않는다.

## 3. 구조·한도·준비 상태

- [서버](../../positioning/server.py): RPC별 세션 하나, 순서대로 읽기 → 계산 → 쓰기.
  연결당 계산 중인 샘플·송신 결과를 하나씩만 유지하고 별도 무제한 입력/결과 큐를 만들지 않는다.
  gRPC 흐름 제어와 메시지 크기·활성 RPC 한도를 함께 사용한다.
- [계산 작업자](../../positioning/worker.py): 통신 루프 밖의 제한된 스레드 실행기.
  제출 총수는 `workers + pending_jobs` 이하이며 취소된 계산도 실제 완료 전까지 용량을 유지한다.
  대기 중 작업은 취소하고 실행 중 작업의 결과는 버린다. `async` 자체가 성능 개선을 뜻하지 않는다.
- [공용 수명 관리](../../services/model_lifecycle.py): HTTP와 gRPC가 같은 모델 런타임 참조.
  종료 시 준비 상태 내림 → 세션 닫기 → RPC 종료 → 실행 중 계산 회수 → 모델 정리.
  Python 스레드의 실행 중 계산은 강제 중단할 수 없어 영구 정지 계산의 종료 상한은 보장하지 않는다.
- [상태 경로](../../routers/health_router.py): `/health/live`는 HTTP 생존,
  `/health/ready`는 모델과 실제 gRPC 준비가 모두 확인되어야 200. 미설정·종료·실패는 503.
  기존 FastAPI 앱을 실행하면 업무 DB 초기화도 남아 있으므로 단순 점검에는 아래 독립 진입점을 사용한다.

[합성 검증 설정](../../config/positioning.synthetic.json)의 기본값은 루프백 50051,
작업자 1·계산 대기 8·활성 RPC 8·메시지 65536바이트다. 시작 3초·입력 유휴 30초·
계산 5초·송신 5초 제한이며 부하 테스트로 정한 최적값은 아니다.
`magnavi-local-synthetic` / `baseline-20261015` / `synthetic-v1`은 검증용 등록값이다.
원격 평문 노출을 막기 위해 현재 바인딩은 루프백만 허용한다. TLS·컨테이너 통신은 후속이다.
센서의 경과 시간 증가만 검사하며 큰 시간 공백의 윈도우 초기화 정책은 아직 정하지 않았다.

## 4. 생성·실행·재검증

CPython 3.12.14 / macOS arm64에서 기존 회귀 의존성에 grpcio 1.85.0·protobuf 7.36.2를
추가한 별도 `build/streaming-venv`를 사용했다. 기존 `.venv`와 최상위 `requirements.txt`는 보존했다.
생성기 버전은 [전용 의존성](../../tools/grpc/requirements.txt)에 고정되어 있다.

처음 준비할 때:

```bash
python3.12 -m venv build/streaming-venv
build/streaming-venv/bin/python -m pip install -r requirements-streaming.txt -r tools/grpc/requirements.txt
build/streaming-venv/bin/python tools/grpc/generate.py
```

생성 도구는 해시·버전을 확인하고 기존 출력 폴더를 덮어쓰지 않는다.
재생성 시 기존 폴더를 보존하거나 `--output`으로 새 경로를 지정하고 PYTHONPATH도 맞춘다.
`build/generated`의 생성 코드는 직접 수정하거나 Git에 추가하지 않는다.

DB 없는 로컬 gRPC 서버:

```bash
PYTHONPATH=.:build/generated build/streaming-venv/bin/python -m positioning --config config/positioning.synthetic.json
```

종료는 Ctrl+C 또는 SIGTERM을 사용한다. `--ready-file`은 테스트용 준비 확인 JSON을 새 파일에
쓰고 정상 종료 시 제거한다. 독립 진입점은 HTTP 포트를 열지 않는다.
FastAPI와 함께 실행할 경우 동일 환경에서 `POSITIONING_CONFIG`와 `PYTHONPATH`를 지정하되
기존 DB 초기화 대상 확인이 필요하다. 동일 gRPC 포트로 여러 HTTP worker를 띄우지 않는다.

재검증:

```bash
build/streaming-venv/bin/python tools/grpc/generate.py --check
PYTHONPATH=.:build/generated build/streaming-venv/bin/python -m unittest discover -s tests/streaming -p 'test_*.py' -v
PYTHONPATH=.:build/generated build/streaming-venv/bin/python -m unittest discover -s tests/contracts -p 'test_*.py' -v
build/streaming-venv/bin/python tests/baseline/prediction_baseline.py verify
build/streaming-venv/bin/python -m pip check
```

Spring 저장소에서 `MAGNAVI_PYTHON_ROOT`에 이 Python 저장소 절대 경로를 지정해
`./gradlew test --tests '*PythonModelIntegrationTests' --rerun-tasks`를 실행한다.
환경 변수가 없으면 해당 교차 언어 테스트는 건너뛴다. 전체 Spring 테스트에는 Docker도 필요하다.

## 5. 검증 범위와 트러블슈팅 기록

Python 전체 76개: 기존 기준 9·세션/HTTP 22·모델/앱 수명 21·계약 표현 8·실제 gRPC 16개 통과.
고정 합성 입력 144개를 gRPC로 보내 기존 코드·후보·점수와 비교했다(상대 1e-9, 절대 1e-12).
Spring 전체 269개 테스트(실패·오류·건너뜀 0)와 실행 JAR 빌드도 통과했으며,
실제 Java 클라이언트 → Python 모델의 144개 결과 비교를 포함한다.
두 측정의 96개 교차 입력, 누락·비유한 값·순번 오류·계약 불일치·중복 ID·연결/계산 한도,
취소·재접속·대기 작업 취소·서버 종료·기한 만료·준비 상태를 검증했다.

초기 응답 불일치의 원인·선택 근거·이력서에 쓸 수 있는 범위는
[트러블슈팅 기록](../troubleshooting/grpc-first-prediction.md)을 참고한다.
이는 정확도·성능 향상이나 운영 장애 해결의 증명이 아니다. 기존 sklearn 저장/실행 버전 차이와
수치 경고는 그대로 남아 있다. 실제 센서·운영 장소 매핑·Linux·TLS·앱 종단·부하 검증은 후속이다.

공식 참고: [gRPC Python 생성](https://grpc.io/docs/languages/python/quickstart/),
[gRPC AsyncIO 제약](https://grpc.github.io/grpc/python/grpc_asyncio.html#caveats).
