#!/usr/bin/env python3
"""
s20: Comprehensive Agent — one loop with tools, hooks, memory, compact, team, worktree, scheduler, background, and MCP.

入口职责：
- 初始化 readline，修复 macOS 中文输入退格问题。
- 注册 teammate 邮箱和协议工具。
- 无参数时启动命令行 REPL。
- `--tick` 时执行一个到期的持久化定时任务。
- `--run-job JOB_ID` 时执行指定定时任务。
- 把用户输入追加到 history，并交给 agent_loop。
- 打印每轮最终 assistant 文本。

Run: python s20_comprehensive_agent/code.py
Tick: python s20_comprehensive_agent/code.py --tick
Needs: pip install openai python-dotenv + OPENAI_API_KEY in .env
"""

import os
import sys

try:
    import readline

    readline.parse_and_bind('set bind-tty-special-chars off')
    readline.parse_and_bind('set input-meta on')
    readline.parse_and_bind('set output-meta on')
    readline.parse_and_bind('set convert-meta off')
except ImportError:
    pass

from hooks import trigger_hooks
from team import register_team_tools


def repl():
    register_team_tools()
    from loop import agent_loop

    print("s20: Comprehensive Agent")
    print("输入问题，回车发送。输入 q 退出。\n")
    history = []
    while True:
        try:
            query = input("\033[36ms20 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break
        trigger_hooks("UserPromptSubmit", query)
        history.append({"role": "user", "content": query})
        agent_loop(history)
        response_content = history[-1]["content"]
        if isinstance(response_content, str):
            print(response_content)
        print()


def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "--tick":
        os.environ["SCHEDULED_MODE"] = "1"
        from system_scheduler import run_tick

        raise SystemExit(run_tick())
    if len(sys.argv) >= 3 and sys.argv[1] == "--run-job":
        os.environ["SCHEDULED_MODE"] = "1"
        from system_scheduler import run_job_by_id

        raise SystemExit(run_job_by_id(sys.argv[2]))
    repl()


if __name__ == "__main__":
    main()
