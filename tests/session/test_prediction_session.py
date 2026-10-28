"""실제 모델로 세션 간 이력 격리와 종료·재접속 동작을 검증한다."""

from concurrent.futures import ThreadPoolExecutor
import contextlib
import io
from pathlib import Path
import sys
from threading import Barrier, Event, Lock
import unittest
from unittest.mock import patch
import warnings


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "baseline"))
import prediction_baseline as baseline

with warnings.catch_warnings(), contextlib.redirect_stdout(io.StringIO()):
    warnings.simplefilter("ignore")
    from services import legacy_prediction_service, model_service
    from services.prediction_session import PredictionSession, PredictionSessionClosedError
    from schemas.prediction import SensorInput


class ObservedLock:
    """두 번째 작업이 잠금에 도착했는지 관찰해 임의의 sleep 없이 경합을 만든다."""

    def __init__(self):
        """실제 잠금과 두 번째 진입 시도를 알리는 이벤트를 준비한다."""
        self.lock = Lock()
        self.counter_lock = Lock()
        self.attempts = 0
        self.second_attempt = Event()

    def __enter__(self):
        """진입 시도를 기록한 뒤 실제 잠금을 획득한다."""
        with self.counter_lock:
            self.attempts += 1
            if self.attempts == 2:
                self.second_attempt.set()
        self.lock.acquire()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        """성공과 예외 경로 모두에서 실제 잠금을 반환한다."""
        self.lock.release()


class PredictionSessionTests(unittest.TestCase):
    """기준 결과를 바꾸지 않고 세션별 실행 결과와 수명 경계를 확인한다."""

    @classmethod
    def setUpClass(cls):
        """기존 기준과 실행 조건이 같은지 확인하고 고정 합성 입력을 읽는다."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            model_service.initialize_model()
        cls.addClassCleanup(model_service.shutdown_model)
        cls.reference = baseline.read_json(baseline.REFERENCE_PATH)
        baseline.compare(cls.reference["environment"], baseline.environment(), "environment")
        baseline.compare(cls.reference["files"], baseline.fingerprints(), "files")
        cls.cases = baseline.load_inputs()
        cls.expected = {case["id"]: case["samples"] for case in cls.reference["results"]["cases"]}

    def setUp(self):
        """기준 단계에서 기록한 기존 경고를 테스트 출력에서만 접어 둔다."""
        context = warnings.catch_warnings()
        context.__enter__()
        warnings.simplefilter("ignore")
        self.addCleanup(context.__exit__, None, None, None)

    def new_session(self):
        """테스트가 실패해도 세션을 정리하도록 종료 함수를 등록한다."""
        session = model_service.create_prediction_session()
        self.addCleanup(session.close)
        return session

    def predict_sample(self, values, session):
        """실제 계산 결과를 기존 기준 파일과 같은 형식으로 변환한다."""
        data = SensorInput(**dict(zip(baseline.FIELDS, values, strict=True)))
        prediction, logged, candidates = model_service.run_prediction(data, session)
        return {
            "prediction": prediction,
            "logging_candidates": baseline.normalize_candidates(logged),
            "candidates": baseline.normalize_candidates(candidates),
            "buffered_count": session.buffered_count,
        }

    def test_interleaved_sessions_match_all_isolated_results(self):
        """세 시퀀스를 교차 처리해도 모든 144개 결과가 각각 단독 기준과 같다."""
        sessions = [self.new_session() for _ in self.cases]
        for index in range(48):
            for case, session in zip(self.cases, sessions, strict=True):
                actual = self.predict_sample(case["samples"][index], session)
                baseline.compare(self.expected[case["id"]][index], actual)

    def test_two_sessions_can_compute_concurrently(self):
        """서로 다른 세션은 전역 잠금 없이 계산에 진입하며 단독 결과를 유지한다."""
        sessions = [self.new_session(), self.new_session()]
        cases = self.cases[1:]
        barrier = Barrier(2, timeout=5)
        original = model_service._predict_in_session

        def synchronized_prediction(data, session, bundle):
            """두 세션이 모두 계산에 진입해야 다음 샘플을 처리하게 한다."""
            barrier.wait()
            return original(data, session, bundle)

        def run_case(case, session):
            """한 연결의 입력 순서는 유지하면서 다른 연결과 병렬 실행한다."""
            return [self.predict_sample(values, session) for values in case["samples"]]

        with patch.object(model_service, "_predict_in_session", side_effect=synchronized_prediction):
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(run_case, case, session) for case, session in zip(cases, sessions)]
                for case, future in zip(cases, futures):
                    baseline.compare(self.expected[case["id"]], future.result(timeout=10))

    def test_same_session_serializes_complete_predictions(self):
        """같은 세션의 두 번째 작업이 첫 계산의 버퍼와 추론 사이에 끼어들지 않는다."""
        session = self.new_session()
        observed = ObservedLock()
        session._prediction_lock = observed
        first_computed = Event()
        release_first = Event()
        second_entered = Event()
        original = model_service._predict_in_session
        calls = []

        def paused_prediction(data, current_session, bundle):
            """첫 결과 반환을 잠시 멈춰 두 번째 작업의 진입을 관찰한다."""
            calls.append(data)
            if len(calls) == 1:
                result = original(data, current_session, bundle)
                first_computed.set()
                if not release_first.wait(timeout=5):
                    raise TimeoutError("첫 작업 해제 신호가 없습니다.")
                return result
            second_entered.set()
            return original(data, current_session, bundle)

        values = self.cases[1]["samples"]
        with patch.object(model_service, "_predict_in_session", side_effect=paused_prediction):
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(self.predict_sample, values[0], session)
                try:
                    self.assertTrue(first_computed.wait(timeout=5))
                    second = pool.submit(self.predict_sample, values[1], session)
                    self.assertTrue(observed.second_attempt.wait(timeout=5))
                    self.assertFalse(second_entered.is_set())
                finally:
                    release_first.set()
                first_result = first.result(timeout=5)
                second_result = second.result(timeout=5)
        # 외부에서 읽는 buffered_count는 후속 호출이 갱신했을 수 있어 결과 본체만 비교한다.
        for index, result in enumerate([first_result, second_result]):
            for key in ["prediction", "candidates", "logging_candidates"]:
                baseline.compare(self.expected["smooth_variation"][index][key], result[key])
        self.assertEqual(2, session.buffered_count)

    def test_close_clears_history_and_rejects_reuse(self):
        """종료는 반복 호출해도 안전하며 해당 객체로 다시 추론할 수 없다."""
        session = self.new_session()
        self.predict_sample(self.cases[1]["samples"][0], session)
        session.close()
        session.close()
        self.assertTrue(session.closed)
        self.assertEqual(0, session.buffered_count)
        with self.assertRaises(PredictionSessionClosedError):
            self.predict_sample(self.cases[1]["samples"][1], session)

    def test_reconnect_starts_with_empty_history(self):
        """이전 연결을 종료한 뒤 만든 세션은 단독 실행의 첫 샘플부터 재현한다."""
        old_session = self.new_session()
        for values in self.cases[2]["samples"]:
            self.predict_sample(values, old_session)
        old_session.close()
        new_session = self.new_session()
        self.assertEqual(0, new_session.buffered_count)
        for index, values in enumerate(self.cases[1]["samples"]):
            baseline.compare(self.expected["smooth_variation"][index], self.predict_sample(values, new_session))

    def test_close_discards_running_result_without_waiting_for_cpu(self):
        """계산이 아직 반환되지 않아도 종료가 완료되고, 늦게 나온 결과는 거절된다."""
        session = self.new_session()
        computed = Event()
        release = Event()
        original = model_service._predict_in_session

        def paused_result(data, current_session, bundle):
            """실제 추론을 마친 뒤 반환만 멈춰 종료와의 경합을 재현한다."""
            result = original(data, current_session, bundle)
            computed.set()
            if not release.wait(timeout=5):
                raise TimeoutError("결과 반환 해제 신호가 없습니다.")
            return result

        with patch.object(model_service, "_predict_in_session", side_effect=paused_result):
            with ThreadPoolExecutor(max_workers=2) as pool:
                work = pool.submit(self.predict_sample, self.cases[0]["samples"][0], session)
                try:
                    self.assertTrue(computed.wait(timeout=5))
                    pool.submit(session.close).result(timeout=2)
                    self.assertTrue(session.closed)
                    self.assertEqual(0, session.buffered_count)
                finally:
                    release.set()
                with self.assertRaises(PredictionSessionClosedError):
                    work.result(timeout=5)

    def test_queued_prediction_is_rejected_after_close(self):
        """잠금을 기다리던 작업도 종료 뒤에는 버퍼를 다시 채우지 않는다."""
        session = self.new_session()
        observed = ObservedLock()
        session._prediction_lock = observed
        # 주 스레드가 먼저 계산 구간을 점유하고 두 번째 진입을 대기시킨다.
        observed.__enter__()
        with ThreadPoolExecutor(max_workers=1) as pool:
            work = pool.submit(self.predict_sample, self.cases[0]["samples"][0], session)
            try:
                self.assertTrue(observed.second_attempt.wait(timeout=5))
                session.close()
            finally:
                observed.__exit__(None, None, None)
            with self.assertRaises(PredictionSessionClosedError):
                work.result(timeout=5)
        self.assertEqual(0, session.buffered_count)

    def test_session_is_required_and_window_size_must_match(self):
        """세션 누락이나 다른 윈도우 크기가 공유 상태 또는 다른 전처리로 이어지지 않는다."""
        data = SensorInput(**dict(zip(baseline.FIELDS, self.cases[0]["samples"][0])))
        with self.assertRaises(TypeError):
            model_service.run_prediction(data)
        with self.assertRaises(ValueError):
            model_service.run_prediction(data, PredictionSession(window_size=8))

    def test_invalid_window_sizes_are_rejected(self):
        """비어 있는 윈도우나 bool·소수 크기로 세션이 만들어지지 않는다."""
        for size in [0, -1, True, 1.5, "16"]:
            with self.subTest(size=size), self.assertRaises(ValueError):
                PredictionSession(window_size=size)

    def test_window_is_bounded_and_snapshot_does_not_mutate_it(self):
        """최근 16개만 남고 외부에서 얻은 윈도우 값이 내부 상태를 변경하지 않는다."""
        session = self.new_session()
        with session.prediction():
            for index in range(20):
                session.append_magnetic_sample({axis: float(index) for axis in model_service.MAG_COLS})
        self.assertEqual(tuple(float(index) for index in range(4, 20)), session.window_values("Mag_X"))
        snapshot = list(session.window_values("Mag_X"))
        snapshot.clear()
        self.assertEqual(16, session.buffered_count)

    def test_exception_releases_session_lock(self):
        """계산이 실패해도 잠금이 남아 이후 작업이 영구 대기하지 않는다."""
        session = self.new_session()
        values = self.cases[0]["samples"][0]
        with patch.object(model_service, "_predict_in_session", side_effect=RuntimeError("synthetic failure")):
            with self.assertRaises(RuntimeError):
                self.predict_sample(values, session)
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(self.predict_sample, values, session).result(timeout=5)
        baseline.compare(self.expected["constant"][0], result)

    def test_legacy_session_cannot_contaminate_explicit_session(self):
        """호환 경로의 공유 이력이 새 세션 기반 추론으로 들어오지 않는다."""
        session = self.new_session()
        legacy_session = self.new_session()
        with patch.object(legacy_prediction_service, "_legacy_session", legacy_session):
            for index in range(48):
                legacy_data = SensorInput(**dict(zip(baseline.FIELDS, self.cases[2]["samples"][index])))
                legacy_prediction_service.run_legacy_prediction(legacy_data)
                result = self.predict_sample(self.cases[1]["samples"][index], session)
                baseline.compare(self.expected["smooth_variation"][index], result)

    def test_shared_legacy_path_still_reproduces_original_problem(self):
        """호환 경로의 남은 제약을 숨기지 않고 수정 전과 같은 혼합 결과를 재현한다."""
        legacy_session = self.new_session()
        cases = self.cases[1:]
        differences = [{"prediction": 0, "candidates": 0} for _ in cases]
        with patch.object(legacy_prediction_service, "_legacy_session", legacy_session):
            for index in range(48):
                for case_index, case in enumerate(cases):
                    data = SensorInput(**dict(zip(baseline.FIELDS, case["samples"][index])))
                    prediction, _, candidates = legacy_prediction_service.run_legacy_prediction(data)
                    expected = self.expected[case["id"]][index]
                    differences[case_index]["prediction"] += prediction != expected["prediction"]
                    differences[case_index]["candidates"] += baseline.normalize_candidates(candidates) != expected["candidates"]
        self.assertEqual([{"prediction": 11, "candidates": 43}, {"prediction": 14, "candidates": 44}], differences)


if __name__ == "__main__":
    unittest.main()
