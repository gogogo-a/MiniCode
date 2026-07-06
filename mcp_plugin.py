"""
MCP 插件模块：连接真实 stdio MCP server，发现工具，并加入 Lead 工具池。

函数职责：
- MCPClient：保存一个 MCP server 的启动参数和已发现工具。
- load_mcp_configs：读取当前工作区 `.mcp/config.json` 中的 mcpServers。
- normalize_mcp_name：把 server/tool 名称规范化为 OpenAI tool 可用的安全名称。
- connect_mcp：按项目配置连接 MCP server，执行 tools/list，保存发现结果。
- assemble_tool_pool：把内置工具和已连接 MCP 工具合并为当前 Lead 工具池。
- call_mcp_tool：通过 stdio JSON-RPC 调用真实 MCP tools/call。
"""

from __future__ import annotations

import anyio
import json
import re
from dataclasses import dataclass, field

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from config import WORKDIR


DISALLOWED_CHARS = re.compile(r"[^a-zA-Z0-9_-]")


@dataclass
class MCPClient:
    name: str
    command: str
    args: list[str]
    tools: list[dict] = field(default_factory=list)
    original_names: dict[str, str] = field(default_factory=dict)


MCP_CLIENTS: dict[str, MCPClient] = {}
MCP_CONFIG_PATH = WORKDIR / ".mcp" / "config.json"


def load_mcp_configs() -> dict:
    if not MCP_CONFIG_PATH.exists():
        return {}
    data = json.loads(MCP_CONFIG_PATH.read_text(encoding="utf-8"))
    servers = data.get("mcpServers", {})
    if not isinstance(servers, dict):
        raise ValueError(".mcp/config.json must contain an object field named mcpServers")
    configs = {}
    for name, config in servers.items():
        if not isinstance(config, dict):
            continue
        command = config.get("command")
        args = config.get("args", [])
        if not command:
            continue
        if not isinstance(args, list):
            raise ValueError(f"mcpServers.{name}.args must be a list")
        configs[name] = {"command": command, "args": [str(item) for item in args]}
    return configs


def normalize_mcp_name(name: str) -> str:
    normalized = DISALLOWED_CHARS.sub("_", name)
    return normalized or "tool"


def _schema_to_parameters(schema: dict | None) -> dict:
    if not schema:
        return {"type": "object", "properties": {}, "additionalProperties": False}
    parameters = dict(schema)
    parameters.setdefault("type", "object")
    parameters.setdefault("properties", {})
    parameters.setdefault("additionalProperties", False)
    return parameters


async def _discover_tools(config: dict) -> list[dict]:
    params = StdioServerParameters(command=config["command"], args=config["args"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
            discovered = []
            for tool in result.tools:
                discovered.append({
                    "name": tool.name,
                    "description": tool.description or "",
                    "parameters": _schema_to_parameters(tool.inputSchema),
                })
            return discovered


async def _call_tool(config: dict, tool_name: str, args: dict) -> str:
    params = StdioServerParameters(command=config["command"], args=config["args"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, args)
            if getattr(result, "structuredContent", None):
                return json.dumps(result.structuredContent, ensure_ascii=False, indent=2)
            parts = []
            for item in result.content:
                text = getattr(item, "text", None)
                if text is not None:
                    parts.append(text)
                else:
                    parts.append(str(item))
            return "\n".join(parts) if parts else str(result)


def connect_mcp(name: str) -> str:
    if name in MCP_CLIENTS:
        tools = ", ".join(tool["prefixed_name"] for tool in MCP_CLIENTS[name].tools)
        return f"MCP server '{name}' already connected. Tools: {tools}"
    try:
        configs = load_mcp_configs()
    except Exception as error:
        return f"MCP config error: {error}"
    config = configs.get(name)
    if not config:
        available = ", ".join(sorted(configs)) or "(none)"
        return f"Unknown MCP server '{name}'. Available: {available}"
    try:
        raw_tools = anyio.run(_discover_tools, config)
    except Exception as error:
        return f"MCP connect error: {error}"
    client = MCPClient(name=name, command=config["command"], args=list(config["args"]))
    safe_server = normalize_mcp_name(name)
    for tool in raw_tools:
        safe_tool = normalize_mcp_name(tool["name"])
        prefixed = f"mcp__{safe_server}__{safe_tool}"
        client.original_names[prefixed] = tool["name"]
        client.tools.append({
            "prefixed_name": prefixed,
            "name": tool["name"],
            "description": tool["description"],
            "parameters": tool["parameters"],
        })
    MCP_CLIENTS[name] = client
    tools = ", ".join(tool["prefixed_name"] for tool in client.tools)
    return f"Connected to '{name}'. Discovered: {tools}"


def call_mcp_tool(server_name: str, tool_name: str, args: dict) -> str:
    client = MCP_CLIENTS.get(server_name)
    if not client:
        return f"MCP error: server '{server_name}' not connected"
    config = {"command": client.command, "args": client.args}
    try:
        return anyio.run(_call_tool, config, tool_name, args)
    except Exception as error:
        return f"MCP call error: {error}"


def assemble_tool_pool(builtin_tools: list, builtin_handlers: dict) -> tuple[list[dict], dict]:
    tools = list(builtin_tools)
    handlers = dict(builtin_handlers)
    for server_name, client in MCP_CLIENTS.items():
        for tool in client.tools:
            prefixed = tool["prefixed_name"]
            original = tool["name"]
            description = f"{tool['description']} (readOnly)"
            tools.append({
                "type": "function",
                "function": {
                    "name": prefixed,
                    "description": description,
                    "parameters": tool["parameters"],
                },
            })
            handlers[prefixed] = (
                lambda _server=server_name, _tool=original, **kwargs:
                    call_mcp_tool(_server, _tool, kwargs)
            )
    return tools, handlers
