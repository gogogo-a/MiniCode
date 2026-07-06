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
from permission import PermissionBehavior


def tool_call(name: str, args: dict) -> dict:
    return {
        "id": "call_test",
        "function": {
            "name": name,
            "arguments": json.dumps(args),
        },
    }


class PermissionHookFlowTests(unittest.TestCase):
    def setUp(self):
        permission.clear_session_rules()
        permission.PROJECT_PERMISSIONS_PATH = ROOT / ".agent" / "missing-permissions.json"
        os.environ.pop("SCHEDULED_MODE", None)
        team.set_current_agent("lead")

    def test_permission_hook_blocks_deny(self):
        result = permission.permission_hook(tool_call("bash", {"command": "sudo reboot"}))

        self.assertEqual(result.permission_behavior, PermissionBehavior.DENY)
        self.assertIn("Permission denied", result.message)

    def test_scheduled_mode_rejects_ask_without_prompting(self):
        os.environ["SCHEDULED_MODE"] = "1"

        result = permission.permission_hook(tool_call("write_file", {"path": "x.txt", "content": "x"}))

        self.assertEqual(result.permission_behavior, PermissionBehavior.DENY)
        self.assertIn("Permission denied", result.message)

    def test_teammate_ask_bubbles_permission_request_to_lead(self):
        with tempfile.TemporaryDirectory() as tmp:
            team.MAILBOX_DIR = Path(tmp)
            team.set_current_agent("alice")

            result = permission.permission_hook(tool_call("write_file", {"path": "x.txt", "content": "x"}))
            messages = team.BUS.read_inbox("lead")

        self.assertEqual(result.permission_behavior, PermissionBehavior.ASK)
        self.assertIn("Permission requested", result.message)
        self.assertEqual(messages[0]["type"], "permission_request")
        self.assertEqual(messages[0]["from"], "alice")
        self.assertEqual(messages[0]["metadata"]["tool_name"], "write_file")

    def test_permission_response_is_delivered_to_teammate_context(self):
        messages = []
        stop = team.dispatch_teammate_message(
            "alice",
            {
                "type": "permission_response",
                "content": "Allowed once.",
                "metadata": {"request_id": "perm_000001", "approve": True},
            },
            messages,
        )

        self.assertFalse(stop)
        self.assertIn("PERMISSION_APPROVED", messages[-1]["content"])


if __name__ == "__main__":
    unittest.main()
