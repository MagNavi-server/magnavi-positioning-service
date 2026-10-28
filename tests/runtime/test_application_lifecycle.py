"""업무 DB와 라우터를 대체해 실제 app.py의 시작·종료 연결만 검증한다."""

from pathlib import Path
import runpy
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from fastapi import APIRouter

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from routers import health_router, prediction_router
from services import legacy_prediction_service, model_service
from services.prediction_errors import ModelPreparationError


class ApplicationLifecycleTests(unittest.IsolatedAsyncioTestCase):
    """실제 DB에 연결하지 않고 import·모델 준비·DB 초기화·정리 순서를 확인한다."""

    def setUp(self):
        """실제 DB 모듈이 로딩되지 않도록 명시적인 대역을 설치한 뒤 앱 코드를 읽는다."""
        model_service.shutdown_model()
        legacy_prediction_service.close_legacy_session()
        self.addCleanup(model_service.shutdown_model)
        self.enterContext(patch("services.model_runtime.logger.warning"))
        self.database = ModuleType("database")
        self.database.engine = object()
        self.database.Base = SimpleNamespace(metadata=SimpleNamespace(create_all=Mock()))
        router_package = ModuleType("routers")
        router_package.prediction_router = prediction_router
        for name in ("favorites_router", "locations_router", "user_router", "outdoor_places_router"):
            setattr(router_package, name, SimpleNamespace(router=APIRouter()))
        self.enterContext(patch.dict(sys.modules, {
            "database": self.database, "routers": router_package, "routers.health_router": health_router,
        }))
        self.application = runpy.run_path(str(ROOT / "app.py"))["app"]

    async def test_import_is_inert_and_normal_start_prepares_before_database(self):
        """앱을 읽을 때는 DB 쓰기가 없고 모델 준비 후에 기존 초기화를 한 번 수행한다."""
        self.database.Base.metadata.create_all.assert_not_called()
        self.assertFalse(model_service.get_model_status()["model_loaded"])

        def check_ready(*, bind):
            """DB 초기화 대역이 호출되는 시점에 모델 준비가 끝났는지 확인한다."""
            self.assertIs(self.database.engine, bind)
            self.assertTrue(model_service.get_model_status()["model_loaded"])

        self.database.Base.metadata.create_all.side_effect = check_ready
        async with self.application.router.lifespan_context(self.application):
            session = model_service.create_prediction_session()
            self.assertEqual(0, session.buffered_count)
        self.database.Base.metadata.create_all.assert_called_once_with(bind=self.database.engine)
        self.assertEqual("stopped", model_service.get_model_status()["state"])
        self.assertTrue(session.closed)
        self.assertIsNone(legacy_prediction_service._legacy_session)

    async def test_model_failure_aborts_startup_before_database(self):
        """모델 준비 실패가 서버 시작 바깥으로 전파되며 DB 초기화에 도달하지 않는다."""
        with patch("services.model_runtime.load_model_bundle", side_effect=ModelPreparationError("synthetic missing")):
            with self.assertRaises(ModelPreparationError):
                async with self.application.router.lifespan_context(self.application):
                    self.fail("모델 준비 실패 후 요청 수락 구간에 진입했습니다.")
        self.database.Base.metadata.create_all.assert_not_called()
        self.assertEqual("failed", model_service.get_model_status()["state"])

    async def test_database_startup_failure_cleans_prepared_model(self):
        """후속 초기화가 실패해도 준비된 모델과 세션을 남기지 않는다."""
        self.database.Base.metadata.create_all.side_effect = RuntimeError("synthetic database failure")
        with self.assertRaises(RuntimeError):
            async with self.application.router.lifespan_context(self.application):
                self.fail("DB 초기화 실패 후 서버가 시작됐습니다.")
        self.assertEqual("stopped", model_service.get_model_status()["state"])

    async def test_application_exception_also_closes_session(self):
        """서버 수명 구간의 예외에도 모델 정리 finally가 실행된다."""
        with self.assertRaises(RuntimeError):
            async with self.application.router.lifespan_context(self.application):
                session = model_service.create_prediction_session()
                raise RuntimeError("synthetic application failure")
        self.assertTrue(session.closed)
        self.assertEqual("stopped", model_service.get_model_status()["state"])


if __name__ == "__main__":
    unittest.main()
