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
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import permission
import team


def tool_call(name: str, args: dict) -> dict:
    return {
        "id": "call_test",
        "function": {
            "name": name,
            "arguments": json.dumps(args),
        },
    }


class PermissionDecisionTests(unittest.TestCase):
    def setUp(self):
        permission.clear_session_rules()
        permission.PROJECT_PERMISSIONS_PATH = ROOT / ".agent" / "missing-permissions.json"
        os.environ.pop("SCHEDULED_MODE", None)
        team.set_current_agent("lead")

    def test_dangerous_bash_is_denied(self):
        decision = permission.check_permissions(tool_call("bash", {"command": "rm -rf /"}))

        self.assertEqual(decision.behavior, permission.PermissionBehavior.DENY)
        self.assertIn("rm -rf /", decision.reason)

    def test_read_file_is_allowed(self):
        decision = permission.check_permissions(tool_call("read_file", {"path": "README.md"}))

        self.assertEqual(decision.behavior, permission.PermissionBehavior.ALLOW)

    def test_write_file_asks_for_permission(self):
        decision = permission.check_permissions(tool_call("write_file", {"path": "x.txt", "content": "x"}))

        self.assertEqual(decision.behavior, permission.PermissionBehavior.ASK)

    def test_unknown_tool_passthrough_becomes_ask_at_resolution(self):
        call = tool_call("custom_tool", {})
        decision = permission.check_permissions(call)
        resolved = permission.resolve_permission_decision(decision, call)

        self.assertEqual(decision.behavior, permission.PermissionBehavior.PASSTHROUGH)
        self.assertEqual(resolved.behavior, permission.PermissionBehavior.ASK)

    def test_project_deny_rule_overrides_session_allow_rule(self):
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
            permission.add_session_rule("bash", "allow", "npm publish:*")

            decision = permission.check_permissions(tool_call("bash", {"command": "npm publish:pkg"}))

        self.assertEqual(decision.behavior, permission.PermissionBehavior.DENY)
        self.assertEqual(decision.source, "project")

    def test_session_rule_can_allow_matching_command(self):
        permission.add_session_rule("bash", "allow", "python -m unittest:*")

        decision = permission.check_permissions(tool_call("bash", {"command": "python -m unittest discover"}))

        self.assertEqual(decision.behavior, permission.PermissionBehavior.ALLOW)
        self.assertEqual(decision.source, "session")


if __name__ == "__main__":
    unittest.main()
