"""업무 DB와 독립적으로 FastAPI의 시작·종료에 모델 준비를 연결한다."""

from contextlib import asynccontextmanager
import os
from pathlib import Path

from fastapi import FastAPI

from . import model_service
from .legacy_prediction_service import close_legacy_session


@asynccontextmanager
async def model_lifespan(app: FastAPI):
    """준비 실패 시 시작을 중단하고 정상·예외 종료 모두에서 모델과 세션을 정리한다."""
    config_path = os.environ.get("POSITIONING_CONFIG")
    config = None
    if config_path:
        from positioning.config import StreamConfig

        config = StreamConfig.read(Path(config_path))
        config.verify_model_files(model_service.MODEL_DIR)
    model_service.initialize_model()
    server = None
    app.state.positioning_server = None
    try:
        if config is not None:
            from positioning.server import PositioningServer

            server = PositioningServer(model_service.get_model_runtime(), config)
            await server.start()
            app.state.positioning_server = server
        yield
    finally:
        try:
            if server is not None:
                await server.close()
        finally:
            # 통신 종료 오류가 있더라도 기존 HTTP 세션과 모델 준비 상태를 정리한다.
            app.state.positioning_server = None
            close_legacy_session()
            model_service.shutdown_model()
