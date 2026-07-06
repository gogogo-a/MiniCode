import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scenario_runner


class PermissionReplayTests(unittest.TestCase):
    def test_recorded_permission_replays_pass(self):
        results, summary = scenario_runner.run_suite(ROOT / "tests" / "permission" / "replay")

        self.assertGreater(summary["total"], 0)
        self.assertEqual(summary["failed"], 0)
        self.assertTrue(all(result.passed for result in results))


if __name__ == "__main__":
    unittest.main()
