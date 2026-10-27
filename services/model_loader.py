"""모델 파일 묶음을 명시적으로 읽고 계산에 필요한 구조를 검증한다."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pywt

from .prediction_errors import ModelPreparationError
from .prediction_session import MAGNETIC_AXES


MODEL_DIR = Path(__file__).resolve().parents[1] / "model"
MODEL_FILES = {
    "mlp": "mlp_model_6input.pkl",
    "scaler": "scaler.pkl",
    "label_encoder": "label_encoder_6input.pkl",
    "soft_iron": "soft_iron_matrix.pkl",
    "hard_iron": "bias.pkl",
    "preproc_params": "preproc_params.pkl",
}
DEFAULT_PARAMS = {
    "wavelet": "db4", "level": 1, "thresh_mode": "soft",
    "window_size": 16, "hop_size": 1, "border_mode": "symmetric",
    "strategy": "adaptive", "warmup_policy": "pad", "min_fill_ratio": 0.5,
}


@dataclass(frozen=True)
class ModelBundle:
    """동시에 검증한 모델과 설정을 한 단위로 전달하며 실행 중 교체하지 않는다."""

    components: dict[str, Any]
    preprocessing: dict[str, Any]
    fixed_sigma: dict | None = None


def validate_preprocessing(params: dict) -> None:
    """지원하지 않는 설정을 기본값이나 다른 계산 방식으로 조용히 바꾸지 않는다."""
    for key in ("window_size", "level", "hop_size"):
        if type(params[key]) is not int or params[key] <= 0:
            raise ValueError(f"{key}는 양의 정수여야 합니다.")
    if params["hop_size"] != 1:
        raise ValueError("현재 추론은 hop_size=1만 지원합니다.")
    pywt.Wavelet(params["wavelet"])
    if params["border_mode"] not in pywt.Modes.modes:
        raise ValueError("지원하지 않는 웨이브렛 경계 정책입니다.")
    if params["thresh_mode"] not in {"soft", "hard", "garrote", "greater", "less"}:
        raise ValueError("지원하지 않는 웨이브렛 임계 정책입니다.")
    if params["strategy"] not in {"adaptive", "fixed"}:
        raise ValueError("지원하지 않는 노이즈 임계 전략입니다.")
    if params["warmup_policy"] not in {"pad", "wait", "passthrough", "ratio"}:
        raise ValueError("지원하지 않는 워밍업 정책입니다.")
    ratio = float(params["min_fill_ratio"])
    if not np.isfinite(ratio) or not 0 < ratio <= 1:
        raise ValueError("min_fill_ratio는 0보다 크고 1 이하여야 합니다.")


def validate_components(components: dict) -> None:
    """입력 6차원·보정 행렬·인코더 순서를 확인해 서로 다른 묶음의 혼용을 감지한다."""
    for key, method in (("mlp", "predict"), ("mlp", "predict_proba"),
                        ("scaler", "transform"), ("label_encoder", "inverse_transform")):
        if not callable(getattr(components[key], method, None)):
            raise ValueError(f"{key}의 {method} 기능이 없습니다.")
    for key in ("mlp", "scaler"):
        if getattr(components[key], "n_features_in_", None) != 6:
            raise ValueError(f"{key}의 입력 차원이 6이 아닙니다.")
    bias = np.asarray([components["hard_iron"][axis] for axis in MAGNETIC_AXES], dtype=float)
    matrix = np.asarray(components["soft_iron"], dtype=float)
    if bias.shape != (3,) or matrix.shape != (3, 3):
        raise ValueError("보정 값 또는 행렬의 크기가 다릅니다.")
    if not np.isfinite(bias).all() or not np.isfinite(matrix).all():
        raise ValueError("보정 파일에 유한하지 않은 값이 있습니다.")
    labels = components["label_encoder"].classes_
    classes = components["mlp"].classes_
    # 기존 후보 계산은 확률 배열의 위치를 인코더 인덱스로 사용한다.
    if len(labels) == 0 or not np.array_equal(classes, np.arange(len(labels))):
        raise ValueError("모델 클래스와 인코더 인덱스가 일치하지 않습니다.")


def load_model_bundle(model_dir: Path = MODEL_DIR) -> ModelBundle:
    """필수 파일 전부를 읽고 검증한 뒤에만 묶음을 반환한다. 부분 로딩은 공개하지 않는다."""
    try:
        paths = {key: Path(model_dir) / name for key, name in MODEL_FILES.items()}
        missing = [key for key, path in paths.items() if not path.is_file()]
        if missing:
            raise ModelPreparationError(f"필수 모델 구성 누락: {', '.join(missing)}")
        components = {key: joblib.load(path) for key, path in paths.items()}
        params = {**DEFAULT_PARAMS, **components["preproc_params"]}
        validate_preprocessing(params)
        validate_components(components)
        fixed_sigma = None
        if params["strategy"] == "fixed":
            fixed_sigma = joblib.load(Path(model_dir) / "wavelet_sigma.pkl")
            for axis in MAGNETIC_AXES:
                threshold = float(fixed_sigma[axis]["uthresh"])
                if not np.isfinite(threshold) or threshold < 0:
                    raise ValueError("고정 임계 값이 유효하지 않습니다.")
        return ModelBundle(components, params, fixed_sigma)
    except ModelPreparationError:
        raise
    except Exception as exc:
        # 예외 원문에는 파일 경로·모델 내부 정보가 포함될 수 있어 외부 메시지를 고정한다.
        raise ModelPreparationError("모델 파일 로딩 또는 구조 검증에 실패했습니다.") from exc
