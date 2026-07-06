"""
Hook 模块：提供结构化生命周期事件，调用方解释 HookResult 后再改变 agent 状态。

函数职责：
- HookEvent / HookOutcome / HookResult：定义 hook 事件和结构化结果。
- register_hook / trigger_hooks：注册并触发 hook，按注册顺序合并结果。
- clear_hooks / reset_default_hooks：测试和运行时重置 hook 注册表。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable

from permission import PermissionBehavior, permission_hook, tool_call_name_args


class HookEvent(str, Enum):
    USER_PROMPT_SUBMIT = "UserPromptSubmit"
    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    STOP = "Stop"
    POST_TOOL_USE_FAILURE = "PostToolUseFailure"
    PERMISSION_REQUEST = "PermissionRequest"
    PERMISSION_DENIED = "PermissionDenied"
    PRE_COMPACT = "PreCompact"
    POST_COMPACT = "PostCompact"


class HookOutcome(str, Enum):
    SUCCESS = "success"
    BLOCKING = "blocking"
    NON_BLOCKING_ERROR = "non_blocking_error"
    CANCELLED = "cancelled"


@dataclass
class HookResult:
    outcome: HookOutcome = HookOutcome.SUCCESS
    updated_input: dict | None = None
    additional_context: str | None = None
    permission_behavior: PermissionBehavior | None = None
    prevent_continuation: bool = False
    message: str | None = None


HOOKS: dict[str, list[Callable]] = {event.value: [] for event in HookEvent}


def _event_name(event: HookEvent | str) -> str:
    return event.value if isinstance(event, HookEvent) else str(event)


def _empty_result() -> HookResult:
    return HookResult()


def _stronger_permission(current: PermissionBehavior | None, new: PermissionBehavior | None) -> PermissionBehavior | None:
    priority = {
        None: 0,
        PermissionBehavior.ALLOW: 1,
        PermissionBehavior.ASK: 2,
        PermissionBehavior.DENY: 3,
    }
    return new if priority.get(new, 0) > priority.get(current, 0) else current


def _merge_results(base: HookResult, item: HookResult) -> HookResult:
    if item.updated_input:
        merged_input = dict(base.updated_input or {})
        merged_input.update(item.updated_input)
        base.updated_input = merged_input
    if item.additional_context:
        base.additional_context = (
            f"{base.additional_context}\n{item.additional_context}"
            if base.additional_context else item.additional_context
        )
    base.permission_behavior = _stronger_permission(base.permission_behavior, item.permission_behavior)
    base.prevent_continuation = base.prevent_continuation or item.prevent_continuation
    if item.outcome != HookOutcome.SUCCESS:
        base.outcome = item.outcome
    if item.message:
        base.message = item.message
    return base


def register_hook(event: HookEvent | str, callback):
    HOOKS.setdefault(_event_name(event), []).append(callback)


def clear_hooks():
    for callbacks in HOOKS.values():
        callbacks.clear()


def trigger_hooks(event: HookEvent | str, *args) -> HookResult:
    result = _empty_result()
    for callback in HOOKS.get(_event_name(event), []):
        item = callback(*args)
        if item is None:
            continue
        if not isinstance(item, HookResult):
            raise TypeError("hook callbacks must return HookResult or None")
        result = _merge_results(result, item)
    return result


def permission_pre_tool_hook(tool_call):
    return permission_hook(tool_call)


def large_output_hook(tool_call, output):
    if len(str(output)) > 100000:
        tool_name, _ = tool_call_name_args(tool_call)
        return HookResult(
            outcome=HookOutcome.NON_BLOCKING_ERROR,
            message=f"Large output from {tool_name}: {len(str(output))} chars",
        )
    return HookResult()


def reset_default_hooks():
    clear_hooks()
    register_hook(HookEvent.PRE_TOOL_USE, permission_pre_tool_hook)
    register_hook(HookEvent.POST_TOOL_USE, large_output_hook)


reset_default_hooks()
