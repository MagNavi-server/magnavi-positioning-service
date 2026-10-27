"""검증된 모델 묶음과 측정 세션으로 기존 전처리·추론 계산을 수행한다."""

from pathlib import Path
from typing import Optional, Tuple

import numpy as np
import pywt

from schemas.prediction import SensorInput
from .model_loader import MODEL_DIR, ModelBundle
from .model_runtime import ModelRuntime
from .prediction_errors import InvalidSensorInputError, PredictionFailedError
from .prediction_session import MAGNETIC_AXES, PredictionSession


MAG_COLS = list(MAGNETIC_AXES)
ORI_COLS = ["Ori_X", "Ori_Y", "Ori_Z"]
FEATURE_COLS = MAG_COLS + ORI_COLS
# 객체 생성은 파일을 읽지 않는다. 서버 lifespan 또는 검증 도구가 명시적으로 준비한다.
_runtime = ModelRuntime()


def initialize_model(model_dir: Path = MODEL_DIR) -> None:
    """공용 모델을 명시적으로 준비하며 실패하면 호출자에게 예외를 전달한다."""
    _runtime.start(model_dir)


def shutdown_model() -> None:
    """모델의 신규 사용을 막고 연결된 세션을 정리한다."""
    _runtime.stop()


def get_model_status() -> dict:
    """복사된 전역 불리언 대신 실제 런타임의 현재 상태를 조회한다."""
    return _runtime.status()


def create_prediction_session() -> PredictionSession:
    """준비된 모델의 윈도우 크기로 측정 연결 하나에 쓸 빈 세션을 생성한다."""
    return _runtime.create_session()


def run_prediction(data: SensorInput, session: PredictionSession) -> Tuple[Optional[int], list, list]:
    """입력 검증과 준비 상태·세션 수명을 확인한 후 기존 계산을 실행한다."""
    return _runtime.predict(data, session)


def extract_location_id(label_str):
    """기존 라벨의 위치 코드를 추출하며 기존 실패 시 0 반환 규칙을 유지한다."""
    try:
        return int(str(label_str).split("_")[0])
    except (ValueError, IndexError):
        try:
            return int(label_str)
        except Exception:
            return 0

def _wavelet_denoise_window(x: np.ndarray, col_name: str, bundle: ModelBundle) -> np.ndarray:
    """기존 설정과 계산 순서를 유지해 한 축의 자기장 윈도우를 필터링한다."""
    wavelet = bundle.preprocessing["wavelet"]
    level = int(bundle.preprocessing["level"])
    mode = bundle.preprocessing["thresh_mode"]
    border = bundle.preprocessing["border_mode"]
    coeffs = pywt.wavedec(x, wavelet=wavelet, level=level, mode=border)

    if bundle.preprocessing.get("strategy", "adaptive") == "fixed" and bundle.fixed_sigma and bundle.fixed_sigma.get(col_name):
        uthresh = float(bundle.fixed_sigma[col_name].get("uthresh", 0.0))
        if uthresh <= 0: return x
    else:
        d = coeffs[-1]
        if len(d) == 0: return x
        sigma = np.median(np.abs(d)) / 0.6745
        if sigma == 0: return x
        uthresh = sigma * np.sqrt(2 * np.log(len(x)))

    den = [coeffs[0]] + [pywt.threshold(c, value=uthresh, mode=mode) for c in coeffs[1:]]
    y = pywt.waverec(den, wavelet=wavelet, mode=border)
    return y[:len(x)]

def _latest_value_with_warmup(col_name: str, session: PredictionSession, bundle: ModelBundle) -> float:
    """해당 세션의 윈도우에 기존 워밍업 정책을 적용해 최신 값을 계산한다."""
    window_size = int(bundle.preprocessing["window_size"])
    policy = bundle.preprocessing.get("warmup_policy", "pad")
    ratio = float(bundle.preprocessing.get("min_fill_ratio", 0.5))
    buf = np.array(session.window_values(col_name), dtype=float)
    n = len(buf)

    if (policy == "wait" and n < window_size) or \
       (policy == "passthrough" and n < window_size) or \
       (policy == "ratio" and n < max(1, int(window_size * ratio))):
        return float(buf[-1]) if n > 0 else 0.0

    if n < window_size:
        x = np.pad(buf, (0, window_size - n), mode="edge") if n > 0 else np.zeros(window_size)
    else:
        x = buf[-window_size:]

    y = _wavelet_denoise_window(x, col_name, bundle)
    return float(y[-1])

def _predict_in_session(
    data: SensorInput, session: PredictionSession, bundle: ModelBundle
) -> Tuple[Optional[int], list, list]:
    """세션 잠금 안에서 기존 보정·웨이브렛·스케일링·추론 순서를 실행한다."""
    models = bundle.components
    sample = data.model_dump()

    for c in MAG_COLS:
        sample[c] -= float(models["hard_iron"][c])

    mag_vec = np.array([sample[c] for c in MAG_COLS], dtype=float)
    mag_vec = models["soft_iron"] @ mag_vec
    for i, c in enumerate(MAG_COLS):
        sample[c] = mag_vec[i]

    if not np.isfinite(mag_vec).all():
        raise InvalidSensorInputError("보정 결과를 유한한 수치로 표현할 수 없습니다.")
    session.append_magnetic_sample(sample)

    window_size = int(bundle.preprocessing["window_size"])
    policy = bundle.preprocessing.get("warmup_policy", "pad")
    n_now = session.buffered_count

    if policy == "wait" and n_now < window_size:
        return None, [], []

    latest = {c: _latest_value_with_warmup(c, session, bundle) for c in MAG_COLS}
    
    feature_vec = np.array([latest[c] for c in MAG_COLS] + [sample[c] for c in ORI_COLS]).reshape(1, -1)
    
    if not np.isfinite(feature_vec).all():
        raise InvalidSensorInputError("전처리 결과를 유한한 수치로 표현할 수 없습니다.")
    scaled = models["scaler"].transform(feature_vec)
    if not np.isfinite(scaled).all():
        raise InvalidSensorInputError("변환한 입력을 유한한 수치로 표현할 수 없습니다.")
    
    mlp = models["mlp"]
    pred_idx = mlp.predict(scaled)[0]
    
    raw_pred = models["label_encoder"].inverse_transform([pred_idx])[0]
    final_pred = extract_location_id(raw_pred)

    top_results_for_logging, top_results_for_response = [], []
    if hasattr(mlp, "predict_proba"):
        proba = mlp.predict_proba(scaled)[0]
        if not np.isfinite(proba).all() or np.any(proba < 0) or np.any(proba > 1):
            raise PredictionFailedError("모델이 유효하지 않은 후보 점수를 반환했습니다.")
        num_candidates = min(len(proba), 15)
        top_indices = np.argsort(-proba)[:num_candidates]
        raw_top = models["label_encoder"].inverse_transform(top_indices)
        top_probs = proba[top_indices]

        seen = set()
        uniq = []
        for lbl, p in zip(raw_top, top_probs):
            loc = extract_location_id(lbl)
            if loc not in seen:
                seen.add(loc)
                uniq.append((loc, float(p)))
                if len(uniq) >= 3:
                    break
        while len(uniq) < 3:
            uniq.append((0, 0.0))
        
        top_results_for_logging = uniq[:3]
        top_results_for_response = uniq[:3]

    return final_pred, top_results_for_logging, top_results_for_response
