"""
任务系统模块：把任务保存成 .tasks/*.json，并用 blocked_by 和 worktree 表达依赖与隔离工作目录。

函数职责：
- Task：任务数据结构，包含状态、负责人、依赖、结果和时间戳。
- task_path：根据 task_id 得到持久化 JSON 文件路径。
- normalize_blocked_by：把 blocked_by 输入统一成 list[str]。
- save_task / load_task / list_tasks：任务 JSON 的保存、读取和列表。
- get_task：返回单个任务的 JSON 详情。
- dependency_status：检查一个任务的依赖是否都完成。
- can_start：判断任务是否可以领取。
- would_create_cycle：创建任务前检查依赖图是否会形成环。
- create_task：创建 pending 任务并持久化。
- list_ready_tasks：列出 pending 且依赖已完成的任务。
- claim_task：把可开始、无 owner 的 pending 任务改成 in_progress。
- complete_task：把 in_progress 任务改成 completed，并报告新解锁任务。
- bind_task_to_worktree：把任务绑定到一个 worktree，不改变任务状态。
"""

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from config import WORKDIR


TASKS_DIR = WORKDIR / ".tasks"
VALID_STATUSES = {"pending", "in_progress", "completed", "failed", "cancelled"}


@dataclass
class Task:
    id: str
    subject: str
    description: str
    status: str
    owner: str | None
    blocked_by: list[str]
    worktree: str | None
    result: str | None
    created_at: int
    updated_at: int
    completed_at: int | None


def task_path(task_id: str) -> Path:
    return TASKS_DIR / f"{task_id}.json"


def normalize_blocked_by(blocked_by) -> list[str]:
    if blocked_by is None:
        return []
    if isinstance(blocked_by, str):
        return [blocked_by]
    if isinstance(blocked_by, list) and all(isinstance(item, str) for item in blocked_by):
        return blocked_by
    raise ValueError("blocked_by must be a string, list of strings, or null")


def save_task(task: Task):
    if task.status not in VALID_STATUSES:
        raise ValueError(f"Invalid task status: {task.status}")
    TASKS_DIR.mkdir(exist_ok=True)
    task_path(task.id).write_text(json.dumps(asdict(task), indent=2, ensure_ascii=False), encoding="utf-8")


def load_task(task_id: str) -> Task:
    raw = json.loads(task_path(task_id).read_text(encoding="utf-8"))
    if "blockedBy" in raw and "blocked_by" not in raw:
        raw["blocked_by"] = raw.pop("blockedBy")
    raw.setdefault("result", None)
    raw.setdefault("worktree", None)
    raw.setdefault("created_at", int(time.time()))
    raw.setdefault("updated_at", raw["created_at"])
    raw.setdefault("completed_at", None)
    task = Task(**raw)
    if task.status not in VALID_STATUSES:
        raise ValueError(f"Invalid task status: {task.status}")
    task.blocked_by = normalize_blocked_by(task.blocked_by)
    return task


def list_tasks() -> list[Task]:
    if not TASKS_DIR.exists():
        return []
    return [load_task(path.stem) for path in sorted(TASKS_DIR.glob("task_*.json"))]


def get_task(task_id: str) -> str:
    return json.dumps(asdict(load_task(task_id)), indent=2, ensure_ascii=False)


def dependency_status(task: Task) -> tuple[bool, list[str]]:
    blocked = []
    for dependency_id in task.blocked_by:
        if not task_path(dependency_id).exists():
            blocked.append(dependency_id)
            continue
        if load_task(dependency_id).status != "completed":
            blocked.append(dependency_id)
    return not blocked, blocked


def can_start(task_id: str) -> bool:
    ready, _ = dependency_status(load_task(task_id))
    return ready


def would_create_cycle(new_task_id: str, blocked_by: list[str]) -> bool:
    graph = {task.id: list(task.blocked_by) for task in list_tasks()}
    graph[new_task_id] = list(blocked_by)
    visiting = set()
    visited = set()

    def visit(task_id: str) -> bool:
        if task_id in visiting:
            return True
        if task_id in visited:
            return False
        visiting.add(task_id)
        for dependency_id in graph.get(task_id, []):
            if visit(dependency_id):
                return True
        visiting.remove(task_id)
        visited.add(task_id)
        return False

    return visit(new_task_id)


def create_task(subject: str, description: str = "", blocked_by=None) -> Task:
    dependencies = normalize_blocked_by(blocked_by)
    for dependency_id in dependencies:
        if not task_path(dependency_id).exists():
            raise ValueError(f"Dependency task not found: {dependency_id}")
    now = int(time.time())
    task = Task(
        id=f"task_{now}_{len(list_tasks()) + 1:04d}",
        subject=subject,
        description=description,
        status="pending",
        owner=None,
        blocked_by=dependencies,
        worktree=None,
        result=None,
        created_at=now,
        updated_at=now,
        completed_at=None,
    )
    if task.id in dependencies:
        raise ValueError("Task cannot depend on itself")
    if would_create_cycle(task.id, dependencies):
        raise ValueError("Task dependency graph would contain a cycle")
    save_task(task)
    return task


def list_ready_tasks() -> list[Task]:
    return [
        task for task in list_tasks()
        if task.status == "pending" and not task.owner and can_start(task.id)
    ]


def claim_task(task_id: str, owner: str = "agent") -> str:
    task = load_task(task_id)
    if task.status != "pending":
        return f"Task {task_id} is {task.status}, cannot claim"
    if task.owner:
        return f"Task {task_id} already owned by {task.owner}"
    ready, blocked = dependency_status(task)
    if not ready:
        return f"Blocked by: {blocked}"
    task.owner = owner
    task.status = "in_progress"
    task.updated_at = int(time.time())
    save_task(task)
    print(f"\033[36m[claim] {task.subject} -> in_progress (owner: {owner})\033[0m")
    return f"Claimed {task.id}: {task.subject}"


def bind_task_to_worktree(task_id: str, worktree_name: str) -> Task:
    task = load_task(task_id)
    task.worktree = worktree_name
    task.updated_at = int(time.time())
    save_task(task)
    return task


def complete_task(task_id: str, result: str = "") -> str:
    task = load_task(task_id)
    if task.status != "in_progress":
        return f"Task {task_id} is {task.status}, cannot complete"
    now = int(time.time())
    task.status = "completed"
    task.result = result or None
    task.updated_at = now
    task.completed_at = now
    save_task(task)
    unblocked = [
        task for task in list_tasks()
        if task.status == "pending" and task.blocked_by and can_start(task.id)
    ]
    print(f"\033[32m[complete] {task.subject} done\033[0m")
    message = f"Completed {task.id}: {task.subject}"
    if unblocked:
        names = ", ".join(task.subject for task in unblocked)
        print(f"\033[33m[unblocked] {names}\033[0m")
        message += f"\nUnblocked: {names}"
    return message
