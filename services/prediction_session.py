"""한 측정 연결의 센서 이력과 종료 상태를 관리한다."""

from collections import deque
from contextlib import contextmanager
from threading import Lock
from typing import Iterator, Mapping


MAGNETIC_AXES = ("Mag_X", "Mag_Y", "Mag_Z")


class PredictionSessionClosedError(RuntimeError):
    """종료된 측정의 입력이나 계산 결과를 재사용하려 할 때 발생한다."""


class PredictionSession:
    """공유 모델과 별도로, 한 측정 연결의 자기장 윈도우만 소유한다."""

    def __init__(self, window_size: int):
        """빈 윈도우로 시작하며 길이와 종료 상태는 세션 수명 동안 유지한다."""
        if type(window_size) is not int or window_size <= 0:
            raise ValueError("window_size는 양의 정수여야 합니다.")
        self._window_size = window_size
        self._buffers = {axis: deque(maxlen=window_size) for axis in MAGNETIC_AXES}
        self._closed = False
        # 한 세션의 보정·윈도우 갱신·추론 전체가 서로 끼어들지 않도록 한다.
        self._prediction_lock = Lock()
        # 종료는 긴 계산을 기다리지 않고 상태를 바꿔야 하므로 별도 잠금을 쓴다.
        self._state_lock = Lock()

    @property
    def window_size(self) -> int:
        """모델 설정과 세션의 윈도우 길이가 일치하는지 확인할 값을 반환한다."""
        return self._window_size

    @property
    def buffered_count(self) -> int:
        """현재 보관한 샘플 수를 반환하며 종료 뒤에는 0이 된다."""
        with self._state_lock:
            return min(len(buffer) for buffer in self._buffers.values())

    @property
    def closed(self) -> bool:
        """호출자가 연결 종료 상태를 확인할 수 있도록 읽기 전용 값을 제공한다."""
        with self._state_lock:
            return self._closed

    def _ensure_open(self) -> None:
        """종료된 세션의 새 작업과 완료 직전 결과를 거절한다."""
        with self._state_lock:
            if self._closed:
                raise PredictionSessionClosedError("종료된 측정 세션입니다.")

    @contextmanager
    def prediction(self) -> Iterator[None]:
        """계산을 직렬화하며 실패한 입력의 이력과 종료된 세션의 결과를 남기지 않는다."""
        with self._prediction_lock:
            self._ensure_open()
            with self._state_lock:
                previous = {axis: tuple(buffer) for axis, buffer in self._buffers.items()}
            try:
                yield
                # CPU 계산은 강제로 중단하지 않는다. 종료됐으면 완료 결과도 버린다.
                self._ensure_open()
            except Exception:
                with self._state_lock:
                    if not self._closed:
                        for axis, values in previous.items():
                            self._buffers[axis].clear()
                            self._buffers[axis].extend(values)
                raise

    def append_magnetic_sample(self, sample: Mapping[str, float]) -> None:
        """보정한 세 축을 함께 갱신하며 일부 축만 추가되는 것을 막는다."""
        values = {axis: sample[axis] for axis in MAGNETIC_AXES}
        with self._state_lock:
            if self._closed:
                raise PredictionSessionClosedError("종료된 측정 세션입니다.")
            for axis, value in values.items():
                self._buffers[axis].append(value)

    def window_values(self, axis: str) -> tuple[float, ...]:
        """계산용 사본을 반환해 호출자가 내부 버퍼를 직접 변경하지 못하게 한다."""
        with self._state_lock:
            if self._closed:
                raise PredictionSessionClosedError("종료된 측정 세션입니다.")
            return tuple(self._buffers[axis])

    def close(self) -> None:
        """반복 종료를 허용하고 즉시 이력을 비운다. 재접속은 새 객체로 시작한다."""
        with self._state_lock:
            self._closed = True
            for buffer in self._buffers.values():
                buffer.clear()
