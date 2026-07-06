import json
import os
import sys
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
from hooks import HookEvent, HookOutcome, HookResult
from permission import PermissionBehavior
import tool_hooks


def tool_call(name: str, args: dict) -> dict:
    return {
        "id": "call_test",
        "function": {
            "name": name,
            "arguments": json.dumps(args),
        },
    }


class HookResultTests(unittest.TestCase):
    def setUp(self):
        hooks.clear_hooks()

    def tearDown(self):
        hooks.reset_default_hooks()

    def test_rejects_non_hook_result_return(self):
        hooks.register_hook(HookEvent.PRE_TOOL_USE, tool_hooks.invalid_return)

        with self.assertRaises(TypeError):
            hooks.trigger_hooks(HookEvent.PRE_TOOL_USE, tool_call("bash", {"command": "ls"}))

    def test_updated_input_last_hook_wins(self):
        hooks.register_hook(HookEvent.PRE_TOOL_USE, tool_hooks.update_command_to_pwd)
        hooks.register_hook(HookEvent.PRE_TOOL_USE, tool_hooks.update_command_to_ls)

        result = hooks.trigger_hooks(HookEvent.PRE_TOOL_USE, tool_call("bash", {"command": "cat README.md"}))

        self.assertEqual(result.updated_input, {"command": "ls"})

    def test_additional_context_appends_in_order(self):
        hooks.register_hook(HookEvent.POST_TOOL_USE, tool_hooks.add_first_context)
        hooks.register_hook(HookEvent.POST_TOOL_USE, tool_hooks.add_second_context)

        result = hooks.trigger_hooks(HookEvent.POST_TOOL_USE, tool_call("bash", {"command": "ls"}), "output")

        self.assertEqual(result.additional_context, "first context\nsecond context")

    def test_permission_behavior_uses_security_priority(self):
        hooks.register_hook(HookEvent.PRE_TOOL_USE, tool_hooks.allow_permission)
        hooks.register_hook(HookEvent.PRE_TOOL_USE, tool_hooks.ask_permission)
        hooks.register_hook(HookEvent.PRE_TOOL_USE, tool_hooks.deny_permission)

        result = hooks.trigger_hooks(HookEvent.PRE_TOOL_USE, tool_call("bash", {"command": "ls"}))

        self.assertEqual(result.permission_behavior, PermissionBehavior.DENY)

    def test_prevent_continuation_is_sticky(self):
        hooks.register_hook(HookEvent.POST_TOOL_USE, tool_hooks.stop_after_tool)

        result = hooks.trigger_hooks(HookEvent.POST_TOOL_USE, tool_call("bash", {"command": "ls"}), "output")

        self.assertTrue(result.prevent_continuation)
        self.assertEqual(result.message, "Stop after tool.")

    def test_blocking_outcome_is_preserved(self):
        hooks.register_hook(HookEvent.STOP, tool_hooks.blocking_stop)

        result = hooks.trigger_hooks(HookEvent.STOP, [])

        self.assertEqual(result.outcome, HookOutcome.BLOCKING)
        self.assertEqual(result.message, "Please provide a final answer.")


if __name__ == "__main__":
    unittest.main()
