"""
System prompt 模块：根据运行时真实状态拼接 system prompt，并做本地缓存。

函数职责：
- tool_prompt_lines：从真实 TOOLS 定义生成工具说明文本。
- update_context：收集工作区、工具列表、skills 索引和 memory 索引。
- assemble_system_prompt：把身份、工作区、动态工具池、任务系统、后台任务、系统定时任务、团队协议、worktree、MCP、skills、memory 拼成 system prompt。
- get_system_prompt：用 json.dumps(context) 判断 context 是否变化，不变就复用上次 prompt。
"""

import json

from config import WORKDIR
from memory import read_memory_index
from mcp_plugin import assemble_tool_pool
from skills import list_skills
from todo_system import todo_snapshot
from tools import TOOL_HANDLERS, TOOLS


PROMPT_SECTIONS = {
    "identity": "You are a coding agent. Use tools to solve tasks. Act, don't explain.",
    "tool_boundary": "Only use and describe tools listed in the Available tools section.",
    "task_system": (
        "Use todo_write for a short current-session checklist when the user asks for a todo list or inspection plan. "
        "For work that has ordered steps or dependencies, use persistent task tools: "
        "create_task, list_tasks, list_ready_tasks, claim_task, and complete_task. "
        "Represent dependencies with blocked_by task IDs. Claim only ready tasks."
    ),
    "background": (
        "For long-running shell commands, set bash.run_in_background=true. "
        "Background completions arrive later as <task_notification> messages."
    ),
    "system_scheduler": (
        "Use schedule_cron, list_crons, and cancel_cron for system-level scheduled jobs. "
        "Jobs are persisted under .scheduled_tasks and run only when an external scheduler calls code.py --tick. "
        "Do not install crontab or launchd from inside the agent. Scheduled runs are fresh agents and deny dangerous commands or writes outside the workspace."
    ),
    "team": (
        "For work that benefits from teammates, use spawn_teammate(name, role, prompt). "
        "Teammates run in reusable threads with their own context and .mailboxes inbox. "
        "When the user asks to shut down a teammate, use request_shutdown; do not use send_message for shutdown. "
        "Use request_plan for risky work so the teammate submits a plan before making changes. "
        "If the user asks teammates to submit plans before claiming tasks, call require_plan_before_claim for each teammate. "
        "Use review_plan(request_id, approve, feedback) to approve or reject pending plans. "
        "Use review_permission(request_id, approve, feedback) to approve or reject teammate permission requests. "
        "Use protocol_status to inspect pending_requests state. "
        "Protocol messages carry request_id and results may arrive as [Inbox] messages. "
        "Idle teammates automatically check inbox first, then claim ready unowned tasks from the task board. "
        "When asked to let teammates auto-claim, create tasks and spawn teammates; do not manually assign every task. "
        "Use create_worktree(name, task_id) to bind a task to an isolated git worktree while keeping the task pending. "
        "When a teammate claims a task with worktree, its bash/read/write/edit/glob tools run inside that worktree cwd. "
        "Use keep_worktree for review or remove_worktree; remove_worktree refuses dirty worktrees unless discard_changes=true. "
        "Use connect_mcp(name) to connect a real MCP server. After connecting, MCP tools appear with mcp__server__tool names."
    ),
    "permissions": (
        "Permission decisions are allow, deny, ask, or passthrough. "
        "Passthrough is not approval; it means the common permission pipeline must decide. "
        "Read-only file and search tools may be auto-approved, file writes and general shell commands may ask, and clearly dangerous commands are denied. "
        "Scheduled runs deny operations that need interaction. "
        "Teammate permission prompts bubble to lead as permission_request messages; wait for permission_response before retrying that tool."
    ),
    "reminders": (
        "Use schedule_reminder for in-session reminders. A reminder due later arrives as a <task_notification>."
    ),
    "skills": "Use load_skill(name) to load full skill instructions before applying a skill.",
    "memory": (
        "Relevant full memory contents may be injected into the current turn. "
        "Respect user preferences from memory."
    ),
    "file_creation": (
        "When creating a code file and the user does not provide exact contents, "
        "write a small useful file instead of an empty placeholder."
    ),
}

_last_context_key = None
_last_prompt = None


def tool_prompt_lines(tools: list) -> list[str]:
    lines = []
    for tool in tools:
        function = tool.get("function", tool)
        name = function.get("name", "")
        description = function.get("description", "")
        parameters = function.get("parameters", {})
        required = function.get("required", parameters.get("required", []))
        required_text = f" required: {', '.join(required)}." if required else ""
        if name:
            lines.append(f"- {name}: {description}{required_text}")
    return lines


def update_context() -> dict:
    current_tools, _ = assemble_tool_pool(TOOLS, TOOL_HANDLERS)
    return {
        "workspace": str(WORKDIR),
        "tools": [
            {
                "name": tool["function"]["name"],
                "description": tool["function"].get("description", ""),
                "required": tool["function"].get("parameters", {}).get("required", []),
            }
            for tool in current_tools
        ],
        "skills": list_skills(),
        "memory_index": read_memory_index(),
        "todo": todo_snapshot(),
    }


def assemble_system_prompt(context: dict) -> str:
    sections = [
        PROMPT_SECTIONS["identity"],
        f"Working directory: {context['workspace']}",
        "Available tools:\n" + "\n".join(tool_prompt_lines(context["tools"])),
        PROMPT_SECTIONS["tool_boundary"],
        PROMPT_SECTIONS["task_system"],
        PROMPT_SECTIONS["background"],
        PROMPT_SECTIONS["reminders"],
        PROMPT_SECTIONS["system_scheduler"],
        PROMPT_SECTIONS["team"],
        PROMPT_SECTIONS["permissions"],
        f"Skills available:\n{context['skills']}\n{PROMPT_SECTIONS['skills']}",
        PROMPT_SECTIONS["file_creation"],
    ]
    if context.get("memory_index"):
        sections.append(f"Memories available:\n{context['memory_index']}\n{PROMPT_SECTIONS['memory']}")
    if context.get("todo") and context["todo"] != "No active todo list.":
        sections.append(f"Current todo list:\n{context['todo']}")
    return "\n\n".join(section for section in sections if section)


def get_system_prompt(context: dict) -> str:
    global _last_context_key, _last_prompt
    key = json.dumps(context, sort_keys=True, ensure_ascii=False, default=str)
    if key == _last_context_key and _last_prompt is not None:
        print("\033[90m[system prompt: cache hit]\033[0m")
        return _last_prompt
    _last_context_key = key
    _last_prompt = assemble_system_prompt(context)
    section_names = ["identity", "workspace", "tools", "tool_boundary", "task_system", "background", "reminders", "system_scheduler", "team", "permissions", "skills", "file_creation"]
    if context.get("memory_index"):
        section_names.append("memory")
    print(f"\033[90m[system prompt: assembled {', '.join(section_names)}]\033[0m")
    return _last_prompt
