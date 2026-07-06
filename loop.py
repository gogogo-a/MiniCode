"""
主循环模块：连接 system prompt、memory、compact、LLM 恢复、后台任务、团队邮箱、MCP 工具池和工具执行。

函数职责：
- _call_agent：发起一次主 agent LLM 请求，遇到上下文过长时 reactive compact 后重试。
- _handle_max_tokens：处理输出截断，先升级 max_tokens，再追加 continuation prompt。
- is_length_finish：兼容判断 length / max_tokens 截断结束原因。
- execute_tool_call：执行一次普通工具调用。
- inject_background_notifications：把已经完成的后台任务通知追加进上下文。
- inject_team_inbox：把队友发给 Lead 的邮箱消息追加进上下文。
- current_tool_pool：每轮重新组装内置工具和已连接 MCP 工具。
- agent_loop：核心 agent 循环，执行 tool_calls，追加 tool 结果，直到模型停止。
"""

import json

from background_tasks import collect_background_results, should_run_background, start_background_task
from compact import compact_history, compact_messages, reactive_compact
from config import (
    CONTINUATION_PROMPT,
    DEFAULT_MAX_TOKENS,
    ESCALATED_MAX_TOKENS,
    MAX_CONTINUATIONS,
    MAX_REACTIVE_COMPACTS,
)
from hooks import trigger_hooks
from llm import RecoveryState, chat_with_system, is_prompt_too_long_error
from memory import consolidate_memories, extract_memories, inject_memories, load_memories
from mcp_plugin import assemble_tool_pool
from reminders import collect_reminders
from system_prompt import get_system_prompt, update_context
from team import inject_lead_inbox
from tools import TOOL_HANDLERS, TOOLS, call_tool


def current_tool_pool() -> tuple[list, dict]:
    return assemble_tool_pool(TOOLS, TOOL_HANDLERS)


def _call_agent(messages: list, request_messages: list, state: RecoveryState, max_tokens: int, tools: list) -> dict | None:
    while True:
        try:
            context = update_context()
            system = get_system_prompt(context)
            return chat_with_system(request_messages, tools, system, state, max_tokens)
        except Exception as error:
            if is_prompt_too_long_error(error) and state.reactive_compact_count < MAX_REACTIVE_COMPACTS:
                messages[:] = reactive_compact(messages)
                request_messages[:] = messages
                state.reactive_compact_count += 1
                continue
            if is_prompt_too_long_error(error):
                messages.append({"role": "assistant", "content": "[Error] Context too large, cannot continue."})
                return None
            raise


def _handle_max_tokens(messages: list, message: dict, state: RecoveryState) -> tuple[bool, int]:
    if not state.has_escalated:
        state.has_escalated = True
        print(f"\033[33m[max_tokens] escalating {DEFAULT_MAX_TOKENS} -> {ESCALATED_MAX_TOKENS}\033[0m")
        return True, ESCALATED_MAX_TOKENS
    messages.append({"role": "assistant", "content": message.get("content", "")})
    if state.continuation_count < MAX_CONTINUATIONS:
        state.continuation_count += 1
        messages.append({"role": "user", "content": CONTINUATION_PROMPT})
        print(f"\033[33m[max_tokens] continuation {state.continuation_count}/{MAX_CONTINUATIONS}\033[0m")
        return True, ESCALATED_MAX_TOKENS
    print("\033[31m[max_tokens] recovery limit reached\033[0m")
    return False, ESCALATED_MAX_TOKENS


def is_length_finish(choice: dict) -> bool:
    reason = choice.get("finish_reason") or choice.get("stop_reason")
    return reason in ("length", "max_tokens")


def execute_tool_call(tool_call: dict, handlers: dict, args: dict | None = None):
    name = tool_call["function"]["name"]
    if args is None:
        args = json.loads(tool_call["function"]["arguments"])
    handler = handlers.get(name)
    return call_tool(handler, args) if handler else f"Unknown: {name}"


def inject_background_notifications(messages: list):
    notifications = collect_background_results() + collect_reminders()
    for notification in notifications:
        messages.append({"role": "user", "content": notification})
    if notifications:
        print(f"\033[32m[inject] {len(notifications)} task notification(s)\033[0m")


def inject_team_inbox(messages: list):
    inject_lead_inbox(messages)


def agent_loop(messages: list):
    state = RecoveryState()
    max_tokens = DEFAULT_MAX_TOKENS
    memory_turn = len(messages) - 1 if messages and isinstance(messages[-1].get("content"), str) else None
    memories_content = load_memories(messages)
    while True:
        inject_background_notifications(messages)
        inject_team_inbox(messages)
        messages[:] = compact_messages(messages)
        request_messages = inject_memories(messages, memories_content, memory_turn)
        tools, handlers = current_tool_pool()
        response = _call_agent(messages, request_messages, state, max_tokens, tools)
        if response is None:
            return
        choice = response["choices"][0]
        message = choice["message"]
        if is_length_finish(choice):
            should_continue, max_tokens = _handle_max_tokens(messages, message, state)
            if should_continue:
                continue
            return
        if not message.get("tool_calls"):
            force = trigger_hooks("Stop", messages)
            if force:
                messages.append({"role": "user", "content": force})
                continue
            messages.append({"role": "assistant", "content": message.get("content", "")})
            extract_memories(messages)
            consolidate_memories()
            return
        messages.append(message)
        tool_results = []
        for tool_call in message["tool_calls"]:
            name = tool_call["function"]["name"]
            args = json.loads(tool_call["function"]["arguments"])
            blocked = trigger_hooks("PreToolUse", tool_call)
            if blocked:
                tool_results.append({"role": "tool", "tool_call_id": tool_call["id"], "content": str(blocked)})
                continue
            print(f"\033[33m> 使用的工具为：{name}\033[0m")
            if name == "compact":
                messages[:] = compact_history(messages)
                tool_results.append({"role": "tool", "tool_call_id": tool_call["id"], "content": "[Compacted.]"})
                break
            if should_run_background(name, args):
                bg_id = start_background_task(tool_call, args, lambda tc, a=None: execute_tool_call(tc, handlers, a))
                output = (
                    f"[Background task {bg_id} started] "
                    f"Command: {args.get('command', name)}. "
                    "Result will be available as a task notification."
                )
                print(output)
                tool_results.append({
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "content": output,
                })
                continue
            output = execute_tool_call(tool_call, handlers, args)
            trigger_hooks("PostToolUse", tool_call, output)
            print(f"输出结果为：{str(output)[:200]}")
            tool_results.append({
                "role": "tool",
                "tool_call_id": tool_call["id"],
                "content": output,
            })
        messages.extend(tool_results)
        inject_background_notifications(messages)
        inject_team_inbox(messages)
