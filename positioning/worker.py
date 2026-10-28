"""비동기 통신 루프 밖에서 추론하며 실행 중·대기 작업의 총수를 제한한다."""

import asyncio
from concurrent.futures import ThreadPoolExecutor

import grpc

from .protocol import ProtocolError


class InferenceWorkers:
    """취소된 RPC의 계산이 끝날 때까지도 작업 용량을 점유하게 한다."""

    def __init__(self, workers: int, pending: int):
        """실제 제출 가능한 작업 수를 제한해 실행기의 내부 무제한 큐를 보호한다."""
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="positioning")
        self._limit = workers + pending
        self._jobs = set()
        self._closing = False

    @property
    def outstanding(self):
        """취소 여부와 별개로 아직 완료하지 않은 계산 수를 반환한다."""
        return len(self._jobs)

    def _completed(self, future):
        """실제 계산 완료 이후에만 신규 요청이 쓸 용량을 돌려준다."""
        self._jobs.discard(future)

    @staticmethod
    def _consume_exception(future):
        """이미 취소된 호출의 늦은 실패가 미회수 예외 로그로 노출되지 않게 한다."""
        if not future.cancelled():
            future.exception()

    async def predict(self, runtime, sample, session, timeout: float):
        """한도 확인과 제출을 같은 루프에서 처리하고 계산을 통신 취소와 분리한다."""
        if self._closing or len(self._jobs) >= self._limit:
            raise ProtocolError(grpc.StatusCode.RESOURCE_EXHAUSTED, "Inference capacity exceeded.")
        loop = asyncio.get_running_loop()
        job = self._executor.submit(runtime.predict, sample, session)
        self._jobs.add(job)
        job.add_done_callback(lambda done: loop.call_soon_threadsafe(self._completed, done))
        future = asyncio.wrap_future(job)
        future.add_done_callback(self._consume_exception)
        try:
            # RPC 취소가 실행 중인 스레드를 중단했다고 오인하지 않도록 보호한다.
            return await asyncio.wait_for(asyncio.shield(future), timeout)
        finally:
            # 대기 작업이면 실행을 막고, 이미 실행 중이면 완료까지 용량을 유지한다.
            if not job.done():
                job.cancel()

    async def close(self):
        """새 제출을 차단하고 대기 계산을 취소한 뒤 실행 중인 계산을 회수한다."""
        self._closing = True
        for job in list(self._jobs):
            job.cancel()
        await asyncio.to_thread(self._executor.shutdown, wait=True, cancel_futures=True)
