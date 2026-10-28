"""실제 루프백 gRPC와 기존 모델로 첫 예측·격리·취소·한도·오류를 검증한다."""

import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
from threading import Event
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4
import warnings

import grpc
from fastapi import FastAPI, Request, Response

from magnavi.positioning.v1 import positioning_pb2 as messages
from magnavi.positioning.v1 import positioning_pb2_grpc as transport
from positioning.config import StreamConfig
from positioning.server import PositioningServer
from services.model_runtime import ModelRuntime
from services.model_lifecycle import model_lifespan
from services import model_service
from routers.health_router import ready


ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config/positioning.synthetic.json"
INPUTS = json.loads((ROOT / "tests/baseline/synthetic_inputs.json").read_text())["cases"]
EXPECTED = json.loads((ROOT / "tests/baseline/reference.json").read_text())["results"]["cases"]


def sample(sequence, values=None):
    """합성 여섯 값을 presence가 있는 protobuf 센서 요청으로 만든다."""
    values = values if values is not None else INPUTS[0]["samples"][0]
    return messages.StreamRequest(sample=messages.SensorSample(
        sequence=sequence, elapsed_nanos=(sequence - 1) * 100_000_000,
        magnetic=messages.Vector3(x=values[0], y=values[1], z=values[2]),
        orientation=messages.Vector3(x=values[3], y=values[4], z=values[5]),
    ))


class StreamingTests(unittest.IsolatedAsyncioTestCase):
    """모든 서버와 모델을 테스트 소유로 만들어 DB·운영 연결 없이 검증한다."""

    async def asyncSetUp(self):
        """모델 묶음과 임의 루프백 포트를 준비하고 기존 수치 경고를 분리한다."""
        self.warning_context = warnings.catch_warnings()
        self.warning_context.__enter__()
        warnings.simplefilter("ignore")
        self.config = replace(StreamConfig.read(CONFIG_PATH), port=0)
        self.config.verify_model_files(ROOT / "model")
        self.runtime = ModelRuntime()
        self.runtime.start()
        self.calls = []
        self.server = None
        self.channel = None
        await self.start_server()

    async def start_server(self, **changes):
        """자원·시간 한도를 바꾼 테스트도 자신이 열었던 서버를 먼저 정리한다."""
        if self.channel:
            await self.channel.close()
        if self.server:
            await self.server.close()
        self.config = replace(self.config, **changes)
        self.server = PositioningServer(self.runtime, self.config)
        await self.server.start()
        self.channel = grpc.aio.insecure_channel(f"127.0.0.1:{self.server.port}")
        self.stub = transport.PositioningServiceStub(self.channel)

    async def asyncTearDown(self):
        """실패한 검증에서도 RPC·스레드·모델·경고 설정을 남기지 않는다."""
        for call in self.calls:
            call.cancel()
        await self.channel.close()
        await self.server.close()
        self.runtime.stop()
        self.warning_context.__exit__(None, None, None)

    def start_message(self, **changes):
        """운영 식별자를 추측하지 않고 테스트 등록값과 새 측정 ID를 사용한다."""
        values = dict(session_id=str(uuid4()), contract_version=2, model_key=self.config.model_key,
                      model_version=self.config.model_version, sensor_profile_id=self.config.sensor_profile_id)
        values.update(changes)
        return messages.StreamRequest(start=messages.StartMeasurement(**values))

    async def open_call(self, **changes):
        """모델이 실제 ready를 보낸 뒤에만 센서를 전송할 호출을 반환한다."""
        call = self.stub.PredictStream(timeout=15)
        self.calls.append(call)
        await call.write(self.start_message(**changes))
        event = await call.read()
        self.assertEqual(event.WhichOneof("payload"), "ready")
        self.assertEqual(event.ready.contract_version, 2)
        self.assertEqual(event.ready.required_count, 16)
        return call, event.session_id

    async def assert_status(self, call, code):
        """오류가 정상 결과로 섞이지 않고 지정한 gRPC 상태로 종료되는지 확인한다."""
        with self.assertRaises(grpc.aio.AioRpcError) as failure:
            await call.read()
        self.assertEqual(failure.exception.code(), code)
        return failure.exception

    def assert_prediction(self, event, expected, sequence, session_id):
        """첫 샘플을 포함해 기존 정수 코드·후보 순서·점수를 전송 결과와 비교한다."""
        self.assertEqual(event.WhichOneof("payload"), "prediction")
        self.assertEqual(event.session_id, session_id)
        self.assertEqual(event.prediction.sequence, sequence)
        self.assertEqual(event.prediction.location_code, str(expected["prediction"]))
        self.assertEqual(len(event.prediction.candidates), len(expected["candidates"]))
        for actual, (code, score) in zip(event.prediction.candidates, expected["candidates"]):
            self.assertEqual(actual.location_code, str(code))
            self.assertAlmostEqual(actual.score, score, delta=max(1e-12, abs(score) * 1e-9))
        self.assertEqual(event.prediction.score, event.prediction.candidates[0].score)

    async def test_all_144_reference_predictions_over_grpc(self):
        """고정 입력 3종의 첫 번째·15·16번째를 포함한 모든 결과를 실제 전송한다."""
        for inputs, expected in zip(INPUTS, EXPECTED):
            call, identity = await self.open_call()
            for sequence, (values, reference) in enumerate(zip(inputs["samples"], expected["samples"]), 1):
                await call.write(sample(sequence, values))
                self.assert_prediction(await call.read(), reference, sequence, identity)
            await call.done_writing()
            self.assertIs(await call.read(), grpc.aio.EOF)

    async def test_two_interleaved_measurements_match_independent_results(self):
        """서로 다른 96개 입력을 교차·동시에 보내도 각각의 단독 결과를 유지한다."""
        a, aid = await self.open_call()
        b, bid = await self.open_call()
        for index in range(48):
            await asyncio.gather(a.write(sample(index + 1, INPUTS[1]["samples"][index])),
                                 b.write(sample(index + 1, INPUTS[2]["samples"][index])))
            left, right = await asyncio.gather(a.read(), b.read())
            self.assert_prediction(left, EXPECTED[1]["samples"][index], index + 1, aid)
            self.assert_prediction(right, EXPECTED[2]["samples"][index], index + 1, bid)

    async def test_start_rejects_old_contract_and_unknown_registration(self):
        """예전 대기 계약이나 미등록 모델·버전·프로필을 조용히 대체하지 않는다."""
        for changes in ({"contract_version": 1}, {"model_key": "unknown"},
                        {"model_version": "unknown"}, {"sensor_profile_id": "unknown"}):
            call = self.stub.PredictStream()
            self.calls.append(call)
            await call.write(self.start_message(**changes))
            await self.assert_status(call, grpc.StatusCode.FAILED_PRECONDITION)

    async def test_start_and_sample_presence_are_required(self):
        """시작 없는 샘플·잘못된 세션 ID·빈 요청·벡터·축 누락을 거절한다."""
        for request in (sample(1), messages.StreamRequest(), self.start_message(session_id="member-1")):
            call = self.stub.PredictStream()
            self.calls.append(call)
            await call.write(request)
            await self.assert_status(call, grpc.StatusCode.INVALID_ARGUMENT)
        for field in ("sequence", "elapsed_nanos", "magnetic", "orientation"):
            call, _ = await self.open_call()
            request = sample(1)
            request.sample.ClearField(field)
            await call.write(request)
            await self.assert_status(call, grpc.StatusCode.INVALID_ARGUMENT)
        for vector in ("magnetic", "orientation"):
            for axis in ("x", "y", "z"):
                call, _ = await self.open_call()
                request = sample(1)
                getattr(request.sample, vector).ClearField(axis)
                await call.write(request)
                await self.assert_status(call, grpc.StatusCode.INVALID_ARGUMENT)

    async def test_zero_is_valid_but_nonfinite_values_are_rejected(self):
        """0은 실제 예측하고 각 축의 NaN·무한대는 결과를 만들지 않는다."""
        call, _ = await self.open_call()
        await call.write(sample(1, [0.0] * 6))
        self.assertTrue((await call.read()).HasField("prediction"))
        call.cancel()
        for index in range(6):
            for value in (float("nan"), float("inf"), -float("inf")):
                call, _ = await self.open_call()
                values = [0.0] * 6
                values[index] = value
                await call.write(sample(1, values))
                await self.assert_status(call, grpc.StatusCode.INVALID_ARGUMENT)

    async def test_sequence_time_and_repeated_start_are_rejected(self):
        """중복·누락·역순, 시간 정지·역행, 재시작 메시지가 스트림을 종료한다."""
        for invalid in (sample(1), sample(3), self.start_message(),
                        messages.StreamRequest(sample=messages.SensorSample(
                            sequence=2, elapsed_nanos=0, magnetic=messages.Vector3(x=0,y=0,z=0),
                            orientation=messages.Vector3(x=0,y=0,z=0)))):
            call, _ = await self.open_call()
            await call.write(sample(1))
            await call.read()
            await call.write(invalid)
            await self.assert_status(call, grpc.StatusCode.INVALID_ARGUMENT)

    async def test_duplicate_session_does_not_close_original(self):
        """동일 식별자 요청을 거절해도 이미 활성인 세션의 이력을 삭제하지 않는다."""
        original, identity = await self.open_call()
        duplicate = self.stub.PredictStream()
        self.calls.append(duplicate)
        await duplicate.write(self.start_message(session_id=identity))
        await self.assert_status(duplicate, grpc.StatusCode.FAILED_PRECONDITION)
        await original.write(sample(1))
        self.assert_prediction(await original.read(), EXPECTED[0]["samples"][0], 1, identity)

    async def test_active_connection_limit(self):
        """입력을 기다리는 연결도 전체 활성 RPC 한도에 포함한다."""
        await self.start_server(max_streams=1)
        await self.open_call()
        excess = self.stub.PredictStream(timeout=3)
        self.calls.append(excess)
        await self.assert_status(excess, grpc.StatusCode.RESOURCE_EXHAUSTED)

    async def test_cancelled_computation_keeps_capacity_until_finished(self):
        """취소한 계산의 늦은 결과를 버리고 재접속은 빈 세션에서 시작한다."""
        await self.start_server(workers=1, pending_jobs=0)
        entered, release = Event(), Event()
        predict = self.runtime.predict

        def held_prediction(data, session):
            """실제 모델 계산 후 반환을 멈춰 취소와 완료의 경합을 만든다."""
            result = predict(data, session)
            entered.set()
            if not release.wait(5):
                raise TimeoutError("test worker release missing")
            return result

        with patch.object(self.runtime, "predict", side_effect=held_prediction):
            old, old_id = await self.open_call()
            await old.write(sample(1))
            self.assertTrue(await asyncio.to_thread(entered.wait, 3))
            old_session = self.server.service.sessions[old_id]
            old.cancel()
            try:
                await self.wait_until(lambda: old_session.closed)
                self.assertEqual(self.server.workers.outstanding, 1)
                replacement, _ = await self.open_call()
                await replacement.write(sample(1))
                await self.assert_status(replacement, grpc.StatusCode.RESOURCE_EXHAUSTED)
            finally:
                release.set()
            await self.wait_until(lambda: self.server.workers.outstanding == 0)
        new, new_id = await self.open_call()
        await new.write(sample(1))
        self.assert_prediction(await new.read(), EXPECTED[0]["samples"][0], 1, new_id)
        self.assertNotEqual(old_id, new_id)
        self.assertEqual(old_session.buffered_count, 0)

    async def wait_until(self, condition):
        """비동기 정리가 끝날 때까지만 짧게 기다리고 무한 대기를 막는다."""
        async with asyncio.timeout(3):
            while not condition():
                await asyncio.sleep(0.01)

    async def test_queued_cancellation_and_server_shutdown(self):
        """대기 작업 취소는 계산을 실행하지 않고 종료 중인 서버는 늦은 결과를 보내지 않는다."""
        await self.start_server(workers=1, pending_jobs=1)
        entered, release = Event(), Event()
        predict = self.runtime.predict

        def held_prediction(data, session):
            """첫 계산을 묶어 두어 다음 요청이 작업자 큐에서 기다리게 한다."""
            entered.set()
            if not release.wait(5):
                raise TimeoutError("test worker release missing")
            return predict(data, session)

        with patch.object(self.runtime, "predict", side_effect=held_prediction) as computation:
            running, running_id = await self.open_call()
            await running.write(sample(1))
            self.assertTrue(await asyncio.to_thread(entered.wait, 3))
            try:
                queued, queued_id = await self.open_call()
                await queued.write(sample(1))
                await self.wait_until(lambda: self.server.workers.outstanding == 2)
                queued.cancel()
                await self.wait_until(lambda: self.server.workers.outstanding == 1)
                self.assertNotIn(queued_id, self.server.service.sessions)
                session = self.server.service.sessions[running_id]
                closing = asyncio.create_task(self.server.close())
                await self.wait_until(lambda: session.closed)
                self.assertFalse(self.server.ready)
                self.assertFalse(closing.done())
            finally:
                release.set()
            await asyncio.wait_for(closing, 3)
            self.assertEqual(computation.call_count, 1)
            self.assertEqual(session.buffered_count, 0)
            self.assertEqual(self.server.workers.outstanding, 0)
            self.assertEqual(self.server.service.sessions, {})

    async def test_start_and_idle_timeouts(self):
        """첫 메시지가 없거나 ready 이후 입력이 멈추면 연결을 정리한다."""
        await self.start_server(start_timeout=0.1, idle_timeout=0.1)
        silent = self.stub.PredictStream()
        self.calls.append(silent)
        await self.assert_status(silent, grpc.StatusCode.DEADLINE_EXCEEDED)
        idle, _ = await self.open_call()
        await self.assert_status(idle, grpc.StatusCode.DEADLINE_EXCEEDED)

    async def test_inference_timeout_closes_session_but_does_not_free_busy_worker(self):
        """추론 시간 초과가 실행 중인 작업을 끝냈다고 오인하지 않는다."""
        await self.start_server(prediction_timeout=0.1)
        release = Event()

        def slow_prediction(data, session):
            """시간 초과 뒤에도 실행 중인 계산을 재현하고 테스트 종료 전에 해제한다."""
            release.wait(3)
            return self.runtime_original(data, session)

        self.runtime_original = self.runtime.predict
        with patch.object(self.runtime, "predict", side_effect=slow_prediction):
            call, identity = await self.open_call()
            session = self.server.service.sessions[identity]
            await call.write(sample(1))
            try:
                await self.assert_status(call, grpc.StatusCode.DEADLINE_EXCEEDED)
                self.assertTrue(session.closed)
                self.assertEqual(self.server.workers.outstanding, 1)
            finally:
                release.set()
            await self.wait_until(lambda: self.server.workers.outstanding == 0)

    async def test_internal_failure_is_sanitized(self):
        """내부 예외 원문을 앱에 노출하지 않고 실패 세션을 정리한다."""
        call, identity = await self.open_call()
        with patch.object(self.runtime, "predict", side_effect=RuntimeError("synthetic-private-detail")):
            await call.write(sample(1))
            failure = await self.assert_status(call, grpc.StatusCode.INTERNAL)
        self.assertNotIn("synthetic-private-detail", failure.details())
        await self.wait_until(lambda: identity not in self.server.service.sessions)

    async def test_empty_stream_and_client_deadline(self):
        """시작 없는 종료와 클라이언트 기한 만료가 다음 측정에 영향을 주지 않는다."""
        call = self.stub.PredictStream()
        self.calls.append(call)
        await call.done_writing()
        await self.assert_status(call, grpc.StatusCode.INVALID_ARGUMENT)
        deadline = self.stub.PredictStream(timeout=0.1)
        self.calls.append(deadline)
        await self.assert_status(deadline, grpc.StatusCode.DEADLINE_EXCEEDED)
        healthy, _ = await self.open_call()
        await healthy.write(sample(1))
        self.assertTrue((await healthy.read()).HasField("prediction"))

    async def test_shared_lifespan_readiness_and_cleanup(self):
        """HTTP 준비 상태가 공용 모델과 실제 gRPC 수명을 함께 반영한다."""
        application = FastAPI()
        request = Request({"type": "http", "app": application})
        response = Response()
        self.assertFalse(ready(request, response)["grpc_ready"])
        self.assertEqual(response.status_code, 503)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            data = json.loads(CONFIG_PATH.read_text())
            data["port"] = 0
            path.write_text(json.dumps(data))
            with patch.dict(os.environ, {"POSITIONING_CONFIG": str(path)}):
                async with model_lifespan(application):
                    server = application.state.positioning_server
                    self.assertIs(server.runtime, model_service.get_model_runtime())
                    self.assertEqual(ready(request, response), {"model_ready": True, "grpc_ready": True})
                    self.assertEqual(response.status_code, 200)
                self.assertFalse(server.ready)
                self.assertFalse(model_service.get_model_status()["model_loaded"])

    async def test_registration_hash_and_bind_validation(self):
        """모델 묶음 불일치나 이번 범위를 벗어난 공개 바인딩을 거절한다."""
        hashes = dict(self.config.model_sha256)
        hashes["scaler.pkl"] = "0" * 64
        with self.assertRaises(ValueError):
            replace(self.config, model_sha256=hashes).verify_model_files(ROOT / "model")
        with self.assertRaises(ValueError):
            replace(self.config, host="0.0.0.0")


if __name__ == "__main__":
    unittest.main()
