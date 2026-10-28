# 브랜치·PR·배포 규칙 — FastAPI/Python

작성일: 2026-09-18

적용 저장소: `MagNavi-server/magnavi-positioning-service`

관련 문서: [커밋 컨벤션](commit-convention.md), [프로젝트 개요](../../PROJECT_OVERVIEW.md), [구현 계획](../../IMPLEMENTATION_PLAN.md)

## 1. 가장 중요한 원칙

개발자는 한 명이지만 작업 기록은 이슈와 PR로 남긴다. 작업 코드는 개인 포크에 올리고 중앙 저장소에는 PR로 반영한다.

```text
중앙 저장소에 이슈 생성
  → 최신 upstream/develop에서 새 작업 브랜치 생성
  → 구현·문서 작성·검증·커밋
  → 개인 포크 origin에 push
  → 중앙 develop에 PR
  → 검토 후 Squash and merge
  → 배포할 때 develop → release PR
  → release 반영 후 GitHub Actions 검증·배포
  → 배포 성공을 확인한 버전을 main에 PR로 반영
```

`release` 중심 CI/CD는 구축할 운영 계획이다. 이 문서 작성만으로 워크플로, 배포 자격 증명, 보호 규칙, 자동 머지 기능이 생성되는 것은 아니다.

## 2. 저장소와 Git 주소를 구분하자

| 용어 | 이 프로젝트에서의 대상 | 역할 |
|---|---|---|
| 중앙 저장소 | [MagNavi-server/magnavi-positioning-service](https://github.com/MagNavi-server/magnavi-positioning-service) | 합의한 코드를 모으고 배포하는 기준 |
| 개인 포크 | [ychoik/magnavi-positioning-service](https://github.com/ychoik/magnavi-positioning-service) | 작업 브랜치를 push하는 곳 |
| `origin` | 개인 포크를 가리키는 로컬 Git 별명 | 일반 작업의 push 대상 |
| `upstream` | 중앙 저장소를 가리키는 로컬 Git 별명 | 최신 개발 코드의 출처 |

포크는 저장소 단위 복사본이고, 브랜치는 그 저장소 안의 작업 흐름이다. 기능마다 다시 포크하지 않는다.

기본 브랜치는 GitHub가 저장소의 대표 기준으로 사용하는 브랜치다. 운영할 기본 브랜치는 `develop`이며, 2026-09-18 확인 시 두 중앙 저장소 모두 `develop`이었다. 개인 포크의 기본 브랜치를 바꾸는 것과 중앙 저장소의 설정을 바꾸는 것은 별개다.

## 3. 계속 유지할 중앙 브랜치

| 브랜치 | 역할 | 반영 방식 |
|---|---|---|
| `develop` | 기본 브랜치, 기능·수정·문서의 개발 통합 | 개인 작업 브랜치에서 PR |
| `release` | 배포 후보 검증 및 GitHub Actions CI/CD 기준 | 중앙 develop에서 PR |
| `main` | 실제 배포·검증을 마친 안정 버전 기록 | 검증된 release에서 PR |

이 프로젝트는 버전마다 `release/1.0.0`을 만드는 방식이 아니라 `release` 브랜치 하나를 계속 유지한다.

공용 세 브랜치에는 직접 push, 강제 push, 임의 삭제를 하지 않는다. PR 필수·강제 push 차단·삭제 차단을 보호 정책으로 두며 필수 승인 인원은 `0명`이다. 승인 인원이 0명이어도 변경 내용과 검증 결과는 직접 확인한다.

이 정책을 GitHub에서 강제하려면 별도 Ruleset 설정이 필요하다. CI 필수 검사는 실제 워크플로와 검사 이름이 준비된 다음 등록한다. 아직 없는 검사를 필수로 지정해 PR이 계속 대기하게 만들지 않는다.

## 4. 작업 브랜치 이름

새 작업은 이슈부터 만든다. 이슈는 개인 포크가 아니라 해당 서버의 중앙 저장소에 생성한다.

```text
타입/이슈번호-영문-작업설명
```

| 타입 | 목적 | 예시 |
|---|---|---|
| `feature` | 새 기능 | `feature/12-grpc-streaming` |
| `fix` | 오류 수정 | `fix/15-session-cleanup` |
| `refactor` | 내부 구조 개선 | `refactor/16-stream-lifecycle` |
| `perf` | 성능 개선 | `perf/17-request-overhead` |
| `docs` | 문서 | `docs/18-git-conventions` |
| `test` | 테스트 | `test/19-stream-validation` |
| `build` | 빌드·의존성 | `build/20-runtime-dependencies` |
| `ci` | CI/CD 자동화 | `ci/21-release-deployment` |
| `chore` | 그 외 설정·정리 | `chore/22-project-structure` |

표의 번호는 모두 예시이며 실제 생성된 이슈 번호가 아니다. 실제 작업에서는 먼저 발급된 번호를 넣는다.

- 설명은 영문 소문자와 하이픈으로 적고, 공백·한글·밑줄·`#`을 넣지 않는다.
- 이름만 읽어도 작업 목적을 알 수 있게 한다.
- 브랜치에는 `feature`, 기능 추가 커밋에는 `feat`를 사용한다.
- 한 작업 브랜치에는 한 이슈의 완료 범위를 담는 것을 기본으로 한다.
- 같은 브랜치 안에서도 기능, 테스트, 문서 커밋의 타입은 서로 다를 수 있다.
- 예전에 만든 `chore/project-setup` 같은 브랜치는 소급해서 이름을 바꾸거나 이력을 다시 쓰지 않는다. 새 작업부터 적용한다.

## 5. 실제 작업 순서

아래 명령은 이 저장소의 로컬 프로젝트 폴더에서 실행한다. `12`와 브랜치명은 설명용 예시이므로 실제 이슈에 맞게 바꾼다.

### 5.1 현재 상태 확인

```bash
git status --short --branch
git remote -v
```

미커밋 작업이 있다면 먼저 누구의 어떤 변경인지 확인한다. 다른 작업을 덮어쓰거나 억지로 지우고 진행하지 않는다. 이 문서의 명령을 통째로 실행하기보다 단계별 결과를 확인한다.

### 5.2 중앙 develop의 최신 코드에서 새 브랜치 생성

```bash
git fetch upstream
git switch --no-track -c docs/12-git-conventions upstream/develop
```

`fetch`는 중앙의 최신 이력을 로컬로 가져온다. 다음 명령은 그 시점의 중앙 `develop`을 출발점으로 작업 브랜치를 만든다. 로컬 `develop`에 먼저 이동하거나 개인 포크의 `develop`을 먼저 갱신할 필요는 없다.

`--no-track`은 새 작업 브랜치가 중앙 `develop`을 추적 대상으로 물려받지 않게 한다. 뒤의 `push -u origin`에서 개인 포크의 같은 작업 브랜치를 추적하도록 설정한다. [Git switch 설명](https://git-scm.com/docs/git-switch)

같은 이름의 브랜치가 이미 있거나 파일 충돌로 전환이 거절되면 멈추고 원인을 확인한다. `-C`나 강제 전환으로 기존 브랜치를 덮어쓰지 않는다.

### 5.3 작업·검증·커밋

```bash
git status --short
git add -- docs/convention/commit-convention.md docs/convention/branch-convention.md
git diff --cached
git diff --cached --check
git commit -m "docs(convention): 커밋 및 브랜치 규칙 문서화"
```

`git add`의 파일은 예시다. 실제 수정한 관련 파일을 선택하고 민감 정보·임시 파일이 없는지 확인한다. 검증은 변경 위험에 맞게 수행하며, 문서만 확인하고 전체 서버 테스트에 성공했다고 쓰지 않는다.

### 5.4 개인 포크에 push

```bash
git push -u origin docs/12-git-conventions
```

이 명령은 개인 포크에 작업 브랜치를 올린다. 중앙 `develop`에 직접 push하는 명령이 아니다. 이후에도 push 대상 브랜치를 명시하는 습관을 유지한다.

### 5.5 중앙 develop으로 PR

| GitHub PR 항목 | 선택할 값 |
|---|---|
| base repository | `MagNavi-server/magnavi-positioning-service` |
| base branch | `develop` |
| head repository | `ychoik/magnavi-positioning-service` |
| compare branch | 이번에 만든 작업 브랜치 |

PR 제목도 [커밋 컨벤션](commit-convention.md)을 따른다. “최종 수정”, “작업 완료”처럼 변경 목적을 알 수 없는 제목은 피한다.

## 6. PR 본문과 이슈 종료

실제로 이번 PR이 이슈를 완료하면 `Closes #12`, 일부만 다루거나 관련 배경을 참조하면 `Refs #12`를 사용한다.

기본 자동 종료는 PR의 대상이 중앙 저장소의 기본 브랜치일 때 적용된다. 이 프로젝트에서는 `develop` 대상 PR에 종료 문구를 넣는다. 이미 머지된 PR을 수정한 뒤 이슈가 닫혔다고 가정하지 말고 실제 상태를 확인한다. [GitHub 이슈 연결 설명](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/linking-a-pull-request-to-an-issue)

서버별 이슈 번호는 독립적이다. 두 서버가 함께 바뀌는 작업이면 각 저장소에 이슈와 PR을 만들고 서로 링크한다. 한쪽 구현만 끝났는데 다른 서버의 미완료 이슈까지 종료하지 않는다.

아래는 복사해서 사용할 PR 본문 예시다. 번호와 내용을 실제 작업에 맞춰 바꾸고, 검증 체크는 확인한 뒤에만 표시한다.

```markdown
## 관련 이슈

Closes #12

## 변경 목적

- 이 변경이 필요한 이유:

## 주요 변경 사항

- 변경한 내용:

## 검증 결과

- [ ] 변경 범위에 맞는 검증을 수행했다.
- 실행한 명령과 결과:
- 실행하지 못한 검증과 이유:

## 영향과 주의점

- API·gRPC·DB·모델 호환성 영향:
- 필요한 설정 또는 배포 순서:
- 관련 문서:
```

## 7. 머지 방식과 작업 브랜치 보존

| 대상 | 사용할 방식 | 이유 |
|---|---|---|
| 개인 작업 브랜치 → 중앙 `develop` | `Squash and merge` | 한 이슈의 최종 변경을 커밋 하나로 정리 |
| 중앙 `develop` → `release` | `Create a merge commit` | 장기 유지 브랜치 사이의 이력 보존 |
| 검증된 `release` → `main` | `Create a merge commit` | 배포된 변경의 이력 보존 |
| `release`에서 생긴 수정 → `develop` | `Create a merge commit` | 배포 중 수정 사항을 개발 코드에도 반영 |

Squash 결과의 제목·본문을 머지 화면에서 확인해 커밋 규칙에 맞춘다. PR 제목이 항상 원하는 형태로 자동 설정된다고 가정하지 않는다.

공용 브랜치 사이에는 Squash를 사용하지 않는다. 같은 이력을 계속 새 커밋으로 바꾸면 다음 승격 PR에서 이미 반영한 커밋이 다시 나타날 수 있다. [GitHub 머지 방식 설명](https://docs.github.com/en/pull-requests/reference/pull-request-merges)

Kohere와 마찬가지로 개인 작업 브랜치는 머지 후에도 보존한다. Squash하기 전 세부 커밋을 확인하는 용도다. 자동 삭제하지 않고, 다음 작업에는 반드시 새 브랜치를 사용한다. 이미 끝난 PR의 브랜치에 새 작업을 계속 쌓지 않는다.

머지 후 다음 작업을 시작할 때는 다시 `git fetch upstream`을 실행하고 최신 `upstream/develop`에서 분기한다. 로컬 `develop`도 갱신하고 싶다면 아래처럼 진행할 수 있다.

```bash
git fetch upstream
git switch develop
git merge --ff-only upstream/develop
```

위 명령은 로컬 `develop`이 이미 있을 때의 예시다. 없다면 `git switch --no-track -c develop upstream/develop`으로 만든다. `--ff-only`가 실패하면 로컬과 중앙 이력이 갈라진 것이므로 차이를 확인한다. 동기화를 이유로 `reset --hard`나 강제 push를 기본 절차로 사용하지 않는다. [Git merge 설명](https://git-scm.com/docs/git-merge)

## 8. release 중심 GitHub Actions CI/CD 계획

CI는 변경 코드의 빌드·테스트 검증이고, CD는 검증한 산출물을 서버에 배포하는 과정이다.

| 시점 | GitHub Actions에서 구성할 동작 | 배포 |
|---|---|---|
| `develop` 대상 PR 생성·수정 | 해당 서버의 빌드·테스트·계약 검증 | 하지 않음 |
| `release` 대상 PR 생성·수정 | 배포 후보의 빌드·테스트 검증 | 하지 않음 |
| PR 머지로 중앙 `release`에 반영 | 실제 release 커밋 검증 → 산출물 생성 → 성공한 산출물 배포 | 수행 |
| 배포 후 정상 동작 확인 | 검증된 release 버전을 main에 PR로 기록 | main 반영만으로 재배포하지 않음 |

PR 검증은 `pull_request`의 대상 브랜치를 `develop`·`release`로 지정하고, 배포 흐름은 중앙 저장소의 `push` 이벤트 중 `release` 반영을 기준으로 설계한다. PR을 단순히 닫은 것만으로 배포하지 않는다. [GitHub Actions 이벤트 설명](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows)

운영 시 지킬 사항:

1. 개인 포크 PR의 코드는 비밀값 없이 검증한다. 배포 권한을 가진 `pull_request_target` 작업에서 포크 코드를 받아 실행하지 않는다.
2. 배포 작업은 중앙 저장소에서만 실행되도록 제한한다. 필요한 자격 증명은 신뢰된 배포 단계에만 제공하고 최소 권한을 사용한다.
3. 검증이 실패하면 배포 단계로 넘어가지 않는다. 성공한 검사와 배포 산출물의 커밋 SHA를 연결한다.
4. 동시에 여러 release 반영이 생겼을 때 이전 실행이 나중 버전을 덮어쓰지 않도록 배포 실행 순서를 제어한다.
5. 배포 대상 EC2·환경, 승인 필요 여부, 산출물 보관·되돌리기 절차는 CI/CD 구현 때 확정한다.
6. 배포 중에는 새 승격을 잠시 멈추거나, 실제 배포 SHA와 main에 반영할 버전이 일치하는지 확인한다. 실패한 배포를 안정 버전으로 기록하지 않는다.

이 서버의 배포 검증: 모델·스케일러·인코더·보정 파일의 버전과 실행 의존성을 함께 식별하고, 모델 준비 상태와 스트리밍 연결 정리까지 확인한다.

배포 후보에서만 고친 내용이 있다면 `release` 기준 별도 작업 브랜치와 PR로 수정하고, 반영 뒤 `release → develop` PR로 같은 수정 사항을 되돌려 반영한다. 수정 뒤에는 CI와 배포 검증을 다시 거친다. 공용 브랜치를 직접 수정하거나 보호 규칙을 우회하지 않는다.

운영 장애에서 최신 develop을 그대로 배포하면 미완성 기능이 섞일 수 있다. 긴급 수정은 실제 배포 커밋을 먼저 확인하고 분기 기준·배포 대상·개발 브랜치 반영 경로를 따로 정한다.

## 9. 적용 범위와 확인 목록

두 서버는 커밋·브랜치·머지 정책을 동일하게 유지한다. 저장소 주소, 작업 영역 예시, 빌드·테스트·배포 구현은 서버별 문서를 따른다. 공통 정책을 바꾸면 다른 서버 문서도 함께 확인한다.

- [ ] 중앙 저장소의 이슈를 먼저 만들었다.
- [ ] 최신 중앙 develop에서 분기했다. 배포 수정 예외라면 기준을 명시했다.
- [ ] 작업 브랜치 이름에 실제 이슈 번호를 넣었다.
- [ ] 개인 포크에 push하고 중앙 develop을 PR 대상으로 선택했다.
- [ ] 완료 이슈는 Closes, 일부 작업은 Refs로 구분했다.
- [ ] 수행한 검증과 미수행 검증을 PR에 적었다.
- [ ] 작업 PR과 공용 브랜치 PR의 머지 방식을 구분했다.
- [ ] release 배포 전후에 검증한 커밋과 산출물 버전을 확인했다.
- [ ] 문서에 적힌 계획과 실제 GitHub 설정·워크플로 적용 상태를 구분했다.

이 문서의 명령과 정책은 사용자가 실행하거나 별도 작업으로 적용하기 위한 안내다. 문서를 추가하는 작업 자체에서는 브랜치 생성, commit, push, 이슈·PR 생성, 보호 규칙 변경, CI/CD 구현을 수행하지 않는다.
