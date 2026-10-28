"""서버 자원 한도와 명시적으로 등록한 모델·센서 규격을 읽는다."""

from dataclasses import dataclass
import hashlib
import ipaddress
import json
import math
from pathlib import Path

from services.model_loader import MODEL_FILES


@dataclass(frozen=True)
class StreamConfig:
    """한 프로세스에서 제공할 모델 묶음과 제한된 실행 자원을 정의한다."""

    model_key: str
    model_version: str
    sensor_profile_id: str
    model_sha256: dict[str, str]
    host: str = "127.0.0.1"
    port: int = 50051
    workers: int = 1
    pending_jobs: int = 8
    max_streams: int = 8
    max_message_bytes: int = 65536
    start_timeout: float = 3.0
    idle_timeout: float = 30.0
    prediction_timeout: float = 5.0
    write_timeout: float = 5.0

    def __post_init__(self):
        """불완전한 식별자·무제한 자원·비공개 설정 없는 외부 바인딩을 거절한다."""
        for name, limit in (("model_key", 128), ("model_version", 64), ("sensor_profile_id", 64)):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or len(value) > limit:
                raise ValueError(f"{name}가 유효하지 않습니다.")
        # 실제 센서 규격과 TLS가 미확정이므로 이번 실행 진입점은 로컬 검증에 한정한다.
        if not ipaddress.ip_address(self.host).is_loopback:
            raise ValueError("현재 서버는 루프백 주소에서만 실행할 수 있습니다.")
        if type(self.port) is not int or not 0 <= self.port <= 65535:
            raise ValueError("port가 유효하지 않습니다.")
        for name in ("workers", "max_streams", "max_message_bytes"):
            if type(getattr(self, name)) is not int or getattr(self, name) <= 0:
                raise ValueError(f"{name}는 양의 정수여야 합니다.")
        if type(self.pending_jobs) is not int or self.pending_jobs < 0:
            raise ValueError("pending_jobs는 0 이상의 정수여야 합니다.")
        for name in ("start_timeout", "idle_timeout", "prediction_timeout", "write_timeout"):
            value = getattr(self, name)
            if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name}는 유한한 양수여야 합니다.")
        if set(self.model_sha256) != set(MODEL_FILES.values()):
            raise ValueError("모델 묶음의 필수 파일 6개 해시를 지정해야 합니다.")

    def verify_model_files(self, directory: Path) -> None:
        """등록한 모델 버전과 다른 파일 조합으로 응답하지 않도록 확인한다."""
        for name, expected in self.model_sha256.items():
            actual = hashlib.sha256((directory / name).read_bytes()).hexdigest()
            if actual != expected:
                raise ValueError("등록한 모델 파일 해시와 실제 파일이 다릅니다.")

    @classmethod
    def read(cls, path: Path):
        """환경별 명시적 JSON을 읽으며 알 수 없는 필드를 조용히 무시하지 않는다."""
        return cls(**json.loads(path.read_text(encoding="utf-8")))
