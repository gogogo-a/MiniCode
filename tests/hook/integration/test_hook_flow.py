import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


os.environ.setdefault("OPENAI_BASE_URL", "http://example.test")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("OPENAI_MODEL", "test-model")

ROOT = Path(__file__).resolve().parents[3]
SHARED_FIXTURES = ROOT / "tests" / "shared" / "fixtures"
for path in (ROOT, SHARED_FIXTURES):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import hooks
import permission
from hooks import HookEvent, HookOutcome, HookResult
import permission_hooks


def tool_call(name: str, args: dict) -> dict:
    return {
        "id": "call_test",
        "function": {
            "name": name,
            "arguments": json.dumps(args),
        },
    }


class HookFlowTests(unittest.TestCase):
    def setUp(self):
        hooks.clear_hooks()
        permission.clear_session_rules()
        permission.PROJECT_PERMISSIONS_PATH = ROOT / ".agent" / "missing-permissions.json"
        os.environ.pop("SCHEDULED_MODE", None)

    def tearDown(self):
        hooks.reset_default_hooks()

    def test_hook_allow_does_not_bypass_project_deny(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "permissions.json"
            path.write_text(
                json.dumps({
                    "rules": [
                        {
                            "toolName": "bash",
                            "ruleBehavior": "deny",
                            "ruleContent": "npm publish:*",
                        }
                    ]
                }),
                encoding="utf-8",
            )
            permission.PROJECT_PERMISSIONS_PATH = path
            hooks.register_hook(HookEvent.PRE_TOOL_USE, permission_hooks.allow_everything)

            result = permission.permission_hook(tool_call("bash", {"command": "npm publish:pkg"}))

        self.assertIn("Permission denied", result.message)

    def test_permission_denied_event_fires_for_denied_tool(self):
        seen = []

        def capture(event):
            seen.append(event)
            return HookResult()

        hooks.register_hook(HookEvent.PERMISSION_DENIED, capture)

        permission.permission_hook(tool_call("bash", {"command": "rm -rf /"}))

        self.assertEqual(seen[0]["tool_name"], "bash")
        self.assertIn("rm -rf /", seen[0]["reason"])

    def test_post_tool_use_failure_event_can_capture_error_output(self):
        seen = []

        def capture(tool_call_arg, output):
            seen.append((tool_call_arg, output))
            return HookResult(outcome=HookOutcome.SUCCESS)

        hooks.register_hook(HookEvent.POST_TOOL_USE_FAILURE, capture)
        result = hooks.trigger_hooks(
            HookEvent.POST_TOOL_USE_FAILURE,
            tool_call("bash", {"command": "bad"}),
            "Error: failed",
        )

        self.assertEqual(seen[0][1], "Error: failed")
        self.assertEqual(result.outcome, HookOutcome.SUCCESS)


if __name__ == "__main__":
    unittest.main()
