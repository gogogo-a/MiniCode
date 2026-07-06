"""
s20 reminders 模块：创建当前 REPL 进程内的会话提醒，并在到点后注入上下文。

函数职责：
- schedule_reminder：按秒数创建提醒。
- collect_reminders：收集已经到期的提醒通知。
- list_reminders：查看等待中的提醒。
"""

from __future__ import annotations

import threading
import time


_counter = 0
_lock = threading.Lock()
_pending: dict[str, dict] = {}
_ready: list[dict] = []


def schedule_reminder(text: str, delay_seconds: int) -> str:
    global _counter
    delay = max(1, int(delay_seconds))
    with _lock:
        _counter += 1
        reminder_id = f"rem_{_counter:04d}"
        _pending[reminder_id] = {
            "id": reminder_id,
            "text": text,
            "due_at": time.time() + delay,
        }

    def worker():
        time.sleep(delay)
        with _lock:
            item = _pending.pop(reminder_id, None)
            if item:
                _ready.append(item)

    threading.Thread(target=worker, daemon=True).start()
    return f"Reminder {reminder_id} scheduled in {delay} seconds: {text}"


def collect_reminders() -> list[str]:
    with _lock:
        ready = list(_ready)
        _ready.clear()
    return [
        "<task_notification>\n"
        f"  <task_id>{item['id']}</task_id>\n"
        "  <status>completed</status>\n"
        "  <tool>schedule_reminder</tool>\n"
        f"  <summary>{item['text']}</summary>\n"
        "</task_notification>"
        for item in ready
    ]


def list_reminders() -> str:
    with _lock:
        if not _pending:
            return "No pending reminders."
        now = time.time()
        lines = []
        for item in sorted(_pending.values(), key=lambda value: value["due_at"]):
            remaining = max(0, int(item["due_at"] - now))
            lines.append(f"{item['id']}: due in {remaining}s - {item['text']}")
        return "\n".join(lines)
