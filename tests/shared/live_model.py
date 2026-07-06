from __future__ import annotations

import contextlib
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


ROOT = Path(__file__).resolve().parents[2]


@dataclass
class LiveRunResult:
    name: str
    prompt: str
    tool_names: list[str]
    final: str
    tool_outputs: list[str]


@contextlib.contextmanager
def quiet_memory() -> Iterator[None]:
    import loop

    original_load = loop.load_memories
    original_extract = loop.extract_memories
    original_consolidate = loop.consolidate_memories
    loop.load_memories = lambda *_args, **_kwargs: ""
    loop.extract_memories = lambda *_args, **_kwargs: None
    loop.consolidate_memories = lambda *_args, **_kwargs: None
    try:
        yield
    finally:
        loop.load_memories = original_load
        loop.extract_memories = original_extract
        loop.consolidate_memories = original_consolidate


@contextlib.contextmanager
def live_workspace(name: str, files: dict[str, str]) -> Iterator[Path]:
    import config
    import permission
    import team
    import tools

    old_workdir = config.WORKDIR
    old_tools_workdir = tools.WORKDIR
    old_project_rules = permission.PROJECT_PERMISSIONS_PATH
    old_agent = team.current_agent()
    old_scheduled = os.environ.get("SCHEDULED_MODE")
    safe_name = "".join(char if char.isalnum() or char in ("-", "_") else "_" for char in name)
    workspace = ROOT / ".live_smoke_workspace" / safe_name
    workspace.mkdir(parents=True, exist_ok=True)
    for path in workspace.iterdir():
        if path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
    for name, content in files.items():
        target = workspace / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    config.WORKDIR = workspace
    tools.WORKDIR = workspace
    permission.PROJECT_PERMISSIONS_PATH = workspace / ".agent" / "permissions.json"
    team.set_current_agent("scenario")
    os.environ.pop("SCHEDULED_MODE", None)
    try:
        yield workspace
    finally:
        team.set_current_agent(old_agent)
        if old_scheduled is None:
            os.environ.pop("SCHEDULED_MODE", None)
        else:
            os.environ["SCHEDULED_MODE"] = old_scheduled
        permission.PROJECT_PERMISSIONS_PATH = old_project_rules
        tools.WORKDIR = old_tools_workdir
        config.WORKDIR = old_workdir


def run_live_prompt(name: str, prompt: str, files: dict[str, str] | None = None) -> LiveRunResult:
    import loop

    seen_tools: list[str] = []
    seen_outputs: list[str] = []
    original_execute = loop.execute_tool_call

    def capture_tool(tool_call: dict, handlers: dict, args: dict | None = None):
        seen_tools.append(str(tool_call["function"]["name"]))
        output = original_execute(tool_call, handlers, args)
        seen_outputs.append(str(output))
        return output

    messages = [{"role": "user", "content": prompt}]
    loop.execute_tool_call = capture_tool
    try:
        with live_workspace(name, files or {}), quiet_memory():
            loop.agent_loop(messages)
    finally:
        loop.execute_tool_call = original_execute
    final = ""
    if messages and messages[-1].get("role") == "assistant":
        final = str(messages[-1].get("content", ""))
    return LiveRunResult(name=name, prompt=prompt, tool_names=seen_tools, final=final, tool_outputs=seen_outputs)
