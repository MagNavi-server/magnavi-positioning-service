"""HTTP 실행 여부와 실제 모델 gRPC 준비 여부를 구분한다."""

from fastapi import APIRouter, Request, Response

from services.model_service import get_model_status


router = APIRouter()


@router.get("/health/live")
def live():
    """HTTP 요청을 처리할 수 있는지 확인한다."""
    return {"status": "ok"}


@router.get("/health/ready")
def ready(request: Request, response: Response):
    """파일·시험 추론과 gRPC 바인딩까지 완료해야 위치 서비스 준비로 표시한다."""
    server = getattr(request.app.state, "positioning_server", None)
    model_ready = get_model_status()["model_loaded"]
    grpc_ready = server is not None and server.ready
    response.status_code = 200 if model_ready and grpc_ready else 503
    return {"model_ready": model_ready, "grpc_ready": grpc_ready}
