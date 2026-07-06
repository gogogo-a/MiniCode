"""
后台任务模块：把适合异步执行的工具调用放到线程里跑，并在完成后生成通知。

函数职责：
- is_slow_operation：根据工具名和参数判断是否像慢操作。
- should_run_background：优先看 run_in_background，其次使用慢操作启发式。
- start_background_task：创建 daemon thread 执行工具，立即返回 bg_id。
- collect_background_results：收集已完成后台任务，生成 <task_notification> 文本。
- list_background_tasks：返回当前仍在运行的后台任务摘要。
"""

import threading


_bg_counter = 0
background_tasks: dict[str, dict] = {}
background_results: dict[str, str] = {}
background_lock = threading.Lock()


def is_slow_operation(tool_name: str, tool_input: dict) -> bool:
    if tool_name != "bash":
        return False
    command = tool_input.get("command", "").lower()
    slow_keywords = [
        "install",
        "build",
        "test",
        "deploy",
        "compile",
        "docker build",
        "pip install",
        "npm install",
        "cargo build",
        "pytest",
        "make",
    ]
    return any(keyword in command for keyword in slow_keywords)


def should_run_background(tool_name: str, tool_input: dict) -> bool:
    if tool_input.get("run_in_background"):
        return True
    return is_slow_operation(tool_name, tool_input)


def start_background_task(tool_call: dict, args: dict, execute_tool) -> str:
    global _bg_counter
    with background_lock:
        _bg_counter += 1
        bg_id = f"bg_{_bg_counter:04d}"
        name = tool_call["function"]["name"]
        command = args.get("command", name)
        background_tasks[bg_id] = {
            "tool_call_id": tool_call["id"],
            "tool_name": name,
            "command": command,
            "status": "running",
        }

    def worker():
        output = execute_tool(tool_call, args)
        with background_lock:
            if bg_id in background_tasks:
                background_tasks[bg_id]["status"] = "completed"
                background_results[bg_id] = str(output)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    print(f"\033[33m[background] dispatched {bg_id}: {str(command)[:80]}\033[0m")
    return bg_id


def collect_background_results() -> list[str]:
    with background_lock:
        ready_ids = [
            bg_id for bg_id, task in background_tasks.items()
            if task["status"] == "completed"
        ]
    notifications = []
    for bg_id in ready_ids:
        with background_lock:
            task = background_tasks.pop(bg_id)
            output = background_results.pop(bg_id, "")
        summary = output[:500] if len(output) > 500 else output
        notification = (
            "<task_notification>\n"
            f"  <task_id>{bg_id}</task_id>\n"
            "  <status>completed</status>\n"
            f"  <tool>{task['tool_name']}</tool>\n"
            f"  <command>{task['command']}</command>\n"
            f"  <summary>{summary}</summary>\n"
            "</task_notification>"
        )
        notifications.append(notification)
        print(f"\033[32m[background done] {bg_id}: {str(task['command'])[:80]} ({len(output)} chars)\033[0m")
    return notifications


def list_background_tasks() -> str:
    with background_lock:
        if not background_tasks:
            return "No background tasks."
        lines = []
        for bg_id, task in sorted(background_tasks.items()):
            lines.append(f"{bg_id}: {task['status']} - {task['command']}")
        return "\n".join(lines)
