"""예측 비교 도구가 실제 차이와 잘못된 비교 조건을 놓치지 않는지 검증한다."""

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


DIRECTORY = Path(__file__).resolve().parent
RUNNER = DIRECTORY / "prediction_baseline.py"
REFERENCE = DIRECTORY / "reference.json"
ROOT = DIRECTORY.parents[1]


class PredictionBaselineTests(unittest.TestCase):
    """원본 기준을 보존한 채 별도 프로세스로 재현과 실패 감지를 확인한다."""

    def setUp(self):
        """각 검증에서 원본 바이트를 보관하고 변경용 임시 폴더를 준비한다."""
        self.original = REFERENCE.read_bytes()
        self.reference = json.loads(self.original)
        self.temporary = tempfile.TemporaryDirectory(prefix="magnavi-baseline-")
        self.addCleanup(self.temporary.cleanup)

    def tearDown(self):
        """성공·실패 어느 경로에서도 기준 결과가 바뀌지 않았는지 확인한다."""
        self.assertEqual(self.original, REFERENCE.read_bytes())

    def run_command(self, *arguments):
        """검증마다 새 Python 프로세스를 사용해 전역 버퍼의 영향을 차단한다."""
        return subprocess.run(
            [sys.executable, str(RUNNER), *arguments], cwd=ROOT,
            capture_output=True, text=True, timeout=30,
        )

    def assert_rejected(self, changed, error_path):
        """조작한 사본만 임시 파일에 쓰고 예상한 원인으로 실패하는지 확인한다."""
        path = Path(self.temporary.name) / "changed.json"
        path.write_text(json.dumps(changed, allow_nan=False), encoding="utf-8")
        result = self.run_command("verify", "--reference", str(path))
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertIn(error_path, result.stderr)

    def test_replay_matches_in_two_fresh_processes(self):
        """같은 144개 입력이 서로 다른 두 실행에서도 기준 결과와 일치한다."""
        for _ in range(2):
            result = self.run_command("verify")
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertIn("3개 시퀀스 / 144개 샘플", result.stdout)

    def test_changed_prediction_is_rejected(self):
        """점수가 같더라도 위치 코드가 바뀌면 회귀 실패로 판단한다."""
        changed = copy.deepcopy(self.reference)
        changed["results"]["cases"][0]["samples"][0]["prediction"] += 1
        self.assert_rejected(changed, ".prediction")

    def test_changed_score_is_rejected(self):
        """위치 코드가 같아도 후보 점수가 허용 오차를 넘으면 실패한다."""
        changed = copy.deepcopy(self.reference)
        changed["results"]["cases"][0]["samples"][0]["candidates"][0][1] += 0.01
        self.assert_rejected(changed, ".candidates[0][1]")

    def test_missing_sample_is_rejected(self):
        """앞부분만 비교하고 통과하지 않도록 시퀀스 길이 차이를 검사한다."""
        changed = copy.deepcopy(self.reference)
        changed["results"]["cases"][0]["samples"].pop()
        self.assert_rejected(changed, ".samples: 항목 개수")

    def test_candidate_order_change_is_rejected(self):
        """같은 후보 목록이라도 순위가 바뀌면 차이를 감지한다."""
        changed = copy.deepcopy(self.reference)
        changed["results"]["cases"][0]["samples"][0]["candidates"].reverse()
        self.assert_rejected(changed, ".candidates[0]")

    def test_different_model_hash_is_rejected(self):
        """모델이 다른 기준 파일은 추론 결과를 비교하기 전에 거절한다."""
        changed = copy.deepcopy(self.reference)
        changed["files"]["models"]["mlp_model_6input.pkl"] = "0" * 64
        self.assert_rejected(changed, "files.models.mlp_model_6input.pkl")

    def test_different_inputs_hash_is_rejected(self):
        """고정 입력이 달라진 기준 파일을 기존 회귀 기준으로 사용하지 않는다."""
        changed = copy.deepcopy(self.reference)
        changed["files"]["inputs_sha256"] = "0" * 64
        self.assert_rejected(changed, "files.inputs_sha256")

    def test_different_runtime_is_rejected(self):
        """실행 라이브러리가 다른 결과를 같은 조건의 비교로 오인하지 않는다."""
        changed = copy.deepcopy(self.reference)
        changed["environment"]["packages"]["scikit-learn"] = "1.5.1"
        self.assert_rejected(changed, "environment.packages.scikit-learn")

    def test_capture_refuses_to_overwrite_reference(self):
        """잘못된 현재 결과로 기존 기준을 자동 갱신하지 못하게 한다."""
        result = self.run_command("capture", "--output", str(REFERENCE))
        self.assertEqual(1, result.returncode)
        self.assertIn("덮어쓸 수 없습니다", result.stderr)


if __name__ == "__main__":
    unittest.main()
