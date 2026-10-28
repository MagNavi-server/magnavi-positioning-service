"""통신 수치와 모델 결과를 검사하며 기존 계산과 protobuf 표현을 연결한다."""

import math
from uuid import UUID

import grpc
from pydantic import ValidationError

from magnavi.positioning.v1 import positioning_pb2 as messages
from schemas.prediction import SensorInput
from services.prediction_errors import PredictionFailedError


CONTRACT_VERSION = 2


class ProtocolError(Exception):
    """센서 원문 없이 외부에 전달할 상태와 고정 안내를 담는다."""

    def __init__(self, code: grpc.StatusCode, detail: str):
        """오류 원문 대신 호출자가 정한 안전한 문구만 저장한다."""
        super().__init__(detail)
        self.code = code
        self.detail = detail


def validate_start(request, config):
    """첫 요청의 세션·계약·모델·센서 규격을 확인한다."""
    if request.WhichOneof("payload") != "start":
        raise ProtocolError(grpc.StatusCode.INVALID_ARGUMENT, "First request must be start.")
    start = request.start
    try:
        if str(UUID(start.session_id)) != start.session_id:
            raise ValueError
    except ValueError:
        raise ProtocolError(grpc.StatusCode.INVALID_ARGUMENT, "Invalid measurement ID.") from None
    if (start.contract_version != CONTRACT_VERSION or start.model_key != config.model_key
            or start.model_version != config.model_version or start.sensor_profile_id != config.sensor_profile_id):
        raise ProtocolError(grpc.StatusCode.FAILED_PRECONDITION, "Unsupported model or contract.")
    return start


def sensor_input(request, expected_sequence: int, previous_elapsed: int) -> SensorInput:
    """필수 값의 존재·연속 순번·경과 시간·유한 수치를 버퍼 변경 전에 검사한다."""
    if request.WhichOneof("payload") != "sample":
        raise ProtocolError(grpc.StatusCode.INVALID_ARGUMENT, "Expected a sensor sample.")
    sample = request.sample
    present = all(sample.HasField(name) for name in ("sequence", "elapsed_nanos", "magnetic", "orientation"))
    present = present and all(vector.HasField(axis) for vector in (sample.magnetic, sample.orientation) for axis in ("x", "y", "z"))
    if not present or sample.sequence != expected_sequence:
        raise ProtocolError(grpc.StatusCode.INVALID_ARGUMENT, "Missing fields or invalid sequence.")
    if (expected_sequence == 1 and sample.elapsed_nanos != 0) or sample.elapsed_nanos <= previous_elapsed:
        raise ProtocolError(grpc.StatusCode.INVALID_ARGUMENT, "Invalid elapsed time.")
    try:
        return SensorInput(
            Mag_X=sample.magnetic.x, Mag_Y=sample.magnetic.y, Mag_Z=sample.magnetic.z,
            Ori_X=sample.orientation.x, Ori_Y=sample.orientation.y, Ori_Z=sample.orientation.z,
        )
    except ValidationError:
        raise ProtocolError(grpc.StatusCode.INVALID_ARGUMENT, "Finite sensor values are required.") from None


def prediction_event(result, session_id: str, sequence: int, config, allowed_codes: frozenset[str]):
    """정수 위치 코드를 문자열로 바꾸고 HTTP용 빈 후보를 제외해 전송한다."""
    prediction, _, response_candidates = result
    candidates = []
    seen = set()
    for code, score in response_candidates:
        location = str(code)
        # 기존 HTTP의 부족한 후보 채우기는 이 모델의 실제 위치 후보가 아니다.
        if code == 0 and score == 0.0 and location not in allowed_codes:
            continue
        if location not in allowed_codes or not math.isfinite(score) or not 0 <= score <= 1:
            raise PredictionFailedError("유효하지 않은 예측 후보입니다.")
        if location in seen:
            raise PredictionFailedError("예측 후보 코드가 중복되었습니다.")
        seen.add(location)
        candidates.append(messages.Candidate(location_code=location, score=score))
    if (prediction is None or not 1 <= len(candidates) <= 3
            or str(prediction) != candidates[0].location_code
            or any(left.score < right.score for left, right in zip(candidates, candidates[1:]))):
        raise PredictionFailedError("예측과 최상위 후보가 일치하지 않습니다.")
    return messages.StreamEvent(session_id=session_id, prediction=messages.Prediction(
        sequence=sequence, model_key=config.model_key, model_version=config.model_version,
        location_code=str(prediction), score=candidates[0].score, candidates=candidates,
    ))
