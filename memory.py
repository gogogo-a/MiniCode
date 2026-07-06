"""
记忆模块：负责持久化 memory、检索相关 memory，并在每轮结束后提取新记忆。

函数职责：
- _memory_slug：把 memory 名称转成安全文件名。
- write_memory_file：把一条记忆写成 .memory/*.md。
- rebuild_memory_index：根据 memory 文件重建 .memory/MEMORY.md 索引。
- read_memory_index：读取 system prompt 使用的 memory 索引。
- read_memory_file：读取某个 memory 文件全文。
- list_memory_files：列出全部 memory 及其元数据。
- _json_array_from_text：从模型输出中提取 JSON 数组。
- _message_text_for_memory：把 message 转成提取/检索用文本。
- select_relevant_memories：根据当前用户问题选择相关 memory 文件。
- load_memories：读取相关 memory 全文，组成 <relevant_memories> 块。
- inject_memories：把 memory 全文临时注入当前 user turn。
- extract_memories：每轮结束后提取新的偏好、反馈、项目事实或引用。
- consolidate_memories：memory 数量过多时合并去重。
"""

import json
import re
import time
from pathlib import Path

from config import CONSOLIDATE_THRESHOLD, MEMORY_DIR, MEMORY_INDEX
from llm import RecoveryState, chat_with_system
from skills import parse_frontmatter


MEMORY_TYPES = {"user", "feedback", "project", "reference"}


def _memory_slug(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", name.strip().lower()).strip("-")
    return slug or f"memory-{int(time.time())}"


def write_memory_file(name: str, memory_type: str, description: str, body: str) -> Path:
    MEMORY_DIR.mkdir(exist_ok=True)
    if memory_type not in MEMORY_TYPES:
        memory_type = "user"
    path = MEMORY_DIR / f"{_memory_slug(name)}.md"
    path.write_text(
        f"---\nname: {name}\ndescription: {description}\ntype: {memory_type}\n---\n\n{body.strip()}\n",
        encoding="utf-8",
    )
    rebuild_memory_index()
    return path


def rebuild_memory_index():
    MEMORY_DIR.mkdir(exist_ok=True)
    lines = []
    for path in sorted(MEMORY_DIR.glob("*.md")):
        if path.name == "MEMORY.md":
            continue
        raw = path.read_text(encoding="utf-8")
        meta, body = parse_frontmatter(raw)
        name = meta.get("name", path.stem)
        description = meta.get("description", body.splitlines()[0][:120] if body else "")
        lines.append(f"- [{name}]({path.name}) - {description}")
    MEMORY_INDEX.write_text(("\n".join(lines) + "\n") if lines else "", encoding="utf-8")


def read_memory_index() -> str:
    if not MEMORY_INDEX.exists():
        return ""
    return MEMORY_INDEX.read_text(encoding="utf-8").strip()


def read_memory_file(filename: str) -> str:
    path = MEMORY_DIR / filename
    if not path.exists() or not path.resolve().is_relative_to(MEMORY_DIR.resolve()):
        return ""
    return path.read_text(encoding="utf-8")


def list_memory_files() -> list[dict]:
    if not MEMORY_DIR.exists():
        return []
    memories = []
    for path in sorted(MEMORY_DIR.glob("*.md")):
        if path.name == "MEMORY.md":
            continue
        raw = path.read_text(encoding="utf-8")
        meta, body = parse_frontmatter(raw)
        memories.append({
            "filename": path.name,
            "name": meta.get("name", path.stem),
            "description": meta.get("description", ""),
            "type": meta.get("type", "user"),
            "body": body,
        })
    return memories


def _json_array_from_text(text: str) -> list:
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        return []
    try:
        parsed = json.loads(match.group())
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _message_text_for_memory(message: dict) -> str:
    content = message.get("content", "")
    if isinstance(content, str):
        text = content
    else:
        text = json.dumps(content, ensure_ascii=False, default=str)
    tool_calls = message.get("tool_calls")
    if tool_calls:
        text = f"{text}\ntool_calls: {json.dumps(tool_calls, ensure_ascii=False)}"
    return text.strip()


def select_relevant_memories(messages: list, max_items: int = 5) -> list[str]:
    files = list_memory_files()
    if not files:
        return []
    recent = "\n".join(
        _message_text_for_memory(message)
        for message in messages[-8:]
        if message.get("role") == "user"
    )[:3000]
    if not recent.strip():
        return []
    catalog = "\n".join(
        f"{index}: {memory['name']} - {memory['description']}"
        for index, memory in enumerate(files)
    )
    prompt = (
        "Select memories that are relevant to the recent user request.\n"
        "For file creation or code editing requests, include coding style preferences.\n"
        "Return only a JSON array of integer indexes. Return [] if none apply.\n\n"
        f"Recent user request:\n{recent}\n\n"
        f"Memory catalog:\n{catalog}"
    )
    response = chat_with_system([{"role": "user", "content": prompt}], [], "Select relevant memories.", RecoveryState())
    text = response["choices"][0]["message"].get("content", "")
    selected = []
    for index in _json_array_from_text(text):
        if isinstance(index, int) and 0 <= index < len(files):
            selected.append(files[index]["filename"])
            if len(selected) >= max_items:
                break
    if selected:
        return selected
    lower_recent = recent.lower()
    wants_preferences = any(word in lower_recent for word in ("prefer", "preference", "remember", "told you"))
    edits_code = any(word in lower_recent for word in ("create", "write", "edit", "python", "code", ".py"))
    if wants_preferences:
        for memory in files:
            if memory["type"] in ("user", "feedback") and memory["filename"] not in selected:
                selected.append(memory["filename"])
                if len(selected) >= max_items:
                    return selected
    if edits_code:
        for memory in files:
            searchable = f"{memory['name']} {memory['description']} {memory['body']}".lower()
            if memory["filename"] not in selected and any(word in searchable for word in ("indent", "quote", "style", "format", "code")):
                selected.append(memory["filename"])
                if len(selected) >= max_items:
                    return selected
    return selected


def load_memories(messages: list) -> str:
    selected = select_relevant_memories(messages)
    if not selected:
        return ""
    parts = ["<relevant_memories>"]
    for filename in selected:
        content = read_memory_file(filename)
        if content:
            parts.append(content)
    parts.append("</relevant_memories>")
    print(f"\033[33m[Memory: loaded {len(selected)} memories]\033[0m")
    return "\n\n".join(parts)


def inject_memories(messages: list, memories_content: str, memory_turn: int | None) -> list:
    if not memories_content or memory_turn is None or memory_turn >= len(messages):
        return messages
    if not isinstance(messages[memory_turn].get("content"), str):
        return messages
    request_messages = messages.copy()
    request_messages[memory_turn] = {
        **messages[memory_turn],
        "content": memories_content + "\n\n" + messages[memory_turn]["content"],
    }
    return request_messages


def extract_memories(messages: list):
    recent = []
    for message in messages[-10:]:
        text = _message_text_for_memory(message)
        if text:
            recent.append(f"{message.get('role', 'unknown')}: {text}")
    dialogue = "\n".join(recent)[:5000]
    if not dialogue.strip():
        return
    existing = "\n".join(
        f"- {memory['name']}: {memory['description']}"
        for memory in list_memory_files()
    ) or "(none)"
    prompt = (
        "Extract durable memories from the dialogue.\n"
        "Only save user preferences, user feedback, stable project facts, or useful references.\n"
        "Do not save one-off completed task results, temporary files, or ordinary tool outputs as memory.\n"
        "Return only a JSON array. Each item must have: name, type, description, body.\n"
        "type must be one of: user, feedback, project, reference.\n"
        "If nothing new should be saved, return [].\n\n"
        f"Existing memories:\n{existing}\n\n"
        f"Dialogue:\n{dialogue}"
    )
    response = chat_with_system([{"role": "user", "content": prompt}], [], "Extract agent memories.", RecoveryState())
    items = _json_array_from_text(response["choices"][0]["message"].get("content", ""))
    count = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        description = str(item.get("description", "")).strip()
        body = str(item.get("body", "")).strip()
        memory_type = str(item.get("type", "user")).strip()
        if name and description and body:
            write_memory_file(name, memory_type, description, body)
            count += 1
    if count:
        print(f"\n\033[33m[Memory: extracted {count} new memories]\033[0m")


def consolidate_memories():
    files = list_memory_files()
    if len(files) < CONSOLIDATE_THRESHOLD:
        return
    catalog = "\n\n".join(
        f"## {memory['filename']}\nname: {memory['name']}\ntype: {memory['type']}\n"
        f"description: {memory['description']}\n{memory['body']}"
        for memory in files
    )[:16000]
    prompt = (
        "Consolidate these memory files.\n"
        "Merge duplicates, remove outdated items, and preserve important user preferences.\n"
        "Return only a JSON array. Each item must have: name, type, description, body.\n\n"
        f"{catalog}"
    )
    response = chat_with_system([{"role": "user", "content": prompt}], [], "Consolidate memories.", RecoveryState())
    items = _json_array_from_text(response["choices"][0]["message"].get("content", ""))
    if not items:
        return
    for path in MEMORY_DIR.glob("*.md"):
        if path.name != "MEMORY.md":
            path.unlink()
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        description = str(item.get("description", "")).strip()
        body = str(item.get("body", "")).strip()
        memory_type = str(item.get("type", "user")).strip()
        if name and description and body:
            write_memory_file(name, memory_type, description, body)
    print(f"\n\033[33m[Memory: consolidated {len(files)} memories]\033[0m")
