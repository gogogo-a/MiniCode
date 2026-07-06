"""
上下文压缩模块：控制 tool 输出、消息数量和整体上下文大小。

函数职责：
- estimate_size：粗略估算 messages 的字符大小。
- _is_tool_message / _has_tool_calls / _tool_messages：识别工具相关消息。
- persist_large_output：把超大的工具输出落盘，只把预览放回上下文。
- tool_result_budget：当工具结果总量过大时，优先落盘大结果。
- snip_compact：消息条数过多时裁掉中间历史。
- micro_compact：只保留最近几个完整 tool result，旧结果替换成占位符。
- write_transcript：把压缩前完整对话保存成 jsonl。
- summarize_history：让模型总结历史，供压缩后继续工作。
- fallback_summary：总结失败时生成本地摘要。
- compact_history：主动压缩历史并保留 transcript 路径。
- reactive_compact：遇到上下文过长错误后的紧急压缩。
- compact_messages：每次 LLM 调用前执行的完整压缩流水线。
"""

import json
import time
from pathlib import Path

from config import (
    CONTEXT_LIMIT,
    KEEP_RECENT_TOOL_RESULTS,
    MAX_MESSAGES,
    PERSIST_THRESHOLD,
    TOOL_RESULT_BUDGET,
    TOOL_RESULTS_DIR,
    TRANSCRIPT_DIR,
)
from llm import RecoveryState, chat_with_system


def estimate_size(messages: list) -> int:
    return len(json.dumps(messages, ensure_ascii=False, default=str))


def _is_tool_message(message: dict) -> bool:
    return message.get("role") == "tool"


def _has_tool_calls(message: dict) -> bool:
    return message.get("role") == "assistant" and bool(message.get("tool_calls"))


def _tool_messages(messages: list) -> list[tuple[int, dict]]:
    return [(index, message) for index, message in enumerate(messages) if _is_tool_message(message)]


def persist_large_output(tool_call_id: str, output: str) -> str:
    if len(output) <= PERSIST_THRESHOLD:
        return output
    TOOL_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = TOOL_RESULTS_DIR / f"{tool_call_id}.txt"
    if not path.exists():
        path.write_text(output, encoding="utf-8")
    return f"<persisted-output>\nFull output: {path}\nPreview:\n{output[:2000]}\n</persisted-output>"


def tool_result_budget(messages: list) -> list:
    total = sum(len(str(message.get("content", ""))) for message in messages if _is_tool_message(message))
    if total <= TOOL_RESULT_BUDGET:
        return messages
    for _, message in sorted(_tool_messages(messages), key=lambda item: len(str(item[1].get("content", ""))), reverse=True):
        if total <= TOOL_RESULT_BUDGET:
            break
        content = str(message.get("content", ""))
        compacted = persist_large_output(message.get("tool_call_id", "unknown"), content)
        if compacted != content:
            print(f"[tool result persisted: {message.get('tool_call_id', 'unknown')}]")
            message["content"] = compacted
            total = sum(len(str(item.get("content", ""))) for item in messages if _is_tool_message(item))
    return messages


def snip_compact(messages: list, max_messages: int = MAX_MESSAGES) -> list:
    if len(messages) <= max_messages:
        return messages
    keep_head = 3
    keep_tail = max_messages - keep_head
    head_end = keep_head
    tail_start = len(messages) - keep_tail
    if tail_start > 0 and tail_start < len(messages) and _is_tool_message(messages[tail_start]) and _has_tool_calls(messages[tail_start - 1]):
        tail_start -= 1
    if head_end >= tail_start:
        return messages
    snipped = tail_start - head_end
    print(f"[snip compact: {snipped} messages]")
    return messages[:head_end] + [{"role": "user", "content": f"[snipped {snipped} messages]"}] + messages[tail_start:]


def micro_compact(messages: list) -> list:
    tools = _tool_messages(messages)
    if len(tools) <= KEEP_RECENT_TOOL_RESULTS:
        return messages
    for _, message in tools[:-KEEP_RECENT_TOOL_RESULTS]:
        content = str(message.get("content", ""))
        if len(content) > 120:
            message["content"] = "[Earlier tool result compacted. Re-run if needed.]"
    print(f"[micro compact: kept last {KEEP_RECENT_TOOL_RESULTS} tool results]")
    return messages


def write_transcript(messages: list) -> Path:
    TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    path = TRANSCRIPT_DIR / f"transcript_{int(time.time())}.jsonl"
    with path.open("w", encoding="utf-8") as file:
        for message in messages:
            file.write(json.dumps(message, ensure_ascii=False, default=str) + "\n")
    return path


def summarize_history(messages: list, limit: int = 20000) -> str:
    conversation = json.dumps(messages, ensure_ascii=False, default=str)[:limit]
    prompt = (
        "Summarize this coding-agent conversation so work can continue.\n"
        "Preserve: current goal, user constraints, files read/changed, tool outputs that matter, remaining work.\n"
        "Be compact but concrete.\n\n"
        + conversation
    )
    response = chat_with_system([{"role": "user", "content": prompt}], [], "You summarize agent transcripts.", RecoveryState())
    return response["choices"][0]["message"].get("content", "").strip() or "(empty summary)"


def fallback_summary(messages: list, transcript_path: Path) -> str:
    recent_user = [
        message.get("content", "")
        for message in messages
        if message.get("role") == "user" and isinstance(message.get("content"), str)
    ][-3:]
    recent_tools = [
        message.get("tool_call_id", "unknown")
        for message in messages
        if message.get("role") == "tool"
    ][-5:]
    return (
        "LLM summary unavailable. Continue from the saved transcript if details are needed.\n"
        f"Transcript: {transcript_path}\n"
        f"Messages before compact: {len(messages)}\n"
        f"Recent user turns: {recent_user}\n"
        f"Recent tool call ids: {recent_tools}"
    )


def compact_history(messages: list) -> list:
    transcript_path = write_transcript(messages)
    print(f"[transcript saved: {transcript_path}]")
    try:
        summary = summarize_history(messages)
    except Exception:
        summary = fallback_summary(messages, transcript_path)
    return [{"role": "user", "content": f"[Compacted]\nTranscript: {transcript_path}\n\n{summary}"}]


def reactive_compact(messages: list) -> list:
    print("[reactive compact]")
    transcript_path = write_transcript(messages)
    print(f"[reactive transcript saved: {transcript_path}]")
    try:
        summary = summarize_history(messages, limit=10000)
    except Exception:
        summary = fallback_summary(messages, transcript_path)
    tail = messages[-5:] if len(messages) > 5 else messages
    return [{"role": "user", "content": f"[Reactive compact]\nTranscript: {transcript_path}\n\n{summary}"}, *tail]


def compact_messages(messages: list) -> list:
    messages = tool_result_budget(messages)
    messages = snip_compact(messages)
    messages = micro_compact(messages)
    if estimate_size(messages) > CONTEXT_LIMIT:
        print("[auto compact]")
        messages = compact_history(messages)
    return messages
