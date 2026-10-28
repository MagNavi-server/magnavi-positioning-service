"""생성한 메시지의 수치 존재 여부·문자열 보존·양방향 RPC 형식을 검증한다."""

import unittest
from unittest.mock import Mock

from magnavi.positioning.v1 import positioning_pb2 as messages
from magnavi.positioning.v1 import positioning_pb2_grpc as transport


class PositioningContractTests(unittest.TestCase):
    """모델·DB·네트워크 없이 Python 생성 코드의 통신 표현만 확인한다."""

    def test_service_is_bidirectional(self):
        """Spring이 호출하는 서비스 이름과 양방향 스트림 특성을 확인한다."""
        service = messages.DESCRIPTOR.services_by_name["PositioningService"]
        method = service.methods_by_name["PredictStream"]
        self.assertEqual(service.full_name, "magnavi.positioning.v1.PositioningService")
        self.assertTrue(method.client_streaming)
        self.assertTrue(method.server_streaming)
        self.assertEqual(method.input_type.full_name, "magnavi.positioning.v1.StreamRequest")
        self.assertEqual(method.output_type.full_name, "magnavi.positioning.v1.StreamEvent")

    def test_request_oneof_replaces_previous_payload(self):
        """시작과 센서 입력이 한 메시지에 동시에 존재하지 않음을 확인한다."""
        request = messages.StreamRequest(start=messages.StartMeasurement(session_id="test"))
        self.assertEqual(request.WhichOneof("payload"), "start")
        request.sample.sequence = 1
        self.assertEqual(request.WhichOneof("payload"), "sample")
        self.assertFalse(request.HasField("start"))

    def test_explicit_zero_values_survive_serialization(self):
        """정상 값 0을 누락으로 오인하지 않도록 모든 필수 수치의 존재를 보존한다."""
        vector = messages.Vector3(x=0.0, y=0.0, z=0.0)
        original = messages.SensorSample(
            sequence=1, elapsed_nanos=0, magnetic=vector, orientation=vector
        )
        restored = messages.SensorSample.FromString(original.SerializeToString())
        self.assertTrue(restored.HasField("sequence"))
        self.assertTrue(restored.HasField("elapsed_nanos"))
        self.assertEqual(restored.elapsed_nanos, 0)
        for name in ("magnetic", "orientation"):
            self.assertTrue(restored.HasField(name))
            for axis in ("x", "y", "z"):
                with self.subTest(vector=name, axis=axis):
                    self.assertTrue(getattr(restored, name).HasField(axis))
                    self.assertEqual(getattr(getattr(restored, name), axis), 0.0)

    def test_missing_values_remain_distinguishable(self):
        """벡터 전체 누락과 벡터 안 수치 누락을 각각 판단할 수 있는지 확인한다."""
        sample = messages.SensorSample(magnetic=messages.Vector3())
        restored = messages.SensorSample.FromString(sample.SerializeToString())
        self.assertFalse(restored.HasField("sequence"))
        self.assertFalse(restored.HasField("elapsed_nanos"))
        self.assertTrue(restored.HasField("magnetic"))
        self.assertFalse(restored.HasField("orientation"))
        for axis in ("x", "y", "z"):
            self.assertFalse(restored.magnetic.HasField(axis))

    def test_ready_retains_requested_identifiers(self):
        """준비 응답에 계약·모델·센서 규격과 세션 식별자를 표현할 수 있다."""
        event = messages.StreamEvent(
            session_id="test-session",
            ready=messages.StreamReady(
                contract_version=1, model_key="test-model", model_version="test-version",
                sensor_profile_id="synthetic-v1", required_count=16,
            ),
        )
        restored = messages.StreamEvent.FromString(event.SerializeToString())
        self.assertEqual(restored, event)
        self.assertEqual(restored.WhichOneof("payload"), "ready")

    def test_warming_up_retains_sequence_and_counts(self):
        """15번째 대기 응답의 순번·채운 수·필요 수가 직렬화 후에도 남는다."""
        event = messages.StreamEvent(
            session_id="test-session",
            warming_up=messages.WarmingUp(sequence=15, buffered_count=15, required_count=16),
        )
        restored = messages.StreamEvent.FromString(event.SerializeToString())
        self.assertEqual(restored, event)
        self.assertEqual(restored.WhichOneof("payload"), "warming_up")
        self.assertTrue(restored.warming_up.HasField("sequence"))

    def test_prediction_preserves_codes_and_zero_score(self):
        """위치 코드의 선행 0·공백·대소문자와 명시한 점수 0을 보존한다."""
        prediction = messages.Prediction(
            sequence=16, model_key="test-model", model_version="test-version",
            location_code="007", score=0.8,
            candidates=[
                messages.Candidate(location_code="007", score=0.8),
                messages.Candidate(location_code=" aB ", score=0.0),
            ],
        )
        event = messages.StreamEvent(session_id="test-session", prediction=prediction)
        restored = messages.StreamEvent.FromString(event.SerializeToString())
        self.assertEqual(restored, event)
        self.assertEqual(restored.WhichOneof("payload"), "prediction")
        self.assertTrue(restored.prediction.HasField("sequence"))
        self.assertTrue(restored.prediction.HasField("score"))
        self.assertTrue(restored.prediction.candidates[1].HasField("score"))
        self.assertFalse(messages.Candidate().HasField("score"))

    def test_stub_uses_the_expected_rpc_and_serializers(self):
        """네트워크 없이 생성 stub의 호출 경로와 메시지 변환 연결을 확인한다."""
        channel = Mock()
        transport.PositioningServiceStub(channel)
        channel.stream_stream.assert_called_once()
        args, kwargs = channel.stream_stream.call_args
        self.assertEqual(args[0], "/magnavi.positioning.v1.PositioningService/PredictStream")
        request = messages.StreamRequest(start=messages.StartMeasurement(session_id="test"))
        self.assertEqual(kwargs["request_serializer"](request), request.SerializeToString())
        event = messages.StreamEvent(session_id="test")
        self.assertEqual(kwargs["response_deserializer"](event.SerializeToString()), event)


if __name__ == "__main__":
    unittest.main()
