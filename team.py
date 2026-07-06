"""
团队协议模块：在文件邮箱、协议层和任务看板之上增加 worktree cwd 隔离。

函数职责：
- ProtocolState：记录协议请求的 request_id、类型、双方、状态和 payload。
- MessageBus.send：把普通消息或协议消息写入目标 `.mailboxes/{agent}.jsonl`。
- MessageBus.read_inbox：消费式读取 inbox，并用文件锁避免并发丢消息。
- new_request_id：生成可追踪的协议请求 ID。
- match_response：按 request_id 和响应类型更新 pending_requests 状态。
- consume_lead_inbox：Lead 统一消费 inbox，先路由协议响应，再返回可注入消息。
- dispatch_teammate_message：队友按消息类型处理 shutdown 和计划审批响应。
- request_shutdown：Lead 向队友发起关机握手。
- request_plan：Lead 要求队友先提交计划。
- review_plan：Lead 审批或拒绝队友提交的计划。
- submit_plan：队友向 Lead 提交计划审批请求。
- teammate_loop：队友执行任务后进入 idle loop，等待新消息或 shutdown_request。
- scan_unclaimed_tasks：扫描可开始、无 owner 的 pending 任务。
- idle_poll：队友空闲时先看 inbox，再自动领取任务，超时后退出。
- teammate_handlers：队友认领带 worktree 的任务后，把 bash/read/write/edit/glob 切到对应 cwd。
- register_team_tools：注册 Lead 的团队和协议工具。
"""

from __future__ import annotations

import contextlib
import dataclasses
import fcntl
import inspect
import json
import threading
import time
from typing import Iterator

from config import DEFAULT_MAX_TOKENS, MAILBOX_DIR, WORKDIR
from hooks import trigger_hooks
from llm import RecoveryState, chat_with_system
from permission import tool_call_name_args
from worktree_system import WORKTREES_DIR


BUS_AGENT = threading.local()
ACTIVE_TEAMMATES: dict[str, threading.Thread] = {}
PENDING_REQUESTS: dict[str, "ProtocolState"] = {}
PLAN_REQUIRED_AGENTS: dict[str, bool] = {}
PLAN_APPROVED_AGENTS: dict[str, bool] = {}
REQUEST_COUNTER = 0
REQUEST_LOCK = threading.Lock()
IDLE_POLL_INTERVAL = 5
IDLE_TIMEOUT = 60
WORK_TURN_LIMIT = 10


@dataclasses.dataclass
class ProtocolState:
    request_id: str
    type: str
    sender: str
    target: str
    status: str
    payload: str
    created_at: float
    response: str = ""


@contextlib.contextmanager
def mailbox_lock(agent: str) -> Iterator[None]:
    MAILBOX_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = MAILBOX_DIR / f"{agent}.lock"
    handle = lock_path.open("w", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


class MessageBus:
    def send(
        self,
        from_agent: str,
        to_agent: str,
        content: str,
        msg_type: str = "message",
        metadata: dict | None = None,
    ) -> None:
        MAILBOX_DIR.mkdir(parents=True, exist_ok=True)
        msg = {
            "from": from_agent,
            "to": to_agent,
            "content": content,
            "type": msg_type,
            "metadata": metadata or {},
            "ts": time.time(),
        }
        inbox = MAILBOX_DIR / f"{to_agent}.jsonl"
        with mailbox_lock(to_agent):
            with inbox.open("a", encoding="utf-8") as file:
                file.write(json.dumps(msg, ensure_ascii=False) + "\n")

    def read_inbox(self, agent: str) -> list[dict]:
        MAILBOX_DIR.mkdir(parents=True, exist_ok=True)
        inbox = MAILBOX_DIR / f"{agent}.jsonl"
        with mailbox_lock(agent):
            if not inbox.exists():
                return []
            lines = inbox.read_text(encoding="utf-8").splitlines()
            inbox.unlink()
        messages = []
        for line in lines:
            try:
                messages.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return messages


BUS = MessageBus()


def current_agent() -> str:
    return getattr(BUS_AGENT, "name", "lead")


def set_current_agent(name: str) -> None:
    BUS_AGENT.name = name


def new_request_id() -> str:
    global REQUEST_COUNTER
    with REQUEST_LOCK:
        REQUEST_COUNTER += 1
        return f"req_{REQUEST_COUNTER:06d}"


def protocol_snapshot() -> str:
    if not PENDING_REQUESTS:
        return "No pending protocol requests."
    return json.dumps(
        [dataclasses.asdict(state) for state in PENDING_REQUESTS.values()],
        ensure_ascii=False,
        indent=2,
    )


def _expected_response(protocol_type: str) -> str:
    return {
        "shutdown": "shutdown_response",
        "plan_approval": "plan_approval_response",
        "permission": "permission_response",
    }.get(protocol_type, "")


def match_response(response_type: str, request_id: str, approve: bool, response: str = "") -> None:
    state = PENDING_REQUESTS.get(request_id)
    if state is None:
        return
    if response_type != _expected_response(state.type):
        return
    if state.status != "pending":
        return
    state.status = "approved" if approve else "rejected"
    state.response = response
    if state.type == "plan_approval" and state.target != "lead":
        PLAN_APPROVED_AGENTS[state.target] = approve
    print(f"\033[36m[protocol] {request_id} {state.type} -> {state.status}\033[0m")


def consume_lead_inbox(route_protocol: bool = True) -> list[dict]:
    messages = BUS.read_inbox("lead")
    if not route_protocol:
        return messages
    for msg in messages:
        metadata = msg.get("metadata", {})
        request_id = metadata.get("request_id", "")
        msg_type = msg.get("type", "")
        if request_id and msg_type.endswith("_response"):
            approve = bool(metadata.get("approve", False))
            match_response(msg_type, request_id, approve, str(msg.get("content", "")))
    return messages


def run_send_message(to_agent: str, content: str, msg_type: str = "message") -> str:
    from_agent = current_agent()
    BUS.send(from_agent, to_agent, content, msg_type)
    return f"Sent {msg_type} from {from_agent} to {to_agent}"


def run_check_inbox(agent: str | None = None) -> str:
    name = agent or current_agent()
    if current_agent() == "lead" and name != "lead":
        return "Error: Lead can only check lead inbox. Use send_message, request_shutdown, or request_plan to contact teammates."
    messages = consume_lead_inbox() if name == "lead" else BUS.read_inbox(name)
    if not messages:
        return "No messages."
    return json.dumps(messages, ensure_ascii=False, indent=2)


def request_permission_from_lead(agent: str, tool_name: str, args: dict, reason: str) -> str:
    request_id = new_request_id()
    payload = json.dumps(
        {"tool_name": tool_name, "args": args, "reason": reason},
        ensure_ascii=False,
        indent=2,
    )
    PENDING_REQUESTS[request_id] = ProtocolState(
        request_id=request_id,
        type="permission",
        sender=agent,
        target="lead",
        status="pending",
        payload=payload,
        created_at=time.time(),
    )
    BUS.send(
        agent,
        "lead",
        payload,
        "permission_request",
        {"request_id": request_id, "tool_name": tool_name},
    )
    return f"Permission requested from lead: {request_id}"


def review_permission(request_id: str, approve: bool, feedback: str = "") -> str:
    state = PENDING_REQUESTS.get(request_id)
    if state is None:
        return f"Error: request {request_id} not found"
    if state.type != "permission":
        return f"Error: request {request_id} is {state.type}, not permission"
    if state.status != "pending":
        return f"Error: request {request_id} is already {state.status}"
    state.status = "approved" if approve else "rejected"
    state.response = feedback
    BUS.send(
        "lead",
        state.sender,
        feedback or ("Permission approved." if approve else "Permission denied."),
        "permission_response",
        {"request_id": request_id, "approve": approve},
    )
    return f"{request_id} {state.status}"


def request_shutdown(name: str, reason: str = "Lead requested shutdown.") -> str:
    thread = ACTIVE_TEAMMATES.get(name)
    if thread is None or not thread.is_alive():
        return f"Error: teammate {name} is not running"
    request_id = new_request_id()
    PENDING_REQUESTS[request_id] = ProtocolState(
        request_id=request_id,
        type="shutdown",
        sender="lead",
        target=name,
        status="pending",
        payload=reason,
        created_at=time.time(),
    )
    BUS.send(
        "lead",
        name,
        reason,
        "shutdown_request",
        {"request_id": request_id},
    )
    print(f"\033[36m[protocol] shutdown_request {request_id} -> {name}\033[0m")
    return f"Sent shutdown_request {request_id} to {name}"


def request_plan(name: str, task: str) -> str:
    request_id = new_request_id()
    PENDING_REQUESTS[request_id] = ProtocolState(
        request_id=request_id,
        type="plan_approval",
        sender="lead",
        target=name,
        status="pending",
        payload=task,
        created_at=time.time(),
    )
    BUS.send(
        "lead",
        name,
        f"Submit a plan before doing this task: {task}",
        "plan_request",
        {"request_id": request_id},
    )
    print(f"\033[36m[protocol] plan_request {request_id} -> {name}\033[0m")
    return f"Sent plan_request {request_id} to {name}"


def require_plan_before_claim(name: str, required: bool = True) -> str:
    PLAN_REQUIRED_AGENTS[name] = required
    if required:
        PLAN_APPROVED_AGENTS[name] = False
        return f"{name} must submit an approved plan before claiming tasks."
    PLAN_APPROVED_AGENTS[name] = True
    return f"{name} can claim tasks without a plan gate."


def review_plan(request_id: str, approve: bool, feedback: str = "") -> str:
    state = PENDING_REQUESTS.get(request_id)
    if state is None:
        return f"Error: request {request_id} not found"
    if state.type != "plan_approval":
        return f"Error: request {request_id} is {state.type}, not plan_approval"
    if state.status != "pending":
        return f"Error: request {request_id} is already {state.status}"
    BUS.send(
        "lead",
        state.target,
        feedback or ("Plan approved." if approve else "Plan rejected."),
        "plan_approval_response",
        {"request_id": request_id, "approve": approve},
    )
    state.status = "approved" if approve else "rejected"
    state.response = feedback
    PLAN_APPROVED_AGENTS[state.target] = approve
    print(f"\033[36m[protocol] plan_approval_response {request_id} -> {state.target}: {state.status}\033[0m")
    return f"{request_id} {state.status}"


def submit_plan(plan: str, request_id: str = "") -> str:
    sender = current_agent()
    if not request_id:
        request_id = new_request_id()
    if request_id not in PENDING_REQUESTS:
        PENDING_REQUESTS[request_id] = ProtocolState(
            request_id=request_id,
            type="plan_approval",
            sender=sender,
            target=sender,
            status="pending",
            payload=plan,
            created_at=time.time(),
        )
    BUS.send(
        sender,
        "lead",
        plan,
        "plan_approval_request",
        {"request_id": request_id},
    )
    return f"Submitted plan {request_id} to lead"


def teammate_tools() -> list:
    return [
        {"type": "function", "function": {"name": "bash", "description": "Run a shell command.",
                                          "parameters": {"type": "object", "properties": {"command": {"type": "string"}},
                                                         "required": ["command"], "additionalProperties": False}}},
        {"type": "function", "function": {"name": "read_file", "description": "Read file contents.",
                                          "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}},
                                                         "required": ["path"], "additionalProperties": False}}},
        {"type": "function", "function": {"name": "write_file", "description": "Write content to a file.",
                                          "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                                                         "required": ["path", "content"], "additionalProperties": False}}},
        {"type": "function", "function": {"name": "edit_file", "description": "Replace exact text in a file once.",
                                          "parameters": {"type": "object", "properties": {"path": {"type": "string"},
                                                                                          "old_text": {"type": "string"},
                                                                                          "new_text": {"type": "string"}},
                                                         "required": ["path", "old_text", "new_text"], "additionalProperties": False}}},
        {"type": "function", "function": {"name": "glob", "description": "Find files matching a glob pattern.",
                                          "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}},
                                                         "required": ["pattern"], "additionalProperties": False}}},
        {"type": "function", "function": {"name": "send_message", "description": "Send a message to lead or another teammate.",
                                          "parameters": {"type": "object", "properties": {"to_agent": {"type": "string"},
                                                                                          "content": {"type": "string"},
                                                                                          "msg_type": {"type": "string"}},
                                                         "required": ["to_agent", "content"], "additionalProperties": False}}},
        {"type": "function", "function": {"name": "check_inbox", "description": "Read this teammate's inbox.",
                                          "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}},
        {"type": "function", "function": {"name": "submit_plan", "description": "Submit a plan to lead and wait for plan_approval_response before doing risky work.",
                                          "parameters": {"type": "object", "properties": {"plan": {"type": "string"},
                                                                                          "request_id": {"type": "string"}},
                                                         "required": ["plan"], "additionalProperties": False}}},
        {"type": "function", "function": {"name": "list_tasks", "description": "List all persistent tasks with status, owner, and dependencies.",
                                          "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}},
        {"type": "function", "function": {"name": "claim_task", "description": "Claim a ready pending task and mark it in_progress.",
                                          "parameters": {"type": "object", "properties": {"task_id": {"type": "string"},
                                                                                          "owner": {"type": "string"}},
                                                         "required": ["task_id"], "additionalProperties": False}}},
        {"type": "function", "function": {"name": "complete_task", "description": "Complete an in_progress task and report newly unblocked tasks.",
                                          "parameters": {"type": "object", "properties": {"task_id": {"type": "string"},
                                                                                          "result": {"type": "string"}},
                                                         "required": ["task_id"], "additionalProperties": False}}},
    ]


def teammate_handlers(name: str, wt_ctx: dict) -> dict:
    from task_system import claim_task, load_task
    from tools import run_bash, run_complete_task, run_edit, run_glob, run_list_tasks, run_read, run_write

    def cwd() -> str | None:
        return wt_ctx.get("path")

    def claim_with_worktree(task_id: str, owner: str = "") -> str:
        result = claim_task(task_id, owner or name)
        if "Claimed" in result:
            task = load_task(task_id)
            if task.worktree:
                wt_ctx["path"] = str((WORKTREES_DIR / task.worktree).resolve())
                print(f"\033[36m[worktree] {name} entered {wt_ctx['path']}\033[0m")
        return result

    return {
        "bash": lambda command: run_bash(command, cwd=cwd()),
        "read_file": lambda path, limit=None: run_read(path, limit=limit, cwd=cwd()),
        "write_file": lambda path, content: run_write(path, content, cwd=cwd()),
        "edit_file": lambda path, old_text, new_text: run_edit(path, old_text, new_text, cwd=cwd()),
        "glob": lambda pattern: run_glob(pattern, cwd=cwd()),
        "send_message": run_send_message,
        "check_inbox": run_check_inbox,
        "submit_plan": submit_plan,
        "list_tasks": run_list_tasks,
        "claim_task": claim_with_worktree,
        "complete_task": run_complete_task,
    }


def _call_handler(tool_name: str, args: dict, agent_name: str, wt_ctx: dict):
    handler = teammate_handlers(agent_name, wt_ctx).get(tool_name)
    if handler is None:
        return f"Unknown: {name}"
    params = inspect.signature(handler).parameters
    filtered_args = {key: value for key, value in args.items() if key in params}
    return handler(**filtered_args)


def dispatch_teammate_message(name: str, msg: dict, messages: list) -> bool:
    msg_type = msg.get("type", "message")
    metadata = msg.get("metadata", {})
    request_id = metadata.get("request_id", "")
    content = str(msg.get("content", ""))
    if msg_type == "shutdown_request":
        BUS.send(
            name,
            "lead",
            f"{name} finished current work and is shutting down. Reason: {content}",
            "shutdown_response",
            {"request_id": request_id, "approve": True},
        )
        return True
    if msg_type == "plan_request":
        messages.append({
            "role": "user",
            "content": (
                f"[Plan request {request_id}] {content}\n"
                f"AWAITING_PLAN_APPROVAL request_id={request_id}\n"
                "Use submit_plan with this request_id. Do not make file changes for this task until the plan is approved."
            ),
        })
        return False
    if msg_type == "plan_approval_response":
        approve = bool(metadata.get("approve", False))
        marker = "[Plan approved]" if approve else "[Plan rejected]"
        gate = "PLAN_APPROVED" if approve else "PLAN_REJECTED"
        messages.append({"role": "user", "content": f"{marker} {gate} request_id={request_id}\n{content}"})
        return False
    if msg_type == "permission_response":
        approve = bool(metadata.get("approve", False))
        marker = "[Permission approved]" if approve else "[Permission denied]"
        gate = "PERMISSION_APPROVED" if approve else "PERMISSION_DENIED"
        messages.append({"role": "user", "content": f"{marker} {gate} request_id={request_id}\n{content}"})
        return False
    messages.append({"role": "user", "content": f"<inbox>{json.dumps([msg], ensure_ascii=False)}</inbox>"})
    return False


def scan_unclaimed_tasks() -> list[dict]:
    from task_system import list_tasks, can_start

    tasks = []
    for task in list_tasks():
        if task.status == "pending" and not task.owner and can_start(task.id):
            tasks.append(dataclasses.asdict(task))
    return tasks


def owned_in_progress_tasks(name: str) -> list[dict]:
    from task_system import list_tasks

    return [
        dataclasses.asdict(task)
        for task in list_tasks()
        if task.status == "in_progress" and task.owner == name
    ]


def idle_poll(name: str, messages: list, wt_ctx: dict) -> str:
    from task_system import claim_task
    from task_system import load_task

    for _ in range(IDLE_TIMEOUT // IDLE_POLL_INTERVAL):
        time.sleep(IDLE_POLL_INTERVAL)
        inbox = BUS.read_inbox(name)
        if inbox:
            should_stop = False
            for msg in inbox:
                if dispatch_teammate_message(name, msg, messages):
                    should_stop = True
            if should_stop:
                return "shutdown"
            return "work"
        unclaimed = scan_unclaimed_tasks()
        if unclaimed:
            if PLAN_REQUIRED_AGENTS.get(name) and not PLAN_APPROVED_AGENTS.get(name):
                messages.append({
                    "role": "user",
                    "content": (
                        "[Plan required before auto-claim]\n"
                        "Ready tasks exist, but you must call submit_plan and wait for approval before claiming any task."
                    ),
                })
                return "work"
            task = unclaimed[0]
            result = claim_task(task["id"], name)
            if "Claimed" in result:
                claimed_task = load_task(task["id"])
                if claimed_task.worktree:
                    wt_ctx["path"] = str((WORKTREES_DIR / claimed_task.worktree).resolve())
                    print(f"\033[36m[worktree] {name} entered {wt_ctx['path']}\033[0m")
                messages.append({
                    "role": "user",
                    "content": (
                        f"[Auto-claimed task]\n{json.dumps(task, ensure_ascii=False, indent=2)}\n"
                        f"{result}\nComplete the task, then call complete_task with task_id={task['id']}."
                    ),
                })
                return "work"
    return "timeout"


def teammate_loop(name: str, role: str, prompt: str) -> None:
    set_current_agent(name)
    system = (
        f"You are '{name}', a {role}, working at {WORKDIR}. "
        "Use tools to complete tasks and send results to lead. "
        "For risky refactors, submit a plan first and wait for approval. "
        "After finishing a task, enter IDLE: check inbox first, then auto-claim ready tasks from the task board. "
        "When you auto-claim a task, complete it and call complete_task. "
        "When shutdown_request arrives, reply with shutdown_response and exit."
        "When permission is requested, wait for permission_response before retrying that tool."
    )
    messages = [{"role": "user", "content": prompt}]
    state = RecoveryState()
    awaiting_plan_approval = False
    wt_ctx = {"path": None}
    while True:
        if len(messages) <= 3:
            messages.insert(0, {
                "role": "user",
                "content": f"<identity>You are '{name}', role: {role}. Continue your work.</identity>",
            })
        for _ in range(WORK_TURN_LIMIT):
            inbox = BUS.read_inbox(name)
            if inbox:
                should_stop = False
                for msg in inbox:
                    if dispatch_teammate_message(name, msg, messages):
                        should_stop = True
                    if msg.get("type") == "plan_request":
                        awaiting_plan_approval = True
                    if msg.get("type") == "plan_approval_response":
                        awaiting_plan_approval = False
                if should_stop:
                    return
            try:
                response = chat_with_system(messages[-40:], teammate_tools(), system, state, DEFAULT_MAX_TOKENS)
            except Exception as error:
                BUS.send(name, "lead", f"{name} failed: {error}", "result")
                return
            choice = response["choices"][0]
            message = choice["message"]
            if not message.get("tool_calls"):
                content = message.get("content", "")
                if content:
                    BUS.send(name, "lead", content, "result")
                messages.append({"role": "assistant", "content": content})
                break
            messages.append(message)
            for tool_call in message["tool_calls"]:
                blocked = trigger_hooks("PreToolUse", tool_call)
                if blocked:
                    messages.append({"role": "tool", "tool_call_id": tool_call["id"], "content": str(blocked)})
                    continue
                tool_name, args = tool_call_name_args(tool_call)
                if awaiting_plan_approval and tool_name not in ("submit_plan", "check_inbox", "send_message"):
                    output = "Protocol gate: submit a plan and wait for approval before using this tool."
                    messages.append({"role": "tool", "tool_call_id": tool_call["id"], "content": output})
                    continue
                output = _call_handler(tool_name, args, name, wt_ctx)
                trigger_hooks("PostToolUse", tool_call, output)
                print(f"  \033[90m[{name}] {tool_name}: {str(output)[:100]}\033[0m")
                messages.append({"role": "tool", "tool_call_id": tool_call["id"], "content": str(output)})
        owned_tasks = owned_in_progress_tasks(name)
        if owned_tasks:
            messages.append({
                "role": "user",
                "content": (
                    "[Continue owned task]\n"
                    f"{json.dumps(owned_tasks[0], ensure_ascii=False, indent=2)}\n"
                    "Finish this task now. When done, call complete_task with this task id."
                ),
            })
            continue
        idle_result = idle_poll(name, messages, wt_ctx)
        if idle_result == "work":
            continue
        if idle_result == "shutdown":
            return
        BUS.send(name, "lead", f"{name} idle timeout reached after {IDLE_TIMEOUT}s.", "result")
        return


def spawn_teammate_thread(name: str, role: str, prompt: str) -> str:
    if name in ACTIVE_TEAMMATES and ACTIVE_TEAMMATES[name].is_alive():
        BUS.send("lead", name, prompt, "message")
        return f"Teammate {name} already running. Sent task to inbox."
    thread = threading.Thread(target=teammate_loop, args=(name, role, prompt), daemon=True)
    ACTIVE_TEAMMATES[name] = thread
    thread.start()
    print(f"\033[35m[Teammate spawned] {name}: {role}\033[0m")
    return f"Spawned teammate {name} as {role}"


def inject_lead_inbox(messages: list) -> None:
    inbox = consume_lead_inbox()
    if not inbox:
        return
    lines = []
    for msg in inbox:
        metadata = msg.get("metadata", {})
        req = f" request_id={metadata.get('request_id')}" if metadata.get("request_id") else ""
        lines.append(f"From {msg.get('from')} [{msg.get('type')}{req}]: {str(msg.get('content', ''))[:1000]}")
    messages.append({"role": "user", "content": "[Inbox]\n" + "\n".join(lines)})
    print(f"\033[32m[inbox] {len(inbox)} message(s)\033[0m")


def register_team_tools() -> None:
    from tools import register_tool

    register_tool({"type": "function", "function": {"name": "spawn_teammate", "description": "Start or reuse a teammate agent with its own thread, context, and mailbox.",
                  "parameters": {"type": "object", "properties": {"name": {"type": "string"}, "role": {"type": "string"}, "prompt": {"type": "string"}},
                                 "required": ["name", "role", "prompt"], "additionalProperties": False}}}, spawn_teammate_thread)
    register_tool({"type": "function", "function": {"name": "send_message", "description": "Send a normal message to lead or a teammate mailbox.",
                  "parameters": {"type": "object", "properties": {"to_agent": {"type": "string"}, "content": {"type": "string"}, "msg_type": {"type": "string"}},
                                 "required": ["to_agent", "content"], "additionalProperties": False}}}, run_send_message)
    register_tool({"type": "function", "function": {"name": "check_inbox", "description": "Read and consume messages from lead or a named agent inbox, routing protocol responses.",
                  "parameters": {"type": "object", "properties": {"agent": {"type": "string"}}, "additionalProperties": False}}}, run_check_inbox)
    register_tool({"type": "function", "function": {"name": "request_shutdown", "description": "Send shutdown_request to a teammate and track pending protocol state.",
                  "parameters": {"type": "object", "properties": {"name": {"type": "string"}, "reason": {"type": "string"}},
                                 "required": ["name"], "additionalProperties": False}}}, request_shutdown)
    register_tool({"type": "function", "function": {"name": "request_plan", "description": "Ask a teammate to submit a plan before doing risky work.",
                  "parameters": {"type": "object", "properties": {"name": {"type": "string"}, "task": {"type": "string"}},
                                 "required": ["name", "task"], "additionalProperties": False}}}, request_plan)
    register_tool({"type": "function", "function": {"name": "require_plan_before_claim", "description": "Require a teammate to submit an approved plan before auto-claiming ready tasks.",
                  "parameters": {"type": "object", "properties": {"name": {"type": "string"}, "required": {"type": "boolean"}},
                                 "required": ["name"], "additionalProperties": False}}}, require_plan_before_claim)
    register_tool({"type": "function", "function": {"name": "review_plan", "description": "Approve or reject a pending plan request by request_id.",
                  "parameters": {"type": "object", "properties": {"request_id": {"type": "string"}, "approve": {"type": "boolean"}, "feedback": {"type": "string"}},
                                 "required": ["request_id", "approve"], "additionalProperties": False}}}, review_plan)
    register_tool({"type": "function", "function": {"name": "review_permission", "description": "Approve or reject a pending teammate permission request by request_id.",
                  "parameters": {"type": "object", "properties": {"request_id": {"type": "string"}, "approve": {"type": "boolean"}, "feedback": {"type": "string"}},
                                 "required": ["request_id", "approve"], "additionalProperties": False}}}, review_permission)
    register_tool({"type": "function", "function": {"name": "protocol_status", "description": "List protocol requests and their current status.",
                  "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}}, protocol_snapshot)
