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
from hooks import HookEvent
import loop
import stop_hooks


class StopHookRegressionTests(unittest.TestCase):
    def setUp(self):
        hooks.clear_hooks()
        stop_hooks.reset()

    def tearDown(self):
        hooks.reset_default_hooks()

    def test_stop_hook_blocks_only_once(self):
        hooks.register_hook(HookEvent.STOP, stop_hooks.block_once)
        state = loop.LoopState()
        messages = []

        first = loop.handle_stop_hooks(messages, state)
        second = loop.handle_stop_hooks(messages, state)

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(stop_hooks.CALL_COUNT, 1)
        self.assertEqual(messages[-1]["content"], "Revise final answer once.")


if __name__ == "__main__":
    unittest.main()
