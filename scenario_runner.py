from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import permission
import team


@dataclass
class ScenarioResult:
    name: str
    passed: bool
    expected_permission: str
    actual_permission: str
    expected_execute: bool
    actual_execute: bool
    reason: str


def _tool_call(name: str, args: dict[str, Any]) -> dict:
    return {
        "id": "scenario_call",
        "function": {
            "name": name,
            "arguments": json.dumps(args, ensure_ascii=False),
        },
    }


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_project_rules(path: Path | None):
    if path is None:
        return contextlib.nullcontext()
    original = permission.PROJECT_PERMISSIONS_PATH

    @contextlib.contextmanager
    def manager():
        permission.PROJECT_PERMISSIONS_PATH = path
        try:
            yield
        finally:
            permission.PROJECT_PERMISSIONS_PATH = original

    return manager()


def _apply_context(context: dict[str, Any]):
    original_scheduled = os.environ.get("SCHEDULED_MODE")
    original_agent = team.current_agent()
    scheduled = bool(context.get("scheduled", False))
    agent = str(context.get("agent", "lead"))

    @contextlib.contextmanager
    def manager():
        if scheduled:
            os.environ["SCHEDULED_MODE"] = "1"
        else:
            os.environ.pop("SCHEDULED_MODE", None)
        team.set_current_agent(agent)
        try:
            yield
        finally:
            if original_scheduled is None:
                os.environ.pop("SCHEDULED_MODE", None)
            else:
                os.environ["SCHEDULED_MODE"] = original_scheduled
            team.set_current_agent(original_agent)

    return manager()


def run_scenario(path: Path) -> ScenarioResult:
    data = _load_json(path)
    expected = data["expected"]
    tool = data["tool"]
    context = data.get("context", {})
    project_rules = context.get("projectRules")
    project_rules_path = (path.parent / project_rules).resolve() if project_rules else None
    call = _tool_call(tool["name"], tool.get("args", {}))

    permission.clear_session_rules()
    for rule in context.get("sessionRules", []):
        permission.add_session_rule(
            str(rule["toolName"]),
            str(rule["ruleBehavior"]),
            str(rule.get("ruleContent", "*")),
        )

    with _load_project_rules(project_rules_path), _apply_context(context):
        with contextlib.redirect_stdout(io.StringIO()):
            decision = permission.resolve_permission_decision(permission.check_permissions(call), call)

    actual_permission = decision.behavior.value
    actual_execute = actual_permission == permission.PermissionBehavior.ALLOW.value
    expected_permission = str(expected["permission"])
    expected_execute = bool(expected.get("execute", expected_permission == "allow"))
    passed = actual_permission == expected_permission and actual_execute == expected_execute
    reason = "" if passed else f"expected {expected_permission}, got {actual_permission}"

    return ScenarioResult(
        name=str(data["name"]),
        passed=passed,
        expected_permission=expected_permission,
        actual_permission=actual_permission,
        expected_execute=expected_execute,
        actual_execute=actual_execute,
        reason=reason,
    )


def discover_scenarios(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.json") if path.name != "permissions.json")


def run_suite(root: Path) -> tuple[list[ScenarioResult], dict[str, Any]]:
    results = [run_scenario(path) for path in discover_scenarios(root)]
    counts = {"allow": 0, "ask": 0, "deny": 0, "passthrough": 0}
    for result in results:
        counts[result.actual_permission] = counts.get(result.actual_permission, 0) + 1
    summary = {
        "total": len(results),
        "passed": sum(1 for result in results if result.passed),
        "failed": sum(1 for result in results if not result.passed),
        "permission_counts": counts,
    }
    return results, summary


def format_summary(summary: dict[str, Any]) -> str:
    counts = summary["permission_counts"]
    return "\n".join([
        "Permission Scenario Suite",
        f"PASS {summary['passed']}/{summary['total']}",
        f"ALLOW {counts.get('allow', 0)}",
        f"ASK {counts.get('ask', 0)}",
        f"DENY {counts.get('deny', 0)}",
        f"PASSTHROUGH {counts.get('passthrough', 0)}",
    ])


def main() -> int:
    parser = argparse.ArgumentParser(description="Run permission scenario tests.")
    parser.add_argument(
        "root",
        nargs="?",
        default=str(ROOT / "tests" / "permission" / "scenarios"),
        help="Scenario directory.",
    )
    args = parser.parse_args()
    results, summary = run_suite(Path(args.root))
    print(format_summary(summary))
    failed = [result for result in results if not result.passed]
    for result in failed:
        print(f"{result.name}: {result.reason}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
