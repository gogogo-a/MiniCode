from __future__ import annotations

import json
import contextlib
import io
import importlib
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import hooks
from hooks import HookEvent, HookResult
from tests.shared.event_collector import EventCollector
from tests.shared.live_model import run_live_prompt


ROOT = Path(__file__).resolve().parents[2]
SHARED_FIXTURES = ROOT / "tests" / "shared" / "fixtures"
if str(SHARED_FIXTURES) not in sys.path:
    sys.path.insert(0, str(SHARED_FIXTURES))


@dataclass
class LiveScenario:
    name: str
    prompt: str
    setup: dict[str, Any]
    expected: dict[str, Any]
    path: Path | None


@dataclass
class LiveScenarioResult:
    name: str
    passed: bool
    actual: dict[str, Any]
    reason: str


def load_live_scenario(path: Path) -> LiveScenario:
    data = json.loads(path.read_text(encoding="utf-8"))
    return LiveScenario(
        name=str(data["name"]),
        prompt=str(data["prompt"]),
        setup=dict(data.get("setup", {})),
        expected=dict(data["expected"]),
        path=path,
    )


def run_live_scenario(path: Path, show_flow: bool = False) -> LiveScenarioResult:
    scenario = load_live_scenario(path)
    collector = EventCollector()
    with live_event_collection(collector):
        for hook_config in scenario.setup.get("hooks", []):
            hook_event = HookEvent(str(hook_config["event"]))
            hooks.register_hook(hook_event, load_callback(str(hook_config["callback"])))
        output_context = contextlib.nullcontext() if show_flow else contextlib.redirect_stdout(io.StringIO())
        error_context = contextlib.nullcontext() if show_flow else contextlib.redirect_stderr(io.StringIO())
        with output_context, error_context:
            result = run_live_prompt(
                scenario.name,
                scenario.prompt,
                dict(scenario.setup.get("files", {})),
            )
    actual = {
        "tools": result.tool_names,
        "tool_outputs": result.tool_outputs,
        "final": result.final,
        "events": collector.names(),
    }
    passed, reason = assert_live_expected(scenario.expected, actual)
    live_result = LiveScenarioResult(scenario.name, passed, actual, reason)
    if show_flow:
        print_live_flow(scenario, live_result)
    return live_result


def assert_live_expected(expected: dict[str, Any], actual: dict[str, Any]) -> tuple[bool, str]:
    for tool in expected.get("tools", []):
        if str(tool) not in actual["tools"]:
            return False, f"missing tool: {tool}"
    for event in expected.get("events", []):
        if str(event) not in actual["events"]:
            return False, f"missing event: {event}"
    if "final_contains" in expected and str(expected["final_contains"]) not in str(actual["final"]):
        return False, "final answer mismatch"
    if "tool_output_contains" in expected:
        needle = str(expected["tool_output_contains"])
        if not any(needle in output for output in actual.get("tool_outputs", [])):
            return False, "tool output mismatch"
    if "max_tool_calls" in expected and len(actual["tools"]) > int(expected["max_tool_calls"]):
        return False, "too many tool calls"
    return True, ""


def discover_live_scenarios(root: Path) -> list[Path]:
    return sorted(root.glob("*.json"))


def load_callback(path_text: str):
    module_name, function_name = path_text.rsplit(".", 1)
    module = importlib.import_module(module_name)
    return getattr(module, function_name)


def print_live_flow(scenario: LiveScenario, result: LiveScenarioResult) -> None:
    print(f"Live scenario: {scenario.name}")
    print("Prompt:")
    print(scenario.prompt)
    print("Tools:")
    for tool in result.actual.get("tools", []):
        print(f"- {tool}")
    print("Events:")
    for event in result.actual.get("events", []):
        print(f"- {event}")
    print("Final:")
    print(result.actual.get("final", ""))
    print("Result:")
    print("PASS" if result.passed else f"FAIL: {result.reason}")


@contextlib.contextmanager
def live_event_collection(collector: EventCollector):
    original_hooks = {name: list(callbacks) for name, callbacks in hooks.HOOKS.items()}

    def record_pre_tool(tool_call):
        collector.record("PreToolUse", tool=tool_call["function"]["name"])
        return HookResult()

    def record_post_tool(tool_call, _output):
        collector.record("PostToolUse", tool=tool_call["function"]["name"])
        return HookResult()

    def record_permission_request(event):
        collector.record("PermissionRequest", **event)
        return HookResult()

    def record_permission_denied(event):
        collector.record("PermissionDenied", **event)
        return HookResult()

    hooks.register_hook(HookEvent.PRE_TOOL_USE, record_pre_tool)
    hooks.register_hook(HookEvent.POST_TOOL_USE, record_post_tool)
    hooks.register_hook(HookEvent.PERMISSION_REQUEST, record_permission_request)
    hooks.register_hook(HookEvent.PERMISSION_DENIED, record_permission_denied)
    try:
        yield
    finally:
        hooks.HOOKS.clear()
        hooks.HOOKS.update(original_hooks)
