import os
import sys
import tempfile
import unittest
from pathlib import Path


os.environ.setdefault("OPENAI_BASE_URL", "http://example.test")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("OPENAI_MODEL", "test-model")

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.shared.runner import run_scenario
from tests.shared.scenario import load_scenario


class SharedScenarioRunnerTests(unittest.TestCase):
    def test_prompt_drives_agent_with_fake_model_tool_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            scenario = Path(tmp) / "allow_read.json"
            scenario.write_text(
                """
                {
                  "name": "allow read",
                  "domain": "permission",
                  "prompt": "读取 README.md",
                  "setup": {
                    "files": {
                      "README.md": "MiniCode"
                    }
                  },
                  "fake_model": {
                    "tool_calls": [
                      {
                        "name": "read_file",
                        "args": {
                          "path": "README.md"
                        }
                      }
                    ],
                    "final": "这是 MiniCode。"
                  },
                  "expected": {
                    "permission": "allow",
                    "tool_executed": true,
                    "events": ["UserPromptSubmit", "PreToolUse", "PostToolUse"],
                    "final_contains": "MiniCode"
                  }
                }
                """,
                encoding="utf-8",
            )

            result = run_scenario(scenario)

        self.assertTrue(result.passed, result.reason)
        self.assertEqual(result.actual["permission"], "allow")
        self.assertTrue(result.actual["tool_executed"])
        self.assertIn("MiniCode", result.actual["final"])

    def test_scenario_requires_fake_model_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            scenario = Path(tmp) / "old_schema.json"
            scenario.write_text(
                """
                {
                  "name": "old schema",
                  "domain": "permission",
                  "prompt": "读取 README.md",
                  "model": {
                    "tool_calls": [
                      {"name": "read_file", "args": {"path": "README.md"}}
                    ],
                    "final": "这是 MiniCode。"
                  },
                  "expected": {"permission": "allow"}
                }
                """,
                encoding="utf-8",
            )

            with self.assertRaises(KeyError):
                load_scenario(scenario)


if __name__ == "__main__":
    unittest.main()
