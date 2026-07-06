import unittest
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
EXPECTED_PERMISSION_LAYERS = {"unit", "integration", "scenarios", "regression", "replay", "live"}
EXPECTED_HOOK_LAYERS = {"unit", "integration", "scenarios", "regression", "live"}
EXPECTED_SHARED_LAYERS = {"fixtures"}
EXPECTED_PERMISSION_SCENARIOS = {"allow", "ask", "deny"}
EXPECTED_PERMISSION_LIVE_SCENARIOS = {"read_file.json", "write_file.json", "git_push.json"}
EXPECTED_HOOK_LIVE_SCENARIOS = {"post_tool_hook.json", "pre_tool_hook.json", "prevent_continuation.json"}


class DomainLayoutTests(unittest.TestCase):
    def _child_dirs(self, path: Path) -> set[str]:
        return {
            child.name
            for child in path.iterdir()
            if child.is_dir() and child.name != "__pycache__"
        }

    def test_domain_layers_match_the_documented_layout(self):
        self.assertEqual(self._child_dirs(ROOT / "tests" / "permission"), EXPECTED_PERMISSION_LAYERS)
        self.assertEqual(self._child_dirs(ROOT / "tests" / "hook"), EXPECTED_HOOK_LAYERS)
        self.assertEqual(self._child_dirs(ROOT / "tests" / "shared"), EXPECTED_SHARED_LAYERS)

    def test_permission_scenarios_are_grouped_by_permission_result(self):
        self.assertEqual(
            self._child_dirs(ROOT / "tests" / "permission" / "scenarios"),
            EXPECTED_PERMISSION_SCENARIOS,
        )

    def test_each_domain_has_real_prompt_scenarios(self):
        for domain in ("permission", "hook"):
            scenario_paths = sorted((ROOT / "tests" / domain / "scenarios").rglob("*.json"))
            scenario_paths = [path for path in scenario_paths if path.name != "permissions.json"]
            self.assertGreater(len(scenario_paths), 0, domain)
            for path in scenario_paths:
                data = json.loads(path.read_text(encoding="utf-8"))
                prompt = str(data.get("prompt", "")).strip()
                self.assertGreater(len(prompt), 4, str(path))

    def test_each_domain_has_live_model_smoke_tests(self):
        for domain in ("permission", "hook"):
            live_tests = sorted((ROOT / "tests" / domain / "live").glob("test_*.py"))
            self.assertGreater(len(live_tests), 0, domain)

    def test_each_domain_has_live_model_scenarios(self):
        expected = {
            "permission": EXPECTED_PERMISSION_LIVE_SCENARIOS,
            "hook": EXPECTED_HOOK_LIVE_SCENARIOS,
        }
        for domain, expected_names in expected.items():
            live_scenarios = sorted((ROOT / "tests" / domain / "live" / "scenarios").glob("*.json"))
            self.assertEqual({path.name for path in live_scenarios}, expected_names)
            for path in live_scenarios:
                data = json.loads(path.read_text(encoding="utf-8"))
                prompt = str(data.get("prompt", "")).strip()
                self.assertGreater(len(prompt), 4, str(path))
                self.assertIn("expected", data, str(path))

    def test_top_level_test_runner_exists(self):
        self.assertTrue((ROOT / "tests" / "run_all.py").exists())


if __name__ == "__main__":
    unittest.main()
