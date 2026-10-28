"""고정된 Spring 계약과 생성기 버전을 확인한 뒤 Python 통신 코드를 만든다."""

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "contracts"
LOCK_FILE = CONTRACTS / "positioning-v1.lock.json"
REQUIREMENTS = Path(__file__).with_name("requirements.txt")


def verify_contract() -> Path:
    """로컬 계약의 내용이 고정된 Spring 원본 해시와 다르면 생성을 중단한다."""
    lock = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
    relative_path = Path(lock["snapshot_path"])
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise ValueError("계약 경로는 contracts 내부의 상대 경로여야 합니다.")
    source = CONTRACTS / relative_path
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if digest != lock["sha256"]:
        raise ValueError("계약 해시가 다릅니다. Spring 원본과 승인된 변경을 확인하세요.")
    print(f"계약 해시 일치: {digest}")
    return relative_path


def verify_toolchain() -> None:
    """다른 생성기나 실행 라이브러리를 섞어 생성 코드가 달라지는 것을 막는다."""
    for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        package, expected = line.split("==")
        try:
            actual = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError as error:
            raise ValueError(
                f"{package} 미설치: 별도 환경에 tools/grpc/requirements.txt를 설치하세요."
            ) from error
        if actual != expected:
            raise ValueError(f"{package} 버전 불일치: 필요 {expected}, 현재 {actual}")


def generate(relative_path: Path, output: Path) -> None:
    """임시 폴더에서 컴파일을 끝낸 뒤 새 출력 폴더로 옮겨 기존 파일을 보존한다."""
    if output.exists():
        raise ValueError("출력 경로가 이미 있습니다. --output으로 새 경로를 지정하세요.")
    output.parent.mkdir(parents=True, exist_ok=True)
    # 실패한 생성 결과가 정상 산출물처럼 남지 않도록 성공할 때만 옮긴다.
    with tempfile.TemporaryDirectory(prefix=".grpc-codegen-", dir=output.parent) as temporary:
        staging = Path(temporary) / "generated"
        staging.mkdir()
        subprocess.run(
            [
                sys.executable, "-m", "grpc_tools.protoc",
                f"--proto_path={CONTRACTS}",
                f"--python_out={staging}",
                f"--pyi_out={staging}",
                f"--grpc_python_out={staging}",
                str(relative_path),
            ],
            cwd=CONTRACTS,
            check=True,
        )
        staging.rename(output)
    print(f"Python 통신 코드 생성 완료: {output}")


def main() -> int:
    """의존성 없는 해시 검사 또는 고정 도구를 사용한 생성을 선택한다."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="계약 해시만 검사")
    parser.add_argument("--output", type=Path, default=ROOT / "build/generated")
    arguments = parser.parse_args()
    try:
        relative_path = verify_contract()
        if not arguments.check:
            verify_toolchain()
            generate(relative_path, arguments.output.resolve())
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"생성 중단: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
