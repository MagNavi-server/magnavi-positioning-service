"""측정 연결마다 세션 하나를 소유하는 제한된 양방향 gRPC 서버다."""

import asyncio
import logging

import grpc

from magnavi.positioning.v1 import positioning_pb2 as messages
from magnavi.positioning.v1 import positioning_pb2_grpc as transport
from services.prediction_errors import InvalidSensorInputError, ModelNotReadyError, PredictionFailedError
from services.prediction_session import PredictionSessionClosedError
from .protocol import CONTRACT_VERSION, ProtocolError, prediction_event, sensor_input, validate_start
from .worker import InferenceWorkers


logger = logging.getLogger(__name__)


class PositioningService(transport.PositioningServiceServicer):
    """순서대로 한 입력씩 계산·전송하며 다른 측정의 버퍼를 공유하지 않는다."""

    def __init__(self, runtime, config, workers):
        """이미 준비된 모델과 한도 있는 작업자를 연결하고 지원 코드를 검증한다."""
        self.runtime = runtime
        self.config = config
        self.workers = workers
        self.allowed_codes = runtime.streaming_location_codes()
        self.sessions = {}
        self.accepting = False

    async def _write(self, context, event):
        """읽지 않는 상대 때문에 연결과 결과가 무한히 남지 않도록 전송 시간을 제한한다."""
        if context.cancelled():
            raise asyncio.CancelledError
        await asyncio.wait_for(context.write(event), self.config.write_timeout)

    async def PredictStream(self, request_iterator, context):
        """준비 확인 후 첫 샘플부터 예측하고 모든 종료 경로에서 세션을 닫는다."""
        session = None
        session_id = None
        try:
            if not self.accepting:
                raise ProtocolError(grpc.StatusCode.UNAVAILABLE, "Model service is not ready.")
            try:
                first = await asyncio.wait_for(anext(request_iterator), self.config.start_timeout)
            except StopAsyncIteration:
                raise ProtocolError(grpc.StatusCode.INVALID_ARGUMENT, "Start request is required.") from None
            start = validate_start(first, self.config)
            if start.session_id in self.sessions:
                raise ProtocolError(grpc.StatusCode.FAILED_PRECONDITION, "Measurement ID is already active.")
            session = self.runtime.create_session()
            session_id = start.session_id
            self.sessions[session_id] = session
            await self._write(context, messages.StreamEvent(session_id=session_id, ready=messages.StreamReady(
                contract_version=CONTRACT_VERSION, model_key=self.config.model_key,
                model_version=self.config.model_version, sensor_profile_id=self.config.sensor_profile_id,
                required_count=16,
            )))
            sequence, elapsed = 1, -1
            while True:
                try:
                    request = await asyncio.wait_for(anext(request_iterator), self.config.idle_timeout)
                except StopAsyncIteration:
                    return
                sample = sensor_input(request, sequence, elapsed)
                result = await self.workers.predict(
                    self.runtime, sample, session, self.config.prediction_timeout,
                )
                # 계산 완료 후에도 종료된 연결의 결과가 전송되지 않도록 다시 검사한다.
                if session.closed or not self.accepting:
                    raise ProtocolError(grpc.StatusCode.CANCELLED, "Measurement closed.")
                event = prediction_event(result, session_id, sequence, self.config, self.allowed_codes)
                await self._write(context, event)
                elapsed = request.sample.elapsed_nanos
                sequence += 1
        except ProtocolError as error:
            await context.abort(error.code, error.detail)
        except TimeoutError:
            await context.abort(grpc.StatusCode.DEADLINE_EXCEEDED, "Measurement operation timed out.")
        except InvalidSensorInputError:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Invalid sensor input.")
        except ModelNotReadyError:
            await context.abort(grpc.StatusCode.UNAVAILABLE, "Model is not ready.")
        except PredictionSessionClosedError:
            await context.abort(grpc.StatusCode.CANCELLED, "Measurement closed.")
        except PredictionFailedError:
            await context.abort(grpc.StatusCode.INTERNAL, "Prediction failed.")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("positioning_stream_failed")
            await context.abort(grpc.StatusCode.INTERNAL, "Measurement failed.")
        finally:
            if session is not None:
                session.close()
                if self.sessions.get(session_id) is session:
                    self.sessions.pop(session_id)


class PositioningServer:
    """같은 이벤트 루프에서 gRPC와 계산 작업자의 시작·종료를 관리한다."""

    def __init__(self, runtime, config):
        """모델 검증 후 메시지 크기·활성 RPC 수 한도를 가진 서버를 구성한다."""
        self.config = config
        self.runtime = runtime
        runtime.streaming_location_codes()
        self.workers = InferenceWorkers(config.workers, config.pending_jobs)
        self.service = PositioningService(runtime, config, self.workers)
        self.server = grpc.aio.server(
            maximum_concurrent_rpcs=config.max_streams,
            options=[("grpc.max_receive_message_length", config.max_message_bytes),
                     ("grpc.max_send_message_length", config.max_message_bytes)],
        )
        transport.add_PositioningServiceServicer_to_server(self.service, self.server)
        self.port = None
        self._closed = False

    @property
    def ready(self):
        """모델 검증과 실제 gRPC 시작이 모두 완료된 경우만 준비 상태를 반환한다."""
        return self.service.accepting and self.runtime.status()["model_loaded"]

    async def start(self):
        """로컬 주소에 바인딩하고 서버 시작 성공 이후에만 요청을 허용한다."""
        host = f"[{self.config.host}]" if ":" in self.config.host else self.config.host
        try:
            self.port = self.server.add_insecure_port(f"{host}:{self.config.port}")
            if not self.port:
                raise RuntimeError("gRPC 포트에 바인딩하지 못했습니다.")
            await self.server.start()
            self.service.accepting = True
        except BaseException:
            await self.close()
            raise

    async def close(self):
        """준비 상태를 내리고 세션·RPC·대기 계산 순서로 정리한다."""
        if self._closed:
            return
        self._closed = True
        self.service.accepting = False
        for session in list(self.service.sessions.values()):
            session.close()
        await self.server.stop(0)
        await self.workers.close()
