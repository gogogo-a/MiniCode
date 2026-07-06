from __future__ import annotations

import contextlib
import json
from typing import Iterator


def _tool_call(index: int, item: dict) -> dict:
    return {
        "id": f"fake_call_{index}",
        "type": "function",
        "function": {
            "name": item["name"],
            "arguments": json.dumps(item.get("args", {}), ensure_ascii=False),
        },
    }


def _tool_response(tool_calls: list[dict]) -> dict:
    return {
        "choices": [{
            "finish_reason": "tool_calls",
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [_tool_call(index, item) for index, item in enumerate(tool_calls, 1)],
            },
        }],
    }


def _final_response(text: str) -> dict:
    return {
        "choices": [{
            "finish_reason": "stop",
            "message": {
                "role": "assistant",
                "content": text,
            },
        }],
    }


@contextlib.contextmanager
def fake_chat(model: dict) -> Iterator[None]:
    import loop
    import memory

    responses = []
    if model.get("tool_calls"):
        responses.append(_tool_response(list(model["tool_calls"])))
    responses.append(_final_response(str(model.get("final", "完成。"))))
    original = loop.chat_with_system
    original_memory_chat = memory.chat_with_system
    original_loop_load = loop.load_memories
    original_loop_extract = loop.extract_memories
    original_loop_consolidate = loop.consolidate_memories
    original_extract = memory.extract_memories
    original_consolidate = memory.consolidate_memories

    def fake_chat_with_system(*_args, **_kwargs):
        if len(responses) > 1:
            return responses.pop(0)
        return responses[0]

    loop.chat_with_system = fake_chat_with_system
    memory.chat_with_system = fake_chat_with_system
    loop.load_memories = lambda *_args, **_kwargs: ""
    loop.extract_memories = lambda *_args, **_kwargs: None
    loop.consolidate_memories = lambda *_args, **_kwargs: None
    memory.extract_memories = lambda *_args, **_kwargs: None
    memory.consolidate_memories = lambda *_args, **_kwargs: None
    try:
        yield
    finally:
        loop.chat_with_system = original
        memory.chat_with_system = original_memory_chat
        loop.load_memories = original_loop_load
        loop.extract_memories = original_loop_extract
        loop.consolidate_memories = original_loop_consolidate
        memory.extract_memories = original_extract
        memory.consolidate_memories = original_consolidate
