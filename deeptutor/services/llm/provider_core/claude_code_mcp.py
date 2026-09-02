"""Ephemeral MCP tool catalog used by the Claude Code primary provider.

The server deliberately does not execute DeepTutor tools.  It gives Claude
Code the same schemas that the host agent loop received, then the provider
stops the CLI after Claude emits a tool call.  DeepTutor's normal dispatcher
executes that call, preserving its retrieval, memory, and knowledge-base
context instead of creating a second tool runtime inside the CLI process.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from mcp import types
from mcp.server.lowlevel.server import Server
from mcp.server.stdio import stdio_server

_SERVER_NAME = "deeptutor"


def _load_tools(path: Path) -> list[types.Tool]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("Claude Code MCP tool catalog must be a JSON array")

    tools: list[types.Tool] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        input_schema = item.get("inputSchema")
        if not isinstance(input_schema, dict):
            input_schema = {"type": "object", "properties": {}}
        description = item.get("description")
        tools.append(
            types.Tool(
                name=name,
                description=str(description) if description else None,
                inputSchema=input_schema,
            )
        )
    return tools


async def _run(path: Path) -> None:
    tool_list = _load_tools(path)
    tool_names = {tool.name for tool in tool_list}
    server = Server(
        _SERVER_NAME,
        version="1.0.0",
        instructions=(
            "These tools are advertised for DeepTutor's host agent loop. "
            "The host, not this MCP process, executes them."
        ),
    )

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return tool_list

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        if name not in tool_names:
            return types.CallToolResult(
                content=[
                    types.TextContent(
                        type="text",
                        text=f"Unknown DeepTutor tool: {name}",
                    )
                ],
                isError=True,
            )
        return types.CallToolResult(
            content=[
                types.TextContent(
                    type="text",
                    text=(
                        "DeepTutor received this tool call. The host agent loop "
                        "will execute it and provide the result on the next turn."
                    ),
                )
            ]
        )

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
            stateless=True,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="DeepTutor's Claude Code MCP tool catalog")
    parser.add_argument("tool_catalog", type=Path)
    args = parser.parse_args()
    asyncio.run(_run(args.tool_catalog))


if __name__ == "__main__":
    main()


__all__ = ["main"]
