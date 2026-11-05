# Python 개발 실행 안내

이 문서는 기존 모델을 사용해 업무 DB 없이 로컬 gRPC 서버를 실행하고 기준 결과를 비교하는 방법을 설명합니다. 프로젝트 소개와 구현 과정은 [README](../README.md), 상세 메시지와 오류는 [gRPC 계약](api/GRPC_CONTRACT.md)을 참고합니다.

## 1. 검증 환경과 실행 범위

- 기록된 기준 환경은 **CPython 3.12.14·macOS arm64**입니다. 기준 결과 검사는 Python 패치 버전·운영체제·CPU 아키텍처·라이브러리 버전이 다르면 비교 전에 실패합니다.
- 모델 실행 의존성은 [requirements-streaming.txt](../requirements-streaming.txt), 통신 코드 생성기는 [tools/grpc/requirements.txt](../tools/grpc/requirements.txt)의 고정 버전을 사용합니다.
- 모델·스케일러·인코더·보정·전처리 파일 6개는 함께 사용합니다. 파일 조합과 해시는 [로컬 설정](../config/positioning.synthetic.json)에 등록되어 있습니다.
- 이 설정의 모델 키·버전·센서 프로필은 합성 입력을 사용하는 로컬 검증용입니다. 실제 모바일 센서나 운영 모델의 등록값으로 해석하지 않습니다.

## 2. 처음 환경을 준비할 때

저장소 루트에서 실행합니다. 기존 `.venv`를 재사용하지 않고 검증용 환경을 따로 만듭니다. 이미 `build/streaming-venv`가 있다면 새로 만들기 전에 용도와 설치 상태를 확인합니다.

```bash
python3.12 -m venv build/streaming-venv
build/streaming-venv/bin/python -m pip install -r requirements-streaming.txt -r tools/grpc/requirements.txt
build/streaming-venv/bin/python tools/grpc/generate.py
```

생성 도구는 계약 해시와 도구 버전을 확인하고 `build/generated`에 통신 코드를 만듭니다. 기존 출력 폴더는 덮어쓰지 않습니다. 재생성이 필요하면 기존 폴더를 보존하고 `--output`으로 새 경로를 지정한 뒤 아래 `PYTHONPATH`도 맞춥니다. 생성 코드는 직접 수정하거나 Git에 추가하지 않습니다.

Python이 사용하는 계약의 원본은 Spring 저장소입니다. [계약 출처 기록](../contracts/positioning-v1.lock.json)은 Spring PR #31의 머지 커밋을 가리킵니다. 패키지 경로 `magnavi.positioning.v1`과 시작 메시지의 동작 계약 버전 `2`는 서로 다른 항목입니다.

## 3. 업무 DB 없이 모델 서버 실행

```bash
PYTHONPATH=.:build/generated build/streaming-venv/bin/python -m positioning --config config/positioning.synthetic.json
```

- `127.0.0.1:50051`에서 gRPC 요청을 받으며 HTTP 포트는 열지 않습니다. 현재 설정 검증은 루프백 이외의 주소를 거절합니다.
- 파일 해시·모델 구조·시험 추론·gRPC 시작이 성공해야 요청을 처리할 수 있습니다. 단순히 Python 프로세스가 떠 있다는 사실과 준비 완료를 구분합니다.
- 종료는 Ctrl+C 또는 SIGTERM을 사용합니다. 서버는 준비 상태를 내리고 세션과 통신을 정리한 뒤 실행 중인 계산을 회수합니다.
- 테스트용 `--ready-file` 옵션은 새 파일에 준비 결과를 쓰며 정상 종료 시 제거합니다. 기존 파일을 덮어쓰는 용도로 사용하지 않습니다.

FastAPI와 함께 실행하려면 같은 환경의 `PYTHONPATH`와 `POSITIONING_CONFIG`를 지정해야 합니다. 다만 기존 `app.py` 시작 과정에는 업무 DB 초기화가 남아 있어 대상 DB를 먼저 확인해야 합니다. 모델만 검증할 때는 위 독립 진입점을 사용합니다. 같은 gRPC 포트를 공유하는 여러 HTTP worker를 실행하는 구성도 지원하지 않습니다.

## 4. 계약·기준 결과·통신 검증

환경과 생성 코드를 준비한 뒤 다음 명령으로 계약과 실제 gRPC 경로를 확인할 수 있습니다.

```bash
build/streaming-venv/bin/python tools/grpc/generate.py --check
PYTHONPATH=.:build/generated build/streaming-venv/bin/python -m unittest discover -s tests/contracts -p 'test_*.py' -v
PYTHONPATH=.:build/generated build/streaming-venv/bin/python -m unittest discover -s tests/streaming -p 'test_*.py' -v
build/streaming-venv/bin/python tests/baseline/prediction_baseline.py verify
build/streaming-venv/bin/python -m pip check
```

모델 준비·세션·기준 비교 도구의 실패 경로까지 확인하려면 아래 검사를 추가합니다.

```bash
PYTHONPATH=.:build/generated build/streaming-venv/bin/python -m unittest discover -s tests/baseline -p 'test_*.py' -v
PYTHONPATH=.:build/generated build/streaming-venv/bin/python -m unittest discover -s tests/session -p 'test_*.py' -v
PYTHONPATH=.:build/generated build/streaming-venv/bin/python -m unittest discover -s tests/runtime -p 'test_*.py' -v
```

위 5개 테스트 디렉터리의 기록된 결과는 2026-10-28 기준 76개 통과입니다. `verify`는 고정 합성 입력 144개의 결과를 기존 기준과 비교하며 기준 파일을 갱신하지 않습니다. 비교에 실패했다고 `reference.json`을 다시 생성해 맞추지 않습니다. 자세한 판정 조건은 [기준 결과 문서](../tests/baseline/README.md)를 따릅니다.

Spring과 함께 검증할 때는 Spring 저장소에서 `MAGNAVI_PYTHON_ROOT`에 이 저장소의 절대 경로를 지정합니다. 준비와 실행 명령은 [Spring 실제 Python 통합 검증](https://github.com/MagNavi-server/magnavi-api-service/blob/develop/docs/api/MODEL_GRPC.md#10-websocket부터-실제-python까지-통합-검증2026-10-31)을 참고합니다. Spring 전체 테스트에는 Docker가 필요하며, Python 경로가 없으면 실제 모델 테스트 5개가 건너뛰어집니다.

## 5. 검증 결과를 읽을 때의 주의점

- 모델 저장 버전 scikit-learn 1.5.1과 실행 버전 1.7.0의 차이, 수치 연산·특성 이름 경고가 남아 있습니다. 기존 결과의 반복 일치와 학습 환경 호환성은 구분합니다.
- gRPC는 스트림마다 센서 버퍼를 분리하지만 기존 HTTP `/predict`는 공유 세션을 사용합니다. 기존 HTTP의 여러 사용자 격리를 완료한 상태는 아닙니다.
- 합성 입력의 회귀 검증은 실제 위치 정답에 대한 정확도 측정이나 부하 시험이 아닙니다. Linux·운영 통신·실제 모바일·배포 검증은 별도로 수행해야 합니다.
- 이 안내는 기존 구현과 검증 기록을 정리한 문서입니다. 이번 README 작성 과정에서는 모델 테스트·서버 기동·배포를 새로 수행하지 않았습니다.

[프로젝트 README로 돌아가기](../README.md)
