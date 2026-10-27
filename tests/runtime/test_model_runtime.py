"""실제 모델과 격리된 실패 조건으로 준비 상태·입력 검증·종료 경계를 확인한다."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Event
import unittest
from unittest.mock import patch
import warnings

import numpy as np
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from schemas.prediction import SensorInput
from services import model_service
from services.model_loader import MODEL_DIR, MODEL_FILES, ModelBundle, load_model_bundle
from services.model_runtime import ModelRuntime
from services.prediction_errors import (
    InvalidSensorInputError, ModelNotReadyError, ModelPreparationError, PredictionFailedError,
)
from services.prediction_session import MAGNETIC_AXES, PredictionSession, PredictionSessionClosedError


class ModelRuntimeTests(unittest.TestCase):
    """시험 예측 이후에만 준비 상태가 공개되고 실패 입력이 이력을 오염시키지 않는다."""

    @classmethod
    def setUpClass(cls):
        """신뢰된 저장소 모델을 읽어 각 테스트가 독립적인 사본을 사용하게 한다."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            cls.source_bundle = load_model_bundle()

    def setUp(self):
        """상태와 모델 사본을 격리하고 기존 수치 경고는 테스트 출력에서만 접는다."""
        self.runtime = ModelRuntime()
        self.addCleanup(self.runtime.stop)
        self.bundle = deepcopy(self.source_bundle)
        self.sample = SensorInput(Mag_X=10, Mag_Y=-5, Mag_Z=40, Ori_X=0, Ori_Y=0, Ori_Z=0)
        self.enterContext(warnings.catch_warnings())
        warnings.simplefilter("ignore")
        self.enterContext(patch("services.model_runtime.logger.warning"))

    def start_runtime(self):
        """실제 모델 사본으로 시험 예측을 수행하고 준비된 런타임을 반환한다."""
        with patch("services.model_runtime.load_model_bundle", return_value=self.bundle):
            self.runtime.start()
        return self.runtime

    def test_import_does_not_load_models_or_database(self):
        """새 프로세스에서 패키지 import만으로 모델 파일과 업무 DB를 열지 않는다."""
        script = '''
from unittest.mock import patch
import sys
with patch("joblib.load", side_effect=AssertionError("unexpected load")) as load:
    from services import model_service
    from services.model_lifecycle import model_lifespan
    assert model_service.get_model_status()["state"] == "not_started"
    load.assert_not_called()
assert not any(name == "database" or name.startswith("database.") for name in sys.modules)
'''
        result = subprocess.run([sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True, timeout=20)
        self.assertEqual(0, result.returncode, result.stderr)

    def test_unstarted_runtime_rejects_requests(self):
        """파일 import와 객체 생성만으로 준비 상태가 되지 않는다."""
        self.assertFalse(self.runtime.status()["model_loaded"])
        with self.assertRaises(ModelNotReadyError):
            self.runtime.create_session()
        with self.assertRaises(ModelNotReadyError):
            self.runtime.predict(self.sample, PredictionSession(16))

    def test_every_required_file_is_checked_before_loading(self):
        """여섯 구성 중 하나라도 없으면 부분 로딩 전에 전체 묶음을 거절한다."""
        for omitted in MODEL_FILES.values():
            with self.subTest(file=omitted), tempfile.TemporaryDirectory() as directory:
                for name in MODEL_FILES.values():
                    if name != omitted:
                        (Path(directory) / name).symlink_to(MODEL_DIR / name)
                with patch("services.model_loader.joblib.load") as load:
                    with self.assertRaises(ModelPreparationError):
                        self.runtime.start(Path(directory))
                    load.assert_not_called()
                self.assertEqual("failed", self.runtime.status()["state"])

    def test_corrupt_file_prevents_startup(self):
        """임시 사본에만 손상 파일을 만들어 모델 원본을 건드리지 않고 실패를 재현한다."""
        with tempfile.TemporaryDirectory() as directory:
            for key, name in MODEL_FILES.items():
                target = Path(directory) / name
                if key == "mlp":
                    target.write_bytes(b"invalid pickle for test")
                else:
                    target.symlink_to(MODEL_DIR / name)
            with self.assertRaises(ModelPreparationError):
                self.runtime.start(Path(directory))
        self.assertEqual("failed", self.runtime.status()["state"])
        with self.assertRaises(ModelNotReadyError):
            self.runtime.create_session()

    def test_incompatible_components_and_settings_are_rejected(self):
        """차원·행렬·클래스 순서·설정이 달라도 준비 완료로 표시하지 않는다."""
        for problem in ("input", "matrix", "bias", "classes", "warmup", "window", "hop", "ratio", "fixed"):
            with self.subTest(problem=problem):
                components = deepcopy(self.source_bundle.components)
                if problem == "input":
                    components["scaler"].n_features_in_ = 5
                elif problem == "matrix":
                    components["soft_iron"] = np.eye(2)
                elif problem == "bias":
                    components["hard_iron"]["Mag_X"] = float("nan")
                elif problem == "classes":
                    components["mlp"].classes_ = components["mlp"].classes_[::-1]
                else:
                    key, value = {
                        "warmup": ("warmup_policy", "unknown"), "window": ("window_size", 0),
                        "hop": ("hop_size", 2), "ratio": ("min_fill_ratio", float("nan")),
                        "fixed": ("strategy", "fixed"),
                    }[problem]
                    components["preproc_params"][key] = value
                with patch("services.model_loader.joblib.load", side_effect=list(components.values())):
                    with self.assertRaises(ModelPreparationError):
                        self.runtime.start()
                self.assertFalse(self.runtime.status()["model_loaded"])

    def test_smoke_prediction_failure_can_be_retried(self):
        """파일이 읽혀도 시험 예측에 실패하면 차단하며 이후 정상 시작을 허용한다."""
        with patch.object(model_service, "_predict_in_session", side_effect=RuntimeError("synthetic failure")):
            with self.assertRaises(ModelPreparationError):
                self.start_runtime()
        self.assertEqual("failed", self.runtime.status()["state"])
        self.start_runtime()
        self.assertTrue(self.runtime.status()["model_loaded"])
        self.assertEqual(0, self.runtime.create_session().buffered_count)

    def test_requests_are_rejected_until_smoke_prediction_finishes(self):
        """준비 검사가 진행 중일 때 상태 조회는 응답하고 세션 생성은 거절한다."""
        entered, release = Event(), Event()
        original = self.runtime._check_prediction

        def wait_before_validation(bundle):
            """시험 예측 직전에 멈춰 준비 중 요청의 동작을 확인한다."""
            entered.set()
            if not release.wait(5):
                raise TimeoutError("validation release missing")
            original(bundle)

        with patch.object(self.runtime, "_check_prediction", side_effect=wait_before_validation):
            with ThreadPoolExecutor(max_workers=1) as pool:
                work = pool.submit(self.start_runtime)
                try:
                    self.assertTrue(entered.wait(5))
                    self.assertEqual("loading", self.runtime.status()["state"])
                    with self.assertRaises(ModelNotReadyError):
                        self.runtime.create_session()
                finally:
                    release.set()
                work.result(5)
        self.assertTrue(self.runtime.status()["model_loaded"])

    def test_repeated_start_loads_once_and_keeps_smoke_history_private(self):
        """반복 준비 호출은 모델을 다시 읽지 않고 시험 이력은 사용자 세션에 남지 않는다."""
        with patch("services.model_runtime.load_model_bundle", return_value=self.bundle) as load:
            self.runtime.start()
            self.runtime.start()
            load.assert_called_once()
        self.assertEqual(0, self.runtime.create_session().buffered_count)
        self.assertTrue(self.runtime.status()["startup_warnings"])

    def test_stop_closes_sessions_and_restart_refuses_old_sessions(self):
        """종료 시 이력을 비우며 재시작해도 이전 모델 세션을 되살리지 않는다."""
        self.start_runtime()
        old = self.runtime.create_session()
        self.runtime.predict(self.sample, old)
        self.runtime.stop()
        self.runtime.stop()
        self.assertTrue(old.closed)
        self.assertEqual(0, old.buffered_count)
        self.start_runtime()
        with self.assertRaises(ModelNotReadyError):
            self.runtime.predict(self.sample, old)
        self.assertEqual(0, self.runtime.create_session().buffered_count)

    def test_stop_discards_inflight_result(self):
        """종료가 긴 계산을 기다리지 않고 완료된 이전 모델 결과를 차단한다."""
        self.start_runtime()
        session = self.runtime.create_session()
        computed, release = Event(), Event()
        original = model_service._predict_in_session

        def hold_result(data, current_session, bundle):
            """실제 결과를 만든 후 반환을 멈춰 서버 종료 경합을 만든다."""
            result = original(data, current_session, bundle)
            computed.set()
            if not release.wait(5):
                raise TimeoutError("prediction release missing")
            return result

        with patch.object(model_service, "_predict_in_session", side_effect=hold_result):
            with ThreadPoolExecutor(max_workers=2) as pool:
                work = pool.submit(self.runtime.predict, self.sample, session)
                try:
                    self.assertTrue(computed.wait(5))
                    pool.submit(self.runtime.stop).result(2)
                    self.assertEqual(0, session.buffered_count)
                finally:
                    release.set()
                with self.assertRaises((ModelNotReadyError, PredictionSessionClosedError)):
                    work.result(5)

    def test_foreign_session_is_rejected(self):
        """윈도우 길이가 같아도 다른 런타임의 세션을 현재 모델에 전달할 수 없다."""
        self.start_runtime()
        with self.assertRaises(ModelNotReadyError):
            self.runtime.predict(self.sample, PredictionSession(16))

    def test_schema_rejects_nonfinite_values_and_assignment(self):
        """여섯 입력의 NaN·양/음 무한대와 사후 대입을 모두 거절한다."""
        for field in self.sample.model_fields:
            for value in (float("nan"), float("inf"), -float("inf")):
                with self.subTest(field=field, value=value):
                    payload = self.sample.model_dump()
                    payload[field] = value
                    with self.assertRaises(ValidationError):
                        SensorInput(**payload)
                    with self.assertRaises(ValidationError):
                        setattr(self.sample, field, value)

    def test_internal_validation_bypass_cannot_mutate_buffer(self):
        """검증 없이 만든 입력 객체도 공용 추론 경계에서 거절해 이전 이력을 보존한다."""
        self.start_runtime()
        session = self.runtime.create_session()
        self.runtime.predict(self.sample, session)
        previous = {axis: session.window_values(axis) for axis in MAGNETIC_AXES}
        for field in self.sample.model_fields:
            for value in (float("nan"), float("inf"), -float("inf")):
                payload = self.sample.model_dump()
                payload[field] = value
                with self.assertRaises(InvalidSensorInputError):
                    self.runtime.predict(SensorInput.model_construct(**payload), session)
                self.assertEqual(previous, {axis: session.window_values(axis) for axis in MAGNETIC_AXES})

    def test_finite_input_overflow_is_rejected_without_history(self):
        """유한한 입력도 보정 연산에서 넘치면 버퍼에 저장하지 않는다."""
        self.bundle.components["soft_iron"] = np.eye(3) * 2
        self.start_runtime()
        session = self.runtime.create_session()
        huge = self.sample.model_copy(update={"Mag_X": 1.79e308})
        with self.assertRaises(InvalidSensorInputError):
            self.runtime.predict(huge, session)
        self.assertEqual(0, session.buffered_count)

    def test_failed_computation_restores_full_window(self):
        """가득 찬 윈도우에서도 실패한 샘플 때문에 가장 오래된 정상 값이 사라지지 않는다."""
        self.start_runtime()
        session = self.runtime.create_session()
        for index in range(16):
            self.runtime.predict(self.sample.model_copy(update={"Mag_X": float(index)}), session)
        previous = {axis: session.window_values(axis) for axis in MAGNETIC_AXES}
        with patch.object(self.bundle.components["scaler"], "transform", side_effect=RuntimeError("synthetic secret")):
            with self.assertRaises(PredictionFailedError):
                self.runtime.predict(self.sample, session)
        self.assertEqual(previous, {axis: session.window_values(axis) for axis in MAGNETIC_AXES})
        self.assertIsNotNone(self.runtime.predict(self.sample, session)[0])

    def test_nonfinite_model_output_is_rejected(self):
        """모델이 NaN 후보를 반환하면 정상 예측으로 취급하지 않고 이력을 되돌린다."""
        self.start_runtime()
        session = self.runtime.create_session()
        invalid = np.full((1, len(self.bundle.components["mlp"].classes_)), float("nan"))
        with patch.object(self.bundle.components["mlp"], "predict_proba", return_value=invalid):
            with self.assertRaises(PredictionFailedError):
                self.runtime.predict(self.sample, session)
        self.assertEqual(0, session.buffered_count)

    def test_status_returns_copies_of_mutable_metadata(self):
        """상태 응답 수정이 실제 모델 설정이나 경고 기록을 바꾸지 않는다."""
        self.start_runtime()
        snapshot = self.runtime.status()
        snapshot["preprocessing"]["window_size"] = 99
        snapshot["startup_warnings"].clear()
        self.assertEqual(16, self.runtime.status()["window_size"])
        self.assertTrue(self.runtime.status()["startup_warnings"])


if __name__ == "__main__":
    unittest.main()
