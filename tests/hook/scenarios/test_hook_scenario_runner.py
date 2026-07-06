import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import hook_scenario_runner


class HookScenarioRunnerTests(unittest.TestCase):
    def test_hook_scenarios_pass_and_report_counts(self):
        results, summary = hook_scenario_runner.run_suite(ROOT / "tests" / "hook" / "scenarios")

        self.assertEqual(summary["failed"], 0)
        self.assertGreaterEqual(summary["total"], 1)
        self.assertTrue(all(result.passed for result in results))

    def test_failed_scenario_reports_expected_and_actual(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text(
                """
                {
                  "name": "bad_expectation",
                  "domain": "hook",
                  "prompt": "读取 README.md",
                  "setup": {"files": {"README.md": "MiniCode"}},
                  "fake_model": {
                    "tool_calls": [
                      {"name": "read_file", "args": {"path": "README.md"}}
                    ],
                    "final": "这是 MiniCode。"
                  },
                  "expected": {"permission": "deny"}
                }
                """,
                encoding="utf-8",
            )

            result = hook_scenario_runner.run_scenario(path)

        self.assertFalse(result.passed)
        self.assertEqual(result.actual["permission"], "allow")

    def test_summary_is_user_facing(self):
        _, summary = hook_scenario_runner.run_suite(ROOT / "tests" / "hook" / "scenarios")

        text = hook_scenario_runner.format_summary(summary)

        self.assertIn("Hook Scenario Suite", text)
        self.assertNotIn("debug", text.lower())
        self.assertNotIn("traceback", text.lower())


if __name__ == "__main__":
    unittest.main()
