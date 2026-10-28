"""DB와 네트워크 서버 없이 기존 HTTP 예측 요청·응답의 호환성을 확인한다."""

import asyncio
import contextlib
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import warnings
import numpy as np


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "baseline"))
import prediction_baseline as baseline

with warnings.catch_warnings(), contextlib.redirect_stdout(io.StringIO()):
    warnings.simplefilter("ignore")
    from fastapi import FastAPI
    from routers import prediction_router
    from services import legacy_prediction_service, model_service
    from services.model_lifecycle import model_lifespan
    from services.model_loader import load_model_bundle


class HttpCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    """예측 라우터만 등록한 임시 앱에 HTTP 메시지를 직접 전달한다."""

    async def asyncSetUp(self):
        """사례별 호환 세션과 임시 앱을 만들고 기존 전역 참조를 복구하도록 한다."""
        self.enterContext(patch("services.model_runtime.logger.warning"))
        self.app = FastAPI(lifespan=model_lifespan)
        await self.enterAsyncContext(self.app.router.lifespan_context(self.app))
        self.app.include_router(prediction_router.router)
        self.case = baseline.load_inputs()[1]
        reference = baseline.read_json(baseline.REFERENCE_PATH)
        self.expected = reference["results"]["cases"][1]["samples"]
        baseline.compare(reference["environment"], baseline.environment(), "environment")
        baseline.compare(reference["files"], baseline.fingerprints(), "files")
        session = model_service.create_prediction_session()
        self.addCleanup(session.close)
        self.enterContext(patch.object(legacy_prediction_service, "_legacy_session", session))
        self.enterContext(warnings.catch_warnings())
        warnings.simplefilter("ignore")

    async def post_prediction(self, payload):
        """추가 HTTP 클라이언트 의존성 없이 ASGI 요청과 응답 본문을 교환한다."""
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        messages = []
        sent_request = False

        async def receive():
            """요청 본문은 한 번만 보내고 추가 수신은 테스트 제한 시간까지 대기한다."""
            nonlocal sent_request
            if sent_request:
                await asyncio.Event().wait()
            sent_request = True
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message):
            """임시 앱이 보낸 HTTP 상태와 본문 메시지를 모은다."""
            messages.append(message)

        scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": "POST", "scheme": "http", "path": "/predict",
            "raw_path": b"/predict", "query_string": b"", "root_path": "",
            "headers": [(b"content-type", b"application/json")],
            "server": ("testserver", 80), "client": ("127.0.0.1", 12345),
        }
        await asyncio.wait_for(self.app(scope, receive, send), timeout=5)
        status = next(message["status"] for message in messages if message["type"] == "http.response.start")
        response_body = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
        return status, json.loads(response_body)

    async def test_existing_six_field_requests_keep_response_values(self):
        """세션 번호 없이 보내던 48개 연속 요청의 코드·점수·문자열 후보를 유지한다."""
        for values, expected in zip(self.case["samples"], self.expected, strict=True):
            status, body = await self.post_prediction(dict(zip(baseline.FIELDS, values)))
            self.assertEqual(200, status)
            self.assertEqual({"prediction", "confidence", "top_k_results"}, set(body))
            self.assertEqual(expected["prediction"], body["prediction"])
            baseline.compare(expected["logging_candidates"][0][1], body["confidence"])
            self.assertEqual([f"{code} ({score:.4f})" for code, score in expected["candidates"]], body["top_k_results"])

    async def test_missing_sensor_field_still_returns_validation_error(self):
        """호환 경로 변경 때문에 필수 센서값 검증이 우회되지 않는다."""
        payload = dict(zip(baseline.FIELDS, self.case["samples"][0]))
        payload.pop("Mag_X")
        status, _ = await self.post_prediction(payload)
        self.assertEqual(422, status)

    async def test_unavailable_model_returns_503_with_legacy_body(self):
        """모델 미준비를 503으로 구분하면서 기존 실패 본문 필드는 유지한다."""
        model_service.shutdown_model()
        status, body = await self.post_prediction(dict(zip(baseline.FIELDS, self.case["samples"][0])))
        self.assertEqual(503, status)
        self.assertEqual(-1, body["prediction"])
        self.assertEqual(0.0, body["confidence"])

    async def test_test_application_does_not_import_business_database(self):
        """예측 요청 검증이 원래 app.py의 DB 초기화로 이어지지 않았는지 확인한다."""
        await self.post_prediction(dict(zip(baseline.FIELDS, self.case["samples"][0])))
        self.assertNotIn("app", sys.modules)
        self.assertFalse(any(name == "database" or name.startswith("database.") for name in sys.modules))

    async def test_nonfinite_inputs_return_422_without_echoing_sensor_values(self):
        """JSON 수치·문자열의 비유한 값을 거절하면서 오류 응답 직렬화도 성공한다."""
        valid = dict(zip(baseline.FIELDS, self.case["samples"][0]))
        await self.post_prediction(valid)
        for field in baseline.FIELDS:
            for value in (float("nan"), float("inf"), -float("inf"), "NaN", "Infinity", "-Infinity"):
                with self.subTest(field=field, value=value):
                    payload = {**valid, field: value}
                    status, body = await self.post_prediction(payload)
                    self.assertEqual(422, status)
                    self.assertEqual("finite_number", body["detail"][0]["type"])
                    self.assertEqual({"type", "loc", "msg"}, set(body["detail"][0]))
                    self.assertEqual(1, legacy_prediction_service._legacy_session.buffered_count)
        status, body = await self.post_prediction(dict(zip(baseline.FIELDS, self.case["samples"][1])))
        self.assertEqual(200, status)
        self.assertEqual(self.expected[1]["prediction"], body["prediction"])

    async def test_invalid_json_and_wrong_type_are_validation_errors(self):
        """깨진 JSON과 숫자로 변환할 수 없는 입력도 500 대신 422로 처리한다."""
        valid = dict(zip(baseline.FIELDS, self.case["samples"][0]))
        for payload in (b'{"Mag_X":', {**valid, "Mag_X": "invalid"}, {**valid, "Ori_Z": None}):
            status, body = await self.post_prediction(payload)
            self.assertEqual(422, status)
            self.assertIn("detail", body)
            self.assertEqual(0, legacy_prediction_service._legacy_session.buffered_count)

    async def test_wait_policy_returns_202_then_normal_prediction(self):
        """실제 wait 설정에서 15개까지 대기하고 16번째에 정상 응답을 반환한다."""
        model_service.shutdown_model()
        bundle = load_model_bundle()
        bundle.preprocessing["warmup_policy"] = "wait"
        with patch("services.model_runtime.load_model_bundle", return_value=bundle):
            model_service.initialize_model()
        for index in range(16):
            status, body = await self.post_prediction(dict(zip(baseline.FIELDS, self.case["samples"][index])))
            if index < 15:
                self.assertEqual(202, status)
                self.assertEqual(-2, body["prediction"])
            else:
                self.assertEqual(200, status)
                self.assertEqual(self.expected[index]["prediction"], body["prediction"])
                baseline.compare(self.expected[index]["logging_candidates"][0][1], body["confidence"])

    async def test_failed_inference_returns_safe_500_and_recovers(self):
        """내부 예외 정보는 숨기고 실패한 입력을 되돌린 뒤 다음 정상 요청을 처리한다."""
        payload = dict(zip(baseline.FIELDS, self.case["samples"][0]))
        with patch.object(model_service, "_predict_in_session", side_effect=RuntimeError("private synthetic marker")):
            with self.assertLogs("routers.prediction_router", level="ERROR") as logs:
                status, body = await self.post_prediction(payload)
        self.assertEqual(500, status)
        self.assertEqual({"detail": "Prediction failed."}, body)
        self.assertNotIn("private synthetic marker", str(logs.output))
        self.assertEqual(0, legacy_prediction_service._legacy_session.buffered_count)
        status, body = await self.post_prediction(payload)
        self.assertEqual(200, status)
        self.assertEqual(self.expected[0]["prediction"], body["prediction"])

    async def test_finite_overflow_returns_422(self):
        """입력은 유한해도 전처리에서 수치 범위를 벗어나면 안전하게 실패한다."""
        model_service.shutdown_model()
        bundle = load_model_bundle()
        # 실제 모델 파일은 유지하고 임시 묶음의 2배 보정에서 넘침을 재현한다.
        bundle.components["soft_iron"] = np.eye(3) * 2
        with patch("services.model_runtime.load_model_bundle", return_value=bundle):
            model_service.initialize_model()
        payload = dict(zip(baseline.FIELDS, self.case["samples"][0]))
        payload["Mag_X"] = 1.79e308
        status, body = await self.post_prediction(payload)
        self.assertEqual(422, status)
        self.assertEqual(0, legacy_prediction_service._legacy_session.buffered_count)
        self.assertNotIn("1.79", str(body))


if __name__ == "__main__":
    unittest.main()
