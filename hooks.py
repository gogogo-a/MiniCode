"""
Hook 模块：提供 agent 生命周期钩子，并注册默认的权限、日志和统计钩子。

函数职责：
- register_hook：把回调函数注册到某个事件。
- trigger_hooks：按顺序执行某个事件下的回调，返回第一个阻断结果。
- log_hook：打印将要执行的工具名和参数预览。
- large_output_hook：工具输出过大时打印提醒。
- context_inject_hook：用户提交问题时显示当前工作区。
- summary_hook：每轮结束时显示本轮累计工具调用数量。
"""

from config import WORKDIR
from permission import permission_hook, tool_call_name_args


HOOKS = {"UserPromptSubmit": [], "PreToolUse": [], "PostToolUse": [], "Stop": []}


def register_hook(event: str, callback):
    HOOKS[event].append(callback)


def trigger_hooks(event: str, *args):
    for callback in HOOKS[event]:
        result = callback(*args)
        if result is not None:
            return result
    return None


def log_hook(tool_call):
    tool_name, args = tool_call_name_args(tool_call)
    args_preview = str(list(args.values())[:2])[:60]
    print(f"\033[90m[HOOK] {tool_name}({args_preview})\033[0m")
    return None


def large_output_hook(tool_call, output):
    tool_name, _ = tool_call_name_args(tool_call)
    if len(str(output)) > 100000:
        print(f"\033[33m[HOOK] ⚠ Large output from {tool_name}: {len(str(output))} chars\033[0m")
    return None


def context_inject_hook(query: str):
    print(f"\033[90m[HOOK] UserPromptSubmit: working in {WORKDIR}\033[0m")
    return None


def summary_hook(messages: list):
    tool_count = sum(1 for message in messages if message.get("role") == "tool")
    print(f"\033[90m[HOOK] Stop: session used {tool_count} tool calls\033[0m")
    return None


register_hook("UserPromptSubmit", context_inject_hook)
register_hook("PreToolUse", permission_hook)
register_hook("PreToolUse", log_hook)
register_hook("PostToolUse", large_output_hook)
register_hook("Stop", summary_hook)
