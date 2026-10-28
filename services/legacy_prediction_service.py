"""측정 식별자가 없는 기존 HTTP API의 이관 기간 호환 처리를 담당한다."""

from threading import Lock
from typing import Optional, Tuple

from schemas.prediction import SensorInput
from . import model_service
from .prediction_session import PredictionSession


_legacy_session: Optional[PredictionSession] = None
_initialization_lock = Lock()


def run_legacy_prediction(data: SensorInput) -> Tuple[Optional[int], list, list]:
    """기존 HTTP의 누적 예측을 유지한다. 이 경로는 여러 사용자를 구분하지 못한다."""
    global _legacy_session
    with _initialization_lock:
        if _legacy_session is None or _legacy_session.closed:
            _legacy_session = model_service.create_prediction_session()
        session = _legacy_session
    # 공유 세션은 호환 경로에만 둔다. 새 gRPC 연결은 각각 새 세션을 소유해야 한다.
    return model_service.run_prediction(data, session)


def close_legacy_session() -> None:
    """서버 종료 시 공유 이력을 비워 다음 시작에서 재사용하지 않는다."""
    global _legacy_session
    with _initialization_lock:
        if _legacy_session is not None:
            _legacy_session.close()
            _legacy_session = None
