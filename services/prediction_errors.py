"""통신 방식과 독립적으로 모델 준비·입력·계산 실패를 구분한다."""


class ModelPreparationError(RuntimeError):
    """필수 모델 묶음 검증이나 시작 시 시험 예측이 실패했다."""


class ModelNotReadyError(RuntimeError):
    """시작 전·실패·종료 상태에서 예측을 요청했다."""


class InvalidSensorInputError(ValueError):
    """센서 입력 또는 보정 결과가 유한한 6개 수치로 표현되지 않는다."""


class PredictionFailedError(RuntimeError):
    """준비된 모델의 계산이 실패하거나 유효하지 않은 결과를 반환했다."""
