"""모델 준비와 종료를 관리하고 검증된 묶음만 추론 요청에 제공한다."""

from collections import Counter
import logging
from pathlib import Path
from threading import Lock
import warnings
from weakref import WeakSet

from pydantic import ValidationError

from schemas.prediction import SensorInput
from .model_loader import MODEL_DIR, ModelBundle, load_model_bundle
from .prediction_errors import (
    InvalidSensorInputError, ModelNotReadyError, ModelPreparationError, PredictionFailedError,
)
from .prediction_session import PredictionSession, PredictionSessionClosedError


logger = logging.getLogger(__name__)


class ModelRuntime:
    """모델 하나의 준비 상태와 해당 모델에서 생성한 세션의 수명을 소유한다."""

    def __init__(self):
        """파일을 읽지 않는 미준비 상태로 생성한다."""
        self._bundle: ModelBundle | None = None
        self._state = "not_started"
        self._startup_warnings = []
        self._sessions = WeakSet()
        self._state_lock = Lock()
        # 느린 준비·종료끼리는 직렬화하되 상태 조회가 모델 로딩을 기다리지는 않는다.
        self._lifecycle_lock = Lock()

    def start(self, model_dir: Path = MODEL_DIR) -> None:
        """전체 검증과 시험 예측 성공 후에만 준비 상태를 한 번에 공개한다."""
        with self._lifecycle_lock:
            with self._state_lock:
                if self._state == "ready":
                    return
                self._state = "loading"
                self._startup_warnings = []
            try:
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter("always")
                    bundle = load_model_bundle(model_dir)
                    self._check_prediction(bundle)
            except Exception as exc:
                with self._state_lock:
                    self._bundle = None
                    self._state = "failed"
                raise ModelPreparationError("모델 준비 검증에 실패하여 시작을 중단합니다.") from exc
            counts = Counter(type(item.message).__name__ for item in caught)
            warning_summary = [{"category": name, "count": count} for name, count in sorted(counts.items())]
            for item in warning_summary:
                # 기존 경고를 숨기지 않되 센서·위치·모델 내부 값은 로그에 남기지 않는다.
                logger.warning("model_preparation_warning category=%s count=%d", item["category"], item["count"])
            with self._state_lock:
                self._bundle = bundle
                self._startup_warnings = warning_summary
                self._state = "ready"

    def _check_prediction(self, bundle: ModelBundle) -> None:
        """독립된 합성 측정으로 실제 전처리·추론을 확인하고 시험 이력을 폐기한다."""
        from .model_service import _predict_in_session

        session = PredictionSession(bundle.preprocessing["window_size"])
        sample = SensorInput(Mag_X=10.0, Mag_Y=-5.0, Mag_Z=40.0, Ori_X=0.0, Ori_Y=0.0, Ori_Z=0.0)
        try:
            # wait 정책도 실제 예측까지 검사하도록 윈도우 하나를 채운다.
            for _ in range(session.window_size):
                with session.prediction():
                    result = _predict_in_session(sample, session, bundle)
            if result[0] is None:
                raise ModelPreparationError("시험 예측이 워밍업을 완료하지 못했습니다.")
        finally:
            session.close()

    def stop(self) -> None:
        """신규 요청을 차단하고 기존 세션을 닫아 종료 중 계산의 결과를 폐기한다."""
        with self._lifecycle_lock:
            with self._state_lock:
                self._state = "stopped"
                self._bundle = None
                sessions = list(self._sessions)
                self._sessions.clear()
            for session in sessions:
                session.close()

    def _require_ready(self) -> ModelBundle:
        """상태 잠금 안에서만 호출하며 미준비 상태의 계산 진입을 차단한다."""
        if self._state != "ready" or self._bundle is None:
            raise ModelNotReadyError("모델이 예측을 제공할 준비가 되지 않았습니다.")
        return self._bundle

    def create_session(self) -> PredictionSession:
        """현재 준비된 모델에 속한 빈 세션을 만들고 종료 시 정리할 대상으로 등록한다."""
        with self._state_lock:
            bundle = self._require_ready()
            session = PredictionSession(bundle.preprocessing["window_size"])
            self._sessions.add(session)
            return session

    def predict(self, data: SensorInput, session: PredictionSession):
        """입력을 재검증하고 현재 모델 소유의 세션에만 계산 결과를 반환한다."""
        from .model_service import _predict_in_session

        with self._state_lock:
            bundle = self._require_ready()
            if session.window_size != bundle.preprocessing["window_size"]:
                raise ValueError("세션과 모델의 윈도우 크기가 다릅니다.")
            if session not in self._sessions:
                raise ModelNotReadyError("현재 모델에서 만든 세션이 아닙니다.")
        try:
            # model_construct 등 HTTP 검증을 우회한 내부 호출도 버퍼 갱신 전에 검사한다.
            validated = SensorInput.model_validate(data.model_dump())
        except (ValidationError, AttributeError, TypeError) as exc:
            raise InvalidSensorInputError("유한한 센서 수치 6개가 필요합니다.") from exc
        with session.prediction():
            try:
                result = _predict_in_session(validated, session, bundle)
            except (InvalidSensorInputError, PredictionSessionClosedError, PredictionFailedError):
                raise
            except Exception as exc:
                raise PredictionFailedError("모델 계산에 실패했습니다.") from exc
            with self._state_lock:
                if self._bundle is not bundle or self._state != "ready":
                    raise ModelNotReadyError("계산 중 모델이 종료되었습니다.")
            return result

    def status(self) -> dict:
        """현재 모델 준비 상태를 반환하며 아직 없는 gRPC 준비 상태와 구분한다."""
        with self._state_lock:
            ready = self._state == "ready"
            params = dict(self._bundle.preprocessing) if self._bundle else {}
            return {
                "status": "ok" if ready else "error", "state": self._state,
                "model_loaded": ready,
                "model_name": "MLP 6-input (Hard/Soft-Iron + Windowed Wavelet + Scaler)",
                "feature_cols": ["Mag_X", "Mag_Y", "Mag_Z", "Ori_X", "Ori_Y", "Ori_Z"] if ready else None,
                "wavelet_strategy": params.get("strategy"), "window_size": params.get("window_size"),
                "warmup_policy": params.get("warmup_policy"), "preprocessing": params,
                "fixed_sigma_loaded": bool(self._bundle and self._bundle.fixed_sigma is not None),
                "startup_warnings": [dict(item) for item in self._startup_warnings],
            }
