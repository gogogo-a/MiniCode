import os
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from tests.shared.live_runner import discover_live_scenarios, run_live_scenario


class LiveHookSmokeTests(unittest.TestCase):
    def test_live_hook_scenarios(self):
        scenario_root = Path(__file__).resolve().parent / "scenarios"
        show_flow = os.getenv("SHOW_LIVE_FLOW") == "1"
        for scenario in discover_live_scenarios(scenario_root):
            with self.subTest(scenario=scenario.name):
                result = run_live_scenario(scenario, show_flow=show_flow)
                self.assertTrue(result.passed, result.reason)


if __name__ == "__main__":
    unittest.main()
