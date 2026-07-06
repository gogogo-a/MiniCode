"""
工具模块：定义 OpenAI tool schema，并实现每个工具真正执行的函数。

函数职责：
- safe_path：把用户传入路径限制在当前工作区内。
- run_bash：执行 shell 命令，并返回 stdout/stderr。
- run_read：读取文件内容，支持 limit 限制行数。
- run_write：写文件，必要时创建父目录。
- run_edit：把文件中的一段精确文本替换成新文本。
- run_glob：按 glob pattern 查找工作区内文件。
- run_list_background_tasks：查看仍在运行的后台任务。
- run_create_task：创建持久化任务，可指定依赖任务。
- run_list_tasks：列出所有任务状态。
- run_list_ready_tasks：列出当前依赖已满足、可以领取的任务。
- run_get_task：查看单个任务完整 JSON。
- run_claim_task：领取任务，把 pending 改成 in_progress。
- run_complete_task：完成任务，把 in_progress 改成 completed 并返回解锁任务。
- run_schedule_cron：创建一个系统级定时任务，由外部 `code.py --tick` 触发。
- run_list_crons：查看所有持久化定时任务。
- run_cancel_cron：取消一个持久化定时任务。
- run_create_worktree：创建 git worktree，并可绑定任务。
- run_remove_worktree：删除 worktree，有改动时默认拒绝。
- run_keep_worktree：保留 worktree 供人工 review。
- run_connect_mcp：连接真实 stdio MCP server 并发现工具。
- call_tool：按 handler 函数签名过滤模型传来的参数并执行工具。
- register_tool：运行时注册额外工具，比如 task 子 agent 工具。
"""

import inspect
import subprocess
from pathlib import Path

from background_tasks import list_background_tasks
from config import WORKDIR
from skills import load_skill
from system_scheduler import cancel_job, list_jobs, schedule_job
from task_system import (
    claim_task,
    complete_task,
    create_task,
    get_task,
    list_ready_tasks,
    list_tasks,
)
from worktree_system import create_worktree, keep_worktree, remove_worktree
from mcp_plugin import connect_mcp
from reminders import list_reminders, schedule_reminder
from todo_system import todo_write


def safe_path(path_text: str, cwd: str | Path | None = None) -> Path:
    base = Path(cwd).resolve() if cwd else WORKDIR
    path = (base / path_text).resolve()
    if not path.is_relative_to(base):
        raise ValueError(f"Path escapes workspace: {path_text}")
    return path


def run_bash(command: str, run_in_background: bool = False, cwd: str | None = None) -> str:
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    if any(item in command for item in dangerous):
        return "Error: Dangerous command blocked"
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=cwd or WORKDIR,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        output = (result.stdout + result.stderr).strip()
        return output[:50000] if output else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"
    except (FileNotFoundError, OSError) as error:
        return f"Error: {error}"


def run_read(path: str, limit: int | None = None, cwd: str | None = None) -> str:
    try:
        lines = safe_path(path, cwd).read_text(encoding="utf-8", errors="replace").splitlines()
        if limit and limit < len(lines):
            lines = lines[:limit] + [f"... ({len(lines) - limit} more lines)"]
        return "\n".join(lines)
    except Exception as error:
        return f"Error: {error}"


def run_write(path: str, content: str, cwd: str | None = None) -> str:
    try:
        file_path = safe_path(path, cwd)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as error:
        return f"Error: {error}"


def run_edit(path: str, old_text: str, new_text: str, cwd: str | None = None) -> str:
    try:
        file_path = safe_path(path, cwd)
        text = file_path.read_text(encoding="utf-8")
        if old_text not in text:
            return f"Error: text not found in {path}"
        file_path.write_text(text.replace(old_text, new_text, 1), encoding="utf-8")
        return f"Edited {path}"
    except Exception as error:
        return f"Error: {error}"


def run_glob(pattern: str, cwd: str | None = None) -> str:
    import glob

    try:
        results = []
        base = Path(cwd).resolve() if cwd else WORKDIR
        for match in glob.glob(pattern, root_dir=base):
            if (base / match).resolve().is_relative_to(base):
                results.append(match)
        return "\n".join(results) if results else "(no matches)"
    except Exception as error:
        return f"Error: {error}"


def run_list_background_tasks() -> str:
    return list_background_tasks()


def run_create_task(subject: str, description: str = "", blocked_by=None, blockedBy=None) -> str:
    try:
        task = create_task(subject, description, blocked_by if blocked_by is not None else blockedBy)
        deps = f" (blocked_by: {', '.join(task.blocked_by)})" if task.blocked_by else ""
        print(f"\033[34m[create] {task.subject}{deps}\033[0m")
        return f"Created {task.id}: {task.subject}{deps}"
    except Exception as error:
        return f"Error: {error}"


def run_list_tasks() -> str:
    tasks = list_tasks()
    if not tasks:
        return "No tasks. Use create_task to add some."
    lines = []
    icons = {"pending": "○", "in_progress": "●", "completed": "✓", "failed": "!", "cancelled": "-"}
    for task in tasks:
        deps = f" (blocked_by: {', '.join(task.blocked_by)})" if task.blocked_by else ""
        owner = f" [{task.owner}]" if task.owner else ""
        lines.append(f"{icons.get(task.status, '?')} {task.id}: {task.subject} [{task.status}]{owner}{deps}")
    return "\n".join(lines)


def run_list_ready_tasks() -> str:
    tasks = list_ready_tasks()
    if not tasks:
        return "No ready tasks."
    return "\n".join(f"{task.id}: {task.subject}" for task in tasks)


def run_get_task(task_id: str) -> str:
    try:
        return get_task(task_id)
    except FileNotFoundError:
        return f"Error: Task {task_id} not found"
    except Exception as error:
        return f"Error: {error}"


def run_claim_task(task_id: str, owner: str = "agent") -> str:
    try:
        return claim_task(task_id, owner)
    except FileNotFoundError:
        return f"Error: Task {task_id} not found"
    except Exception as error:
        return f"Error: {error}"


def run_complete_task(task_id: str, result: str = "") -> str:
    try:
        return complete_task(task_id, result)
    except FileNotFoundError:
        return f"Error: Task {task_id} not found"
    except Exception as error:
        return f"Error: {error}"


def run_schedule_cron(cron: str, prompt: str, recurring: bool = True, durable: bool = True) -> str:
    try:
        job = schedule_job(cron=cron, prompt=prompt, recurring=recurring, durable=durable)
        return (
            f"Scheduled {job.id}: cron={job.cron}, recurring={job.recurring}, "
            f"next_run_after={job.next_run_after}"
        )
    except Exception as error:
        return f"Error: {error}"


def run_list_crons() -> str:
    jobs = list_jobs()
    if not jobs:
        return "No scheduled jobs."
    lines = []
    for job in jobs:
        lines.append(
            f"{job.id}: cron={job.cron}, status={job.last_status}, "
            f"running={job.running}, next_run_after={job.next_run_after}, prompt={job.prompt}"
        )
    return "\n".join(lines)


def run_cancel_cron(job_id: str) -> str:
    return cancel_job(job_id)


def run_create_worktree(name: str, task_id: str = "") -> str:
    return create_worktree(name, task_id)


def run_remove_worktree(name: str, discard_changes: bool = False) -> str:
    return remove_worktree(name, discard_changes)


def run_keep_worktree(name: str) -> str:
    return keep_worktree(name)


def run_connect_mcp(name: str) -> str:
    return connect_mcp(name)


def run_todo_write(items: list) -> str:
    return todo_write(items)


def run_schedule_reminder(text: str, delay_seconds: int) -> str:
    return schedule_reminder(text, delay_seconds)


def run_list_reminders() -> str:
    return list_reminders()


def call_tool(handler, args: dict):
    params = inspect.signature(handler).parameters
    if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in params.values()):
        return handler(**args)
    filtered_args = {key: value for key, value in args.items() if key in params}
    return handler(**filtered_args)


TOOLS = [
    {"type": "function", "function": {"name": "todo_write", "description": "Create or update the current session todo list. Use this first when the user asks to make a todo list or plan steps.",
                                      "parameters": {"type": "object", "properties": {"items": {"type": "array", "items": {"type": "object", "properties": {"content": {"type": "string"}, "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]}}, "required": ["content"], "additionalProperties": False}}},
                                                     "required": ["items"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "load_skill", "description": "Load the full content of a skill by name.",
                                      "parameters": {"type": "object", "properties": {"name": {"type": "string"}},
                                                     "required": ["name"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "compact", "description": "Summarize earlier conversation to free context space.",
                                      "parameters": {"type": "object", "properties": {"focus": {"type": "string"}},
                                                     "additionalProperties": False}}},
    {"type": "function", "function": {"name": "bash", "description": "Run a shell command.",
                                      "parameters": {"type": "object", "properties": {"command": {"type": "string"},
                                                                                      "run_in_background": {"type": "boolean"}},
                                                     "required": ["command"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "read_file", "description": "Read file contents.",
                                      "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}},
                                                     "required": ["path"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "write_file", "description": "Write content to a file.",
                                      "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                                                     "required": ["path", "content"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "edit_file", "description": "Replace exact text in a file once.",
                                      "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}},
                                                     "required": ["path", "old_text", "new_text"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "glob", "description": "Find files matching a glob pattern.",
                                      "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}},
                                                     "required": ["pattern"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "list_background_tasks", "description": "List currently running background tasks.",
                                      "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}},
    {"type": "function", "function": {"name": "schedule_reminder", "description": "Schedule an in-session reminder after delay_seconds. Use delay_seconds=180 for 3 minutes.",
                                      "parameters": {"type": "object", "properties": {"text": {"type": "string"}, "delay_seconds": {"type": "integer"}},
                                                     "required": ["text", "delay_seconds"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "list_reminders", "description": "List pending in-session reminders.",
                                      "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}},
    {"type": "function", "function": {"name": "create_task", "description": "Create a persistent task with optional blocked_by dependency task IDs.",
                                      "parameters": {"type": "object", "properties": {"subject": {"type": "string"},
                                                                                      "description": {"type": "string"},
                                                                                      "blocked_by": {"type": "array", "items": {"type": "string"}}},
                                                     "required": ["subject"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "list_tasks", "description": "List all persistent tasks with status, owner, and dependencies.",
                                      "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}},
    {"type": "function", "function": {"name": "list_ready_tasks", "description": "List pending tasks whose dependencies are completed.",
                                      "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}},
    {"type": "function", "function": {"name": "get_task", "description": "Get full JSON details for a task by ID.",
                                      "parameters": {"type": "object", "properties": {"task_id": {"type": "string"}},
                                                     "required": ["task_id"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "claim_task", "description": "Claim a ready pending task and mark it in_progress.",
                                      "parameters": {"type": "object", "properties": {"task_id": {"type": "string"},
                                                                                      "owner": {"type": "string"}},
                                                     "required": ["task_id"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "complete_task", "description": "Complete an in_progress task and report newly unblocked tasks.",
                                      "parameters": {"type": "object", "properties": {"task_id": {"type": "string"},
                                                                                      "result": {"type": "string"}},
                                                     "required": ["task_id"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "schedule_cron", "description": "Create a persisted system-level scheduled job. External runner calls code.py --tick; this does not install crontab.",
                                      "parameters": {"type": "object", "properties": {"cron": {"type": "string"},
                                                                                      "prompt": {"type": "string"},
                                                                                      "recurring": {"type": "boolean"},
                                                                                      "durable": {"type": "boolean"}},
                                                     "required": ["cron", "prompt"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "list_crons", "description": "List persisted scheduled jobs and their status fields.",
                                      "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}},
    {"type": "function", "function": {"name": "cancel_cron", "description": "Cancel a persisted scheduled job by ID.",
                                      "parameters": {"type": "object", "properties": {"job_id": {"type": "string"}},
                                                     "required": ["job_id"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "create_worktree", "description": "Create a git worktree under .worktrees and optionally bind it to a task_id without claiming the task.",
                                      "parameters": {"type": "object", "properties": {"name": {"type": "string"},
                                                                                      "task_id": {"type": "string"}},
                                                     "required": ["name"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "remove_worktree", "description": "Remove a git worktree. Refuses dirty worktrees unless discard_changes is true.",
                                      "parameters": {"type": "object", "properties": {"name": {"type": "string"},
                                                                                      "discard_changes": {"type": "boolean"}},
                                                     "required": ["name"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "keep_worktree", "description": "Keep a git worktree for review and record the decision.",
                                      "parameters": {"type": "object", "properties": {"name": {"type": "string"}},
                                                     "required": ["name"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "connect_mcp", "description": "Connect a real stdio MCP server and add its discovered tools to the Lead tool pool.",
                                      "parameters": {"type": "object", "properties": {"name": {"type": "string"}},
                                                     "required": ["name"], "additionalProperties": False}}},
]

TOOL_HANDLERS = {
    "todo_write": run_todo_write,
    "bash": run_bash,
    "read_file": run_read,
    "write_file": run_write,
    "edit_file": run_edit,
    "glob": run_glob,
    "list_background_tasks": run_list_background_tasks,
    "schedule_reminder": run_schedule_reminder,
    "list_reminders": run_list_reminders,
    "load_skill": load_skill,
    "create_task": run_create_task,
    "list_tasks": run_list_tasks,
    "list_ready_tasks": run_list_ready_tasks,
    "get_task": run_get_task,
    "claim_task": run_claim_task,
    "complete_task": run_complete_task,
    "schedule_cron": run_schedule_cron,
    "list_crons": run_list_crons,
    "cancel_cron": run_cancel_cron,
    "create_worktree": run_create_worktree,
    "remove_worktree": run_remove_worktree,
    "keep_worktree": run_keep_worktree,
    "connect_mcp": run_connect_mcp,
}


def register_tool(tool: dict, handler):
    TOOLS.append(tool)
    TOOL_HANDLERS[tool["function"]["name"]] = handler
