from pydantic import BaseModel, ConfigDict, FiniteFloat
from typing import Optional, List

class SensorInput(BaseModel):
    """기존 6개 필드를 유지하며 NaN·무한대가 계산에 유입되지 않게 한다."""

    model_config = ConfigDict(validate_assignment=True)
    Mag_X: FiniteFloat
    Mag_Y: FiniteFloat
    Mag_Z: FiniteFloat
    Ori_X: FiniteFloat
    Ori_Y: FiniteFloat
    Ori_Z: FiniteFloat

class ModelOutput(BaseModel):
    """기존 HTTP의 위치 코드·점수·문자열 후보 응답을 유지한다."""

    prediction: int
    confidence: Optional[float] = None
    top_k_results: List[str]
