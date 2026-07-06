import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scenario_runner


class ScenarioRunnerTests(unittest.TestCase):
    def test_permission_scenarios_pass_and_report_counts(self):
        results, summary = scenario_runner.run_suite(ROOT / "tests" / "permission" / "scenarios")

        self.assertEqual(summary["failed"], 0)
        self.assertGreaterEqual(summary["permission_counts"]["allow"], 1)
        self.assertGreaterEqual(summary["permission_counts"]["ask"], 1)
        self.assertGreaterEqual(summary["permission_counts"]["deny"], 1)
        self.assertTrue(all(result.passed for result in results))

    def test_failed_scenario_reports_expected_and_actual_permission(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text(
                """
                {
                  "name": "bad_expectation",
                  "prompt": "Read README",
                  "tool": {"name": "read_file", "args": {"path": "README.md"}},
                  "expected": {"permission": "deny", "execute": false}
                }
                """,
                encoding="utf-8",
            )

            result = scenario_runner.run_scenario(path)

        self.assertFalse(result.passed)
        self.assertEqual(result.expected_permission, "deny")
        self.assertEqual(result.actual_permission, "allow")

    def test_summary_is_user_facing(self):
        _, summary = scenario_runner.run_suite(ROOT / "tests" / "permission" / "scenarios")

        text = scenario_runner.format_summary(summary)

        self.assertIn("Permission Scenario Suite", text)
        self.assertNotIn("debug", text.lower())
        self.assertNotIn("traceback", text.lower())


if __name__ == "__main__":
    unittest.main()
