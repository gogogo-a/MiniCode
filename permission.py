"""
权限模块：把工具调用转换为 allow / deny / ask / passthrough 四态决策。

函数职责：
- PermissionBehavior / PermissionDecision / PermissionRule：表达权限结果和规则。
- tool_call_name_args：解析 OpenAI tool_call。
- check_permissions：按项目规则、会话规则、工具规则和自动审批生成决策。
- resolve_permission_decision：把 passthrough 收敛成最终 ask。
- permission_hook：作为 PreToolUse hook 入口，处理拒绝、询问和子 agent 权限冒泡。
"""

from __future__ import annotations

import fnmatch
import json
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from config import WORKDIR


class PermissionBehavior(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"
    PASSTHROUGH = "passthrough"


@dataclass
class PermissionRule:
    tool_name: str
    behavior: PermissionBehavior
    content: str = "*"
    source: str = "session"


@dataclass
class PermissionDecision:
    behavior: PermissionBehavior
    reason: str
    source: str
    rule: PermissionRule | None = None


PROJECT_PERMISSIONS_PATH = WORKDIR / ".agent" / "permissions.json"
SESSION_RULES: list[PermissionRule] = []

DENY_COMMAND_PATTERNS = [
    "rm -rf /",
    "sudo",
    "shutdown",
    "reboot",
    "mkfs",
    "dd if=",
    "chmod 777",
    "git push",
    "npm publish",
]
SAFE_BASH_PREFIXES = (
    "pwd",
    "ls",
    "rg",
    "cat",
    "sed -n",
    "head",
    "tail",
    "python -m unittest",
    "python -m pytest",
)


def scheduled_mode() -> bool:
    return os.getenv("SCHEDULED_MODE") == "1"


def current_agent_name() -> str:
    try:
        from team import current_agent

        return current_agent()
    except Exception:
        return "lead"


def non_interactive_agent() -> bool:
    return scheduled_mode() or current_agent_name() != "lead"


def tool_call_name_args(tool_call):
    tool_name = tool_call["function"]["name"]
    args = json.loads(tool_call["function"]["arguments"])
    return tool_name, args


def clear_session_rules() -> None:
    SESSION_RULES.clear()


def add_session_rule(tool_name: str, behavior: str, content: str = "*") -> PermissionRule:
    rule = PermissionRule(
        tool_name=tool_name,
        behavior=PermissionBehavior(behavior),
        content=content,
        source="session",
    )
    SESSION_RULES.append(rule)
    return rule


def _load_project_rules() -> list[PermissionRule]:
    path = Path(PROJECT_PERMISSIONS_PATH)
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    rules = []
    for item in data.get("rules", []):
        try:
            rules.append(PermissionRule(
                tool_name=str(item["toolName"]),
                behavior=PermissionBehavior(str(item["ruleBehavior"])),
                content=str(item.get("ruleContent", "*")),
                source="project",
            ))
        except (KeyError, ValueError, TypeError):
            continue
    return rules


def _cli_rules() -> list[PermissionRule]:
    rules = []
    for env_name, behavior in (
        ("S20_ALLOWED_TOOLS", PermissionBehavior.ALLOW),
        ("S20_DENIED_TOOLS", PermissionBehavior.DENY),
    ):
        raw = os.getenv(env_name, "")
        for tool_name in [item.strip() for item in raw.split(",") if item.strip()]:
            rules.append(PermissionRule(tool_name, behavior, "*", "cliArg"))
    return rules


def _decision(behavior: PermissionBehavior, reason: str, source: str, rule: PermissionRule | None = None) -> PermissionDecision:
    return PermissionDecision(behavior=behavior, reason=reason, source=source, rule=rule)


def _target_text(tool_name: str, args: dict[str, Any]) -> str:
    if tool_name == "bash":
        return str(args.get("command", ""))
    if tool_name in ("read_file", "write_file", "edit_file"):
        return str(args.get("path", ""))
    if tool_name == "glob":
        return str(args.get("pattern", ""))
    return json.dumps(args, ensure_ascii=False, sort_keys=True)


def _rule_matches(rule: PermissionRule, tool_name: str, args: dict[str, Any]) -> bool:
    if rule.tool_name not in (tool_name, "*"):
        return False
    content = rule.content or "*"
    if content == "*":
        return True
    target = _target_text(tool_name, args)
    prefix = content[:-2] if content.endswith(":*") else content.rstrip("*")
    return fnmatch.fnmatch(target, content) or target.startswith(prefix)


def _first_matching_rule(rules: list[PermissionRule], tool_name: str, args: dict[str, Any]) -> PermissionRule | None:
    for rule in rules:
        if _rule_matches(rule, tool_name, args):
            return rule
    return None


def _rule_decision(rule: PermissionRule) -> PermissionDecision:
    return _decision(rule.behavior, f"Matched {rule.source} rule: {rule.content}", rule.source, rule)


def tool_check_permissions(tool_name: str, args: dict[str, Any]) -> PermissionDecision:
    if tool_name == "bash":
        command = str(args.get("command", "")).strip()
        for pattern in DENY_COMMAND_PATTERNS:
            if pattern in command:
                return _decision(PermissionBehavior.DENY, f"Command contains blocked pattern: {pattern}", "tool")
        if command.startswith(SAFE_BASH_PREFIXES):
            return _decision(PermissionBehavior.ALLOW, "Command is a read-only shell operation.", "auto")
        return _decision(PermissionBehavior.ASK, "Shell command needs approval.", "tool")
    if tool_name in ("read_file", "glob"):
        return _decision(PermissionBehavior.ALLOW, f"{tool_name} is read-only.", "auto")
    if tool_name in ("write_file", "edit_file"):
        return _decision(PermissionBehavior.ASK, f"{tool_name} changes files.", "tool")
    return _decision(PermissionBehavior.PASSTHROUGH, "No tool-specific permission decision.", "tool")


def check_permissions(tool_call, context: dict | None = None) -> PermissionDecision:
    tool_name, args = tool_call_name_args(tool_call)
    project_rule = _first_matching_rule(_load_project_rules(), tool_name, args)
    if project_rule and project_rule.behavior == PermissionBehavior.DENY:
        return _rule_decision(project_rule)

    cli_rule = _first_matching_rule(_cli_rules(), tool_name, args)
    if cli_rule:
        return _rule_decision(cli_rule)

    session_rule = _first_matching_rule(SESSION_RULES, tool_name, args)
    if session_rule:
        return _rule_decision(session_rule)

    if project_rule:
        return _rule_decision(project_rule)

    return tool_check_permissions(tool_name, args)


def resolve_permission_decision(decision: PermissionDecision, tool_call) -> PermissionDecision:
    if decision.behavior != PermissionBehavior.PASSTHROUGH:
        return decision
    tool_name, _ = tool_call_name_args(tool_call)
    return _decision(PermissionBehavior.ASK, f"{tool_name} has no explicit permission rule.", "pipeline")


def _ask_lead(decision: PermissionDecision, tool_call) -> str | None:
    tool_name, args = tool_call_name_args(tool_call)
    print(f"\n\033[33mPermission required: {decision.reason}\033[0m")
    print(f"Tool: {tool_name}({args})")
    choice = input("Allow? [y/N] ").strip().lower()
    if choice in ("y", "yes"):
        return None
    return "Permission denied by user"


def _bubble_to_lead(decision: PermissionDecision, tool_call) -> str:
    from team import request_permission_from_lead

    tool_name, args = tool_call_name_args(tool_call)
    return request_permission_from_lead(current_agent_name(), tool_name, args, decision.reason)


def permission_hook(tool_call):
    decision = resolve_permission_decision(check_permissions(tool_call), tool_call)
    if decision.behavior == PermissionBehavior.ALLOW:
        return None
    if decision.behavior == PermissionBehavior.DENY:
        return f"Permission denied: {decision.reason}"
    if scheduled_mode():
        return f"Permission denied in scheduled mode: {decision.reason}"
    if current_agent_name() != "lead":
        return _bubble_to_lead(decision, tool_call)
    return _ask_lead(decision, tool_call)
