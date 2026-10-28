from fastapi import FastAPI
from contextlib import asynccontextmanager
import logging
from database import engine, Base
# 새로 만든 prediction_router를 임포트 목록에 추가합니다.
from routers import favorites_router, locations_router, user_router, prediction_router, outdoor_places_router
from services.model_lifecycle import model_lifespan
from routers.health_router import router as health_router

logging.basicConfig(level=logging.INFO, format='%(levelname)s:     %(message)s')

@asynccontextmanager
async def application_lifespan(application: FastAPI):
    """모델 준비 후 기존 DB 초기화를 수행하며 시작 실패 때도 모델 자원을 정리한다."""
    async with model_lifespan(application):
        # 업무 기능 이관 전까지 초기화는 유지하되 import만으로 DB에 쓰지 않는다.
        Base.metadata.create_all(bind=engine)
        yield

app = FastAPI(
    title="Midas API - Refactored",
    description="전체 프로젝트 구조가 개선된 API 서버입니다.",
    version="10.0.0",
    lifespan=application_lifespan,
)

# --- 라우터 등록 ---
# 모든 API 엔드포인트들을 이곳에서 깔끔하게 등록하고 관리합니다.
app.include_router(user_router.router, prefix="/users", tags=["users"])
app.include_router(favorites_router.router, prefix="/favorites", tags=["favorites"])
app.include_router(locations_router.router, prefix="/locations", tags=["Predicted Locations"])
# 새로 만든 예측 라우터를 등록합니다. prefix가 없으므로 경로가 바로 /predict가 됩니다.
app.include_router(prediction_router.router, tags=["Prediction"])
app.include_router(health_router, tags=["Health"])

# @app.post("/predict") ... 로 시작하던 긴 함수 블록 전체가 삭제됩니다.


app.include_router(
    outdoor_places_router.router, 
    prefix="/outdoor-places", 
    tags=["Outdoor Places"]
)

