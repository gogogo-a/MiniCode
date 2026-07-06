"""
系统级定时任务模块：把定时任务持久化到文件，并提供外部 runner 调用入口。

函数职责：
- ScheduledJob：描述一个持久化定时任务的全部字段。
- validate_cron / cron_matches / compute_next_run_after：校验和匹配五段 cron 表达式。
- schedule_job / list_jobs / cancel_job：创建、查看和取消 `.scheduled_tasks` 中的任务。
- due_jobs：找出当前已经到期、没有完成的一批任务。
- run_tick：获取 tick 锁后只执行第一个到期任务，保证串行。
- run_job_by_id：获取单任务锁，创建全新 agent 上下文执行任务，并把日志落盘。
"""

from __future__ import annotations

import contextlib
import dataclasses
import fcntl
import json
import os
import sys
import traceback
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator

from config import SCHEDULED_LOCKS_DIR, SCHEDULED_LOGS_DIR, SCHEDULED_TASKS_DIR


@dataclasses.dataclass
class ScheduledJob:
    id: str
    cron: str
    prompt: str
    recurring: bool = True
    durable: bool = True
    last_run_at: str | None = None
    next_run_after: str | None = None
    running: bool = False
    last_status: str = "pending"
    last_error: str | None = None
    created_at: str = ""
    updated_at: str = ""


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def ensure_dirs() -> None:
    SCHEDULED_TASKS_DIR.mkdir(parents=True, exist_ok=True)
    SCHEDULED_LOCKS_DIR.mkdir(parents=True, exist_ok=True)
    SCHEDULED_LOGS_DIR.mkdir(parents=True, exist_ok=True)


def job_path(job_id: str) -> Path:
    return SCHEDULED_TASKS_DIR / f"{job_id}.json"


def load_job(job_id: str) -> ScheduledJob:
    data = json.loads(job_path(job_id).read_text(encoding="utf-8"))
    return ScheduledJob(**data)


def save_job(job: ScheduledJob) -> None:
    ensure_dirs()
    job.updated_at = now_iso()
    job_path(job.id).write_text(
        json.dumps(dataclasses.asdict(job), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _field_matches(field: str, value: int, minimum: int, maximum: int) -> bool:
    if field == "*":
        return True
    for part in field.split(","):
        part = part.strip()
        if not part:
            return False
        step = 1
        if "/" in part:
            part, step_text = part.split("/", 1)
            if not step_text.isdigit() or int(step_text) <= 0:
                return False
            step = int(step_text)
        if part == "*":
            start, end = minimum, maximum
        elif "-" in part:
            start_text, end_text = part.split("-", 1)
            if not start_text.isdigit() or not end_text.isdigit():
                return False
            start, end = int(start_text), int(end_text)
        elif part.isdigit():
            start = end = int(part)
        else:
            return False
        if start < minimum or end > maximum or start > end:
            return False
        if start <= value <= end and (value - start) % step == 0:
            return True
    return False


def validate_cron(cron: str) -> None:
    fields = cron.split()
    if len(fields) != 5:
        raise ValueError("cron must have 5 fields: minute hour day month weekday")
    ranges = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 6)]
    for field, (minimum, maximum) in zip(fields, ranges):
        if not any(_field_matches(field, value, minimum, maximum) for value in range(minimum, maximum + 1)):
            raise ValueError(f"invalid cron field: {field}")


def cron_matches(cron: str, when: datetime | None = None) -> bool:
    when = when or datetime.now()
    minute, hour, day, month, weekday = cron.split()
    return (
        _field_matches(minute, when.minute, 0, 59)
        and _field_matches(hour, when.hour, 0, 23)
        and _field_matches(day, when.day, 1, 31)
        and _field_matches(month, when.month, 1, 12)
        and _field_matches(weekday, when.weekday(), 0, 6)
    )


def compute_next_run_after(cron: str, after: datetime | None = None) -> str | None:
    after = (after or datetime.now()).replace(second=0, microsecond=0) + timedelta(minutes=1)
    for offset in range(366 * 24 * 60):
        candidate = after + timedelta(minutes=offset)
        if cron_matches(cron, candidate):
            return candidate.isoformat()
    return None


def schedule_job(cron: str, prompt: str, recurring: bool = True, durable: bool = True) -> ScheduledJob:
    validate_cron(cron)
    created = now_iso()
    job = ScheduledJob(
        id=f"cron_{uuid.uuid4().hex[:12]}",
        cron=cron,
        prompt=prompt,
        recurring=recurring,
        durable=durable,
        next_run_after=compute_next_run_after(cron, datetime.now() - timedelta(minutes=1)),
        created_at=created,
        updated_at=created,
    )
    save_job(job)
    return job


def list_jobs() -> list[ScheduledJob]:
    ensure_dirs()
    jobs = []
    for path in sorted(SCHEDULED_TASKS_DIR.glob("*.json")):
        try:
            jobs.append(ScheduledJob(**json.loads(path.read_text(encoding="utf-8"))))
        except Exception:
            continue
    return jobs


def cancel_job(job_id: str) -> str:
    path = job_path(job_id)
    if not path.exists():
        return f"Error: scheduled job {job_id} not found"
    job = load_job(job_id)
    job.last_status = "cancelled"
    job.running = False
    job.last_error = None
    job.next_run_after = None
    save_job(job)
    return f"Cancelled {job_id}"


def due_jobs(when: datetime | None = None) -> list[ScheduledJob]:
    when = when or datetime.now()
    ready = []
    for job in list_jobs():
        if job.running or job.last_status == "cancelled":
            continue
        if not job.recurring and job.last_run_at:
            continue
        next_after = parse_dt(job.next_run_after)
        if next_after and next_after > when:
            continue
        if cron_matches(job.cron, when):
            ready.append(job)
    return sorted(ready, key=lambda item: item.created_at)


@contextlib.contextmanager
def file_lock(name: str) -> Iterator[bool]:
    ensure_dirs()
    lock_path = SCHEDULED_LOCKS_DIR / f"{name}.lock"
    handle = lock_path.open("w", encoding="utf-8")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            handle.write(str(os.getpid()))
            handle.flush()
            yield True
        except BlockingIOError:
            yield False
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def log_path(job_id: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return SCHEDULED_LOGS_DIR / f"{job_id}_{stamp}.log"


def final_text(messages: list) -> str:
    for message in reversed(messages):
        if message.get("role") == "assistant" and isinstance(message.get("content"), str):
            return message["content"]
    return ""


def run_job_by_id(job_id: str) -> int:
    ensure_dirs()
    with file_lock(job_id) as locked:
        if not locked:
            print(f"[scheduled] {job_id} locked, skipping")
            return 0
        try:
            job = load_job(job_id)
        except FileNotFoundError:
            print(f"[scheduled] {job_id} not found")
            return 1
        if job.running:
            print(f"[scheduled] {job_id} already running, skipping")
            return 0
        log_file = log_path(job_id)
        job.running = True
        job.last_status = "running"
        job.last_error = None
        save_job(job)
        code = 0
        with log_file.open("w", encoding="utf-8") as log:
            old_stdout, old_stderr = sys.stdout, sys.stderr
            sys.stdout = sys.stderr = log
            try:
                print(f"[scheduled] start {job.id} at {now_iso()}")
                print(f"[scheduled] cron {job.cron}")
                print(f"[scheduled] prompt {job.prompt}")
                os.environ["SCHEDULED_MODE"] = "1"
                from loop import agent_loop
                from team import register_team_tools

                register_team_tools()
                history = [{"role": "user", "content": f"[Scheduled] {job.prompt}"}]
                agent_loop(history)
                answer = final_text(history)
                print(f"[scheduled] final {answer}")
                job.last_status = "completed"
            except Exception:
                code = 1
                job.last_status = "failed"
                job.last_error = traceback.format_exc()
                print(job.last_error)
            finally:
                sys.stdout, sys.stderr = old_stdout, old_stderr
        job.running = False
        job.last_run_at = now_iso()
        job.next_run_after = compute_next_run_after(job.cron) if job.recurring else None
        save_job(job)
        print(f"[scheduled] {job.id} {job.last_status}, log: {log_file}")
        return code


def run_tick() -> int:
    ensure_dirs()
    with file_lock("tick") as locked:
        if not locked:
            print("[tick] locked, skipping")
            return 0
        jobs = due_jobs()
        if not jobs:
            print("[tick] no due jobs")
            return 0
        job = jobs[0]
        print(f"[tick] running {job.id}")
        return run_job_by_id(job.id)
