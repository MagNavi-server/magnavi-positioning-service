"""기존 HTTP 예측 형식을 유지하면서 입력·준비·계산 실패를 구분한다."""

import logging

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

import schemas
from services import run_legacy_prediction
from services.prediction_errors import InvalidSensorInputError, ModelNotReadyError, PredictionFailedError
from services.prediction_session import PredictionSessionClosedError


logger = logging.getLogger(__name__)


class PredictionRoute(APIRoute):
    """예측 입력 오류만 안전하게 직렬화하고 다른 업무 API의 오류 처리는 유지한다."""

    def get_route_handler(self):
        """NaN·무한대 원본을 오류 응답에 다시 넣어 JSON 직렬화가 실패하지 않게 한다."""
        original_handler = super().get_route_handler()

        async def handle(request: Request):
            """필드 위치·오류 종류만 전달하고 센서 원본과 내부 검증 문맥을 제외한다."""
            try:
                return await original_handler(request)
            except RequestValidationError as exc:
                errors = [{key: error[key] for key in ("type", "loc", "msg")} for error in exc.errors()]
                return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, content={"detail": errors})

        return handle


router = APIRouter(route_class=PredictionRoute)


@router.post("/predict", response_model=schemas.ModelOutput, summary="센서 데이터로 위치 예측")
def predict(data: schemas.SensorInput, response: Response):
    """정상 결과 필드를 유지하고 미준비·워밍업·입력·계산 실패의 HTTP 상태를 구분한다."""
    try:
        # 측정 식별자가 없는 기존 HTTP 경로의 공유 세션 제약은 계속 남아 있다.
        prediction_result, log_results, response_results = run_legacy_prediction(data)
    except (ModelNotReadyError, PredictionSessionClosedError):
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return schemas.ModelOutput(
            prediction=-1, confidence=0.0,
            top_k_results=["Error: Model components are not loaded.", "", ""],
        )
    except InvalidSensorInputError:
        raise HTTPException(status_code=422, detail="Sensor values cannot be processed as finite numbers.") from None
    except PredictionFailedError:
        # 예외 원문·센서·위치 후보를 로그나 응답에 노출하지 않는다.
        logger.error("prediction_failed")
        raise HTTPException(status_code=500, detail="Prediction failed.") from None

    if prediction_result is None:
        response.status_code = status.HTTP_202_ACCEPTED
        return schemas.ModelOutput(
            prediction=-2, confidence=0.0,
            top_k_results=["Status: Model is warming up. Please wait.", "", ""],
        )

    confidence_score = log_results[0][1] if log_results else None
    top_k_list = [f"{code} ({score:.4f})" for code, score in response_results]
    while len(top_k_list) < 3:
        top_k_list.append("")
    return schemas.ModelOutput(
        prediction=prediction_result, confidence=confidence_score, top_k_results=top_k_list,
    )
