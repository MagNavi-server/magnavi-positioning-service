"""업무 DB와 독립적으로 FastAPI의 시작·종료에 모델 준비를 연결한다."""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from . import model_service
from .legacy_prediction_service import close_legacy_session


@asynccontextmanager
async def model_lifespan(app: FastAPI):
    """준비 실패 시 시작을 중단하고 정상·예외 종료 모두에서 모델과 세션을 정리한다."""
    model_service.initialize_model()
    try:
        yield
    finally:
        close_legacy_session()
        model_service.shutdown_model()
