"""
Worktree 系统模块：创建、绑定、保留和删除任务隔离工作目录。

函数职责：
- validate_worktree_name：限制 worktree 名称只包含安全字符。
- run_git：在 WORKDIR 执行 git 命令并返回成功状态和输出。
- log_event：把 create/remove/keep 生命周期事件追加到 `.worktrees/events.jsonl`。
- create_worktree：创建 `.worktrees/{name}` 和 `wt/{name}` 分支，可绑定任务。
- remove_worktree：默认拒绝删除有未提交改动或本地提交的 worktree。
- keep_worktree：记录保留事件，留给人工 review。
- worktree_path：返回 worktree 目录路径。
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

from config import WORKDIR
from task_system import bind_task_to_worktree


WORKTREES_DIR = WORKDIR / ".worktrees"
EVENTS_FILE = WORKTREES_DIR / "events.jsonl"
NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def validate_worktree_name(name: str) -> None:
    if not NAME_RE.fullmatch(name):
        raise ValueError("worktree name must match [A-Za-z0-9._-]{1,64}")


def worktree_path(name: str) -> Path:
    validate_worktree_name(name)
    return WORKTREES_DIR / name


def run_git(args: list[str], cwd: Path | None = None) -> tuple[bool, str]:
    result = subprocess.run(
        ["git"] + args,
        cwd=cwd or WORKDIR,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    output = (result.stdout + result.stderr).strip()
    return result.returncode == 0, output


def log_event(event_type: str, worktree_name: str, task_id: str = "") -> None:
    WORKTREES_DIR.mkdir(parents=True, exist_ok=True)
    event = {
        "type": event_type,
        "worktree": worktree_name,
        "task_id": task_id,
        "ts": time.time(),
    }
    with EVENTS_FILE.open("a", encoding="utf-8") as file:
        file.write(json.dumps(event, ensure_ascii=False) + "\n")


def create_worktree(name: str, task_id: str = "") -> str:
    try:
        validate_worktree_name(name)
        WORKTREES_DIR.mkdir(parents=True, exist_ok=True)
        path = worktree_path(name)
        ok, result = run_git(["worktree", "add", str(path), "-b", f"wt/{name}", "HEAD"])
        if not ok:
            return f"Git error: {result}"
        if task_id:
            bind_task_to_worktree(task_id, name)
        log_event("create", name, task_id)
        return f"Worktree '{name}' created at {path}"
    except Exception as error:
        return f"Error: {error}"


def _count_worktree_changes(path: Path) -> tuple[int, int]:
    ok, status = run_git(["status", "--porcelain"], cwd=path)
    files = len(status.splitlines()) if ok and status else 0
    ok, base_head = run_git(["rev-parse", "HEAD"], cwd=WORKDIR)
    if not ok:
        return files, 0
    ok, commits = run_git(["rev-list", "--count", "HEAD", f"^{base_head.strip()}"], cwd=path)
    commit_count = 0
    if ok:
        try:
            commit_count = int(commits.strip() or "0")
        except ValueError:
            commit_count = 0
    return files, commit_count


def remove_worktree(name: str, discard_changes: bool = False) -> str:
    try:
        validate_worktree_name(name)
        path = worktree_path(name)
        if not path.exists():
            return f"Error: worktree {name} not found"
        if not discard_changes:
            files, commits = _count_worktree_changes(path)
            if files > 0 or commits > 0:
                return (
                    "Worktree has changes. Use discard_changes=true to remove, "
                    "or keep_worktree to keep it for review."
                )
        ok, result = run_git(["worktree", "remove", str(path), "--force"])
        if not ok:
            return f"Git error: {result}"
        run_git(["branch", "-D", f"wt/{name}"])
        log_event("remove", name)
        return f"Worktree '{name}' removed"
    except Exception as error:
        return f"Error: {error}"


def keep_worktree(name: str) -> str:
    try:
        validate_worktree_name(name)
        if not worktree_path(name).exists():
            return f"Error: worktree {name} not found"
        log_event("keep", name)
        return f"Worktree '{name}' kept for review (branch: wt/{name})"
    except Exception as error:
        return f"Error: {error}"
