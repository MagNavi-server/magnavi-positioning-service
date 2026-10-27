"""기존 추론 함수를 그대로 호출해 합성 입력의 기준 결과를 기록하고 비교한다."""

import argparse
from collections import Counter
import contextlib
from datetime import datetime
import hashlib
import importlib.metadata
import io
import json
import math
from pathlib import Path
import platform
import subprocess
import sys
import warnings
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[2]
BASELINE_DIR = Path(__file__).resolve().parent
INPUT_PATH = BASELINE_DIR / "synthetic_inputs.json"
REFERENCE_PATH = BASELINE_DIR / "reference.json"
FIELDS = ["Mag_X", "Mag_Y", "Mag_Z", "Ori_X", "Ori_Y", "Ori_Z"]
MODEL_FILES = [
    "mlp_model_6input.pkl", "scaler.pkl", "label_encoder_6input.pkl",
    "bias.pkl", "soft_iron_matrix.pkl", "preproc_params.pkl",
]
REL_TOL = 1e-9
ABS_TOL = 1e-12


def read_json(path):
    """UTF-8 JSON을 읽으며 비표준 NaN·Infinity 상수는 거절한다."""
    return json.loads(path.read_text(encoding="utf-8"), parse_constant=reject_constant)


def reject_constant(value):
    """비교를 무력화할 수 있는 JSON의 비유한 수치를 거절한다."""
    raise ValueError(f"JSON에 허용되지 않는 수치: {value}")


def sha256(path):
    """원본을 변경하지 않고 파일 내용을 식별할 SHA-256 해시를 구한다."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def required_versions():
    """검증 전용 파일에 고정된 의존성 이름과 버전을 읽는다."""
    lines = (BASELINE_DIR / "requirements.txt").read_text(encoding="utf-8").splitlines()
    return dict(line.split("==") for line in lines if line and not line.startswith("#"))


def environment():
    """기준 생성 전에 실제 설치 버전을 확인하고 실행 환경을 식별한다."""
    installed = {}
    for name, expected in required_versions().items():
        version = importlib.metadata.version(name)
        if version != expected:
            raise ValueError(f"{name}: 설치 버전 {version}, 필요한 버전 {expected}")
        installed[name] = version
    return {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": sys.platform,
        "machine": platform.machine(),
        "packages": installed,
    }


def fingerprints():
    """모델 묶음과 고정 입력을 식별해 다른 조건끼리 비교하는 것을 막는다."""
    return {
        "models": {name: sha256(ROOT / "model" / name) for name in MODEL_FILES},
        "inputs_sha256": sha256(INPUT_PATH),
    }


def source_fingerprints():
    """기준 생성 당시 코드를 추적하되 후속 리팩터링 자체를 실패로 보지 않는다."""
    paths = sorted((ROOT / "services").glob("*.py"))
    paths += sorted((ROOT / "schemas").glob("*.py"))
    paths += [ROOT / "requirements.txt", Path(__file__).resolve()]
    return {str(path.relative_to(ROOT)): sha256(path) for path in paths}


def load_inputs():
    """합성 입력의 필드 순서·사례 ID·수치 유효성을 검사한다."""
    fixture = read_json(INPUT_PATH)
    if fixture["schema_version"] != 1 or fixture["fields"] != FIELDS:
        raise ValueError("입력 형식 또는 센서 필드 순서가 다릅니다.")
    if fixture["source"] != "synthetic" or not fixture["cases"]:
        raise ValueError("비어 있지 않은 합성 입력 사례가 필요합니다.")
    seen = set()
    for case in fixture["cases"]:
        if not case["id"] or case["id"] in seen or not case["samples"]:
            raise ValueError("사례 ID가 중복되거나 샘플이 없습니다.")
        seen.add(case["id"])
        for values in case["samples"]:
            if len(values) != len(FIELDS) or any(
                type(value) not in (int, float) or not math.isfinite(value)
                for value in values
            ):
                raise ValueError(f"{case['id']}: 유한한 센서 수치 6개가 필요합니다.")
    return fixture["cases"]


def normalize_candidates(candidates):
    """NumPy 값이 섞인 후보 목록을 순서를 유지한 JSON 수치로 바꾼다."""
    return [[int(code), float(score)] for code, score in candidates]


def run_cases(cases):
    """DB·HTTP 앱 없이 변경 전 모델을 호출하고 사례마다 전역 버퍼를 비운다."""
    sys.path.insert(0, str(ROOT))
    # 기존 모델은 import 때 파일을 읽는다. app·database·routers는 불러오지 않는다.
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with contextlib.redirect_stdout(io.StringIO()):
            from services import model_service
            from schemas.prediction import SensorInput
        if not model_service.MODEL_LOADED:
            raise RuntimeError("기준 결과를 비교할 모델이 준비되지 않았습니다.")
        results = []
        window_size = int(model_service.PREPROC_PARAMS["window_size"])
        for case in cases:
            if len(case["samples"]) <= window_size:
                raise ValueError("각 사례에는 윈도우가 찬 다음 샘플까지 필요합니다.")
            for buffer in model_service.BUFFERS.values():
                buffer.clear()
            samples = []
            try:
                for values in case["samples"]:
                    data = SensorInput(**dict(zip(FIELDS, values, strict=True)))
                    prediction, logging_candidates, candidates = model_service.run_prediction(data)
                    samples.append({
                        "prediction": None if prediction is None else int(prediction),
                        "logging_candidates": normalize_candidates(logging_candidates),
                        "candidates": normalize_candidates(candidates),
                        "buffered_count": min(len(buffer) for buffer in model_service.BUFFERS.values()),
                    })
            finally:
                # 실패한 사례의 이력도 다른 사례의 기준 결과에 섞이지 않도록 지운다.
                for buffer in model_service.BUFFERS.values():
                    buffer.clear()
            results.append({"id": case["id"], "samples": samples})
        params = dict(model_service.PREPROC_PARAMS)
        fixed_sigma_loaded = model_service.FIXED_SIGMA is not None

    counts = Counter((type(item.message).__name__, str(item.message)) for item in caught)
    warning_summary = [
        {"category": category, "message": message, "count": count}
        for (category, message), count in sorted(counts.items())
    ]
    return {"preprocessing": params, "fixed_sigma_loaded": fixed_sigma_loaded, "cases": results}, warning_summary


def compare(expected, actual, path="result"):
    """구조·위치 코드·후보 순서를 엄격히 비교하고 실수에만 작은 오차를 허용한다."""
    if type(expected) is not type(actual):
        raise ValueError(f"{path}: 값의 형식이 다릅니다.")
    if isinstance(expected, dict):
        if expected.keys() != actual.keys():
            raise ValueError(f"{path}: 필드가 추가되거나 누락되었습니다.")
        for key in expected:
            compare(expected[key], actual[key], f"{path}.{key}")
    elif isinstance(expected, list):
        if len(expected) != len(actual):
            raise ValueError(f"{path}: 항목 개수가 다릅니다.")
        for index, (left, right) in enumerate(zip(expected, actual, strict=True)):
            compare(left, right, f"{path}[{index}]")
    elif isinstance(expected, float):
        if not math.isfinite(expected) or not math.isfinite(actual):
            raise ValueError(f"{path}: 유한하지 않은 수치입니다.")
        if not math.isclose(expected, actual, rel_tol=REL_TOL, abs_tol=ABS_TOL):
            raise ValueError(f"{path}: 수치가 허용 오차를 벗어났습니다.")
    elif expected != actual:
        raise ValueError(f"{path}: 값이 다릅니다.")


def capture(output):
    """별도 경로에 기준 결과를 최초 기록하고 기존 기준 파일은 덮어쓰지 않는다."""
    if output.exists():
        raise FileExistsError("기존 파일을 덮어쓸 수 없습니다. 새 --output 경로를 지정하세요.")
    runtime = environment()
    files = fingerprints()
    cases = load_inputs()
    results, warning_summary = run_cases(cases)
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    reference = {
        "schema_version": 1,
        "created_at": datetime.now(ZoneInfo("Asia/Seoul")).isoformat(),
        "purpose": "합성 입력에 대한 기존 추론 동작 비교. 실제 위치 정확도·성능 기준이 아님.",
        "git_revision": revision,
        "source_sha256": source_fingerprints(),
        "environment": runtime,
        "files": files,
        "comparison": {"relative_tolerance": REL_TOL, "absolute_tolerance": ABS_TOL},
        "warnings": warning_summary,
        "results": results,
    }
    # 비유한 결과가 있으면 파일을 만들기 전에 실패한다.
    encoded = json.dumps(reference, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    with output.open("x", encoding="utf-8") as stream:
        stream.write(encoded)
    return results, warning_summary


def verify(reference_path):
    """기준 파일을 읽기만 하며 실행 조건과 모든 샘플의 결과를 비교한다."""
    reference = read_json(reference_path)
    if reference["schema_version"] != 1:
        raise ValueError("지원하지 않는 기준 파일 형식입니다.")
    compare({"relative_tolerance": REL_TOL, "absolute_tolerance": ABS_TOL}, reference["comparison"], "comparison")
    compare(reference["environment"], environment(), "environment")
    compare(reference["files"], fingerprints(), "files")
    actual, warning_summary = run_cases(load_inputs())
    compare(reference["results"], actual)
    return actual, warning_summary


def main():
    """기준 생성과 검증을 분리하고 실패 시 종료 코드 1을 반환한다."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    capture_parser = subparsers.add_parser("capture", help="새 파일에만 기준 결과 생성")
    capture_parser.add_argument("--output", type=Path, required=True)
    verify_parser = subparsers.add_parser("verify", help="기존 기준과 현재 결과 비교")
    verify_parser.add_argument("--reference", type=Path, default=REFERENCE_PATH)
    args = parser.parse_args()
    try:
        if args.command == "capture":
            results, warning_summary = capture(args.output)
        else:
            results, warning_summary = verify(args.reference)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, ImportError, subprocess.CalledProcessError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    count = sum(len(case["samples"]) for case in results["cases"])
    print(f"PASS: {args.command}, {len(results['cases'])}개 시퀀스 / {count}개 샘플")
    for category in sorted({warning["category"] for warning in warning_summary}):
        count = sum(item["count"] for item in warning_summary if item["category"] == category)
        print(f"WARNING: {category} {count}회 (기준 파일과 README의 제한 사항 참고)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
