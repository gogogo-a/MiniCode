"""
s20 todo 模块：保存当前会话内的轻量计划。

函数职责：
- todo_write：写入或替换当前会话 TODO 列表。
- todo_snapshot：返回当前 TODO 状态，供 system prompt 展示。
"""

from __future__ import annotations

import json


TODOS: list[dict] = []


def _normalize_item(item) -> dict:
    if isinstance(item, str):
        return {"content": item, "status": "pending"}
    if isinstance(item, dict):
        content = str(item.get("content") or item.get("task") or item.get("text") or "").strip()
        status = str(item.get("status") or "pending").strip()
        if status not in {"pending", "in_progress", "completed"}:
            status = "pending"
        return {"content": content, "status": status}
    return {"content": str(item), "status": "pending"}


def todo_write(items: list) -> str:
    global TODOS
    normalized = [_normalize_item(item) for item in items]
    TODOS = [item for item in normalized if item["content"]]
    if not TODOS:
        return "Todo list is empty."
    return "Todo list updated:\n" + "\n".join(
        f"- [{item['status']}] {item['content']}" for item in TODOS
    )


def todo_snapshot() -> str:
    if not TODOS:
        return "No active todo list."
    return json.dumps(TODOS, ensure_ascii=False, indent=2)
