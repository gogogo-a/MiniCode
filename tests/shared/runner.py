from __future__ import annotations

import contextlib
import importlib
import io
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SHARED_FIXTURES = ROOT / "tests" / "shared" / "fixtures"
if str(SHARED_FIXTURES) not in sys.path:
    sys.path.insert(0, str(SHARED_FIXTURES))

import hooks
import loop
import permission
import team
from hooks import HookEvent, HookResult
from permission import PermissionBehavior
from tests.shared.assertions import assert_expected
from tests.shared.event_collector import EventCollector
from tests.shared.fake_openai import fake_chat
from tests.shared.scenario import load_scenario
from tests.shared.workspace import scenario_workspace


@dataclass
class ScenarioRunResult:
    name: str
    passed: bool
    actual: dict[str, Any]
    events: list[dict[str, Any]]
    reason: str


def _permission_from_result(result: HookResult) -> str | None:
    return result.permission_behavior.value if result.permission_behavior else None


def _empty_permission_counts() -> dict[str, int]:
    return {
        "allow": 0,
        "ask": 0,
        "deny": 0,
        "passthrough": 0,
        "none": 0,
    }


@contextlib.contextmanager
def _scenario_context(scenario_path: Path, context: dict[str, Any]):
    original_project_rules = permission.PROJECT_PERMISSIONS_PATH
    original_agent = team.current_agent()
    original_scheduled = os.environ.get("SCHEDULED_MODE")
    project_rules = context.get("projectRules")
    if project_rules:
        permission.PROJECT_PERMISSIONS_PATH = (scenario_path.parent / str(project_rules)).resolve()
    permission.clear_session_rules()
    for rule in context.get("sessionRules", []):
        permission.add_session_rule(
            str(rule["toolName"]),
            str(rule["ruleBehavior"]),
            str(rule.get("ruleContent", "*")),
        )
    team.set_current_agent(str(context.get("agent", "scenario")))
    if context.get("scheduled"):
        os.environ["SCHEDULED_MODE"] = "1"
    else:
        os.environ.pop("SCHEDULED_MODE", None)
    try:
        yield
    finally:
        permission.PROJECT_PERMISSIONS_PATH = original_project_rules
        permission.clear_session_rules()
        team.set_current_agent(original_agent)
        if original_scheduled is None:
            os.environ.pop("SCHEDULED_MODE", None)
        else:
            os.environ["SCHEDULED_MODE"] = original_scheduled


def run_scenario(path: Path) -> ScenarioRunResult:
    scenario = load_scenario(path)
    collector = EventCollector()
    actual: dict[str, Any] = {
        "permission": None,
        "tool_executed": False,
        "updated_input": None,
        "prevent_continuation": False,
        "final": "",
    }

    def record_user_prompt(prompt):
        collector.record("UserPromptSubmit", prompt=prompt)
        return HookResult()

    def record_pre_tool(tool_call):
        name = tool_call["function"]["name"]
        result = hooks.permission_pre_tool_hook(tool_call)
        collector.record("PreToolUse", tool=name, permission=_permission_from_result(result))
        if result.permission_behavior:
            actual["permission"] = result.permission_behavior.value
        if result.updated_input:
            actual["updated_input"] = result.updated_input
        if result.prevent_continuation:
            actual["prevent_continuation"] = True
        return result

    def record_post_tool(tool_call, output):
        name = tool_call["function"]["name"]
        actual["tool_executed"] = True
        collector.record("PostToolUse", tool=name, output=str(output)[:120])
        return HookResult()

    def record_permission_denied(event):
        collector.record("PermissionDenied", **event)
        return HookResult()

    def record_permission_request(event):
        collector.record("PermissionRequest", **event)
        return HookResult()

    def load_callback(path_text: str):
        module_name, function_name = path_text.rsplit(".", 1)
        module = importlib.import_module(module_name)
        return getattr(module, function_name)

    hooks.clear_hooks()
    hooks.register_hook(HookEvent.USER_PROMPT_SUBMIT, record_user_prompt)
    for hook_path in scenario.setup.get("hooks", []):
        hook_event = HookEvent(str(hook_path["event"]))
        hooks.register_hook(hook_event, load_callback(str(hook_path["callback"])))
    hooks.register_hook(HookEvent.PRE_TOOL_USE, record_pre_tool)
    hooks.register_hook(HookEvent.POST_TOOL_USE, record_post_tool)
    hooks.register_hook(HookEvent.PERMISSION_REQUEST, record_permission_request)
    hooks.register_hook(HookEvent.PERMISSION_DENIED, record_permission_denied)

    with scenario_workspace(dict(scenario.setup.get("files", {}))), _scenario_context(scenario.path, scenario.context), fake_chat(scenario.fake_model):
        messages = [{"role": "user", "content": scenario.prompt}]
        hooks.trigger_hooks(HookEvent.USER_PROMPT_SUBMIT, scenario.prompt)
        with contextlib.redirect_stdout(io.StringIO()):
            loop.agent_loop(messages)
        if messages and messages[-1].get("role") == "assistant":
            actual["final"] = messages[-1].get("content", "")

    passed, reason = assert_expected(scenario.expected, actual, collector.events)
    hooks.reset_default_hooks()
    return ScenarioRunResult(scenario.name, passed, actual, collector.events, reason)


def discover_scenarios(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.json") if path.name != "permissions.json")


def run_suite(root: Path) -> tuple[list[ScenarioRunResult], dict[str, Any]]:
    results = [run_scenario(path) for path in discover_scenarios(root)]
    permission_counts = _empty_permission_counts()
    for result in results:
        behavior = result.actual.get("permission") or "none"
        permission_counts[behavior] = permission_counts.get(behavior, 0) + 1
    summary = {
        "total": len(results),
        "passed": sum(1 for result in results if result.passed),
        "failed": sum(1 for result in results if not result.passed),
        "permission_counts": permission_counts,
    }
    return results, summary


def format_summary(name: str, summary: dict[str, Any]) -> str:
    return "\n".join([
        f"{name} Scenario Suite",
        f"PASS {summary['passed']}/{summary['total']}",
    ])
