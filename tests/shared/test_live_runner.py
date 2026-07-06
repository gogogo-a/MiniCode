import io
import unittest
from contextlib import redirect_stdout

from tests.shared.live_runner import LiveScenario, LiveScenarioResult, print_live_flow


class LiveRunnerDisplayTests(unittest.TestCase):
    def test_show_flow_prints_user_facing_live_result(self):
        scenario = LiveScenario(
            name="live_read_file",
            prompt="读取 README.md",
            setup={},
            expected={},
            path=None,
        )
        result = LiveScenarioResult(
            name="live_read_file",
            passed=True,
            actual={
                "tools": ["read_file"],
                "events": ["PreToolUse", "PostToolUse"],
                "final": "文件内容里出现了 MiniCode。",
            },
            reason="",
        )

        output = io.StringIO()
        with redirect_stdout(output):
            print_live_flow(scenario, result)

        text = output.getvalue()
        self.assertIn("Live scenario: live_read_file", text)
        self.assertIn("Prompt:", text)
        self.assertIn("读取 README.md", text)
        self.assertIn("- read_file", text)
        self.assertIn("- PreToolUse", text)
        self.assertIn("文件内容里出现了 MiniCode。", text)
        self.assertIn("Result:", text)
        self.assertIn("PASS", text)
        self.assertNotIn("OPENAI_API_KEY", text)
        self.assertNotIn("OPENAI_BASE_URL", text)


if __name__ == "__main__":
    unittest.main()
