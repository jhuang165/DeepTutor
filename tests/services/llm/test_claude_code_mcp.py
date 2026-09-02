from __future__ import annotations

import json

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import pytest


@pytest.mark.asyncio
async def test_claude_code_mcp_advertises_catalog_without_executing_tools(tmp_path) -> None:
    catalog_path = tmp_path / "tools.json"
    catalog_path.write_text(
        json.dumps(
            [
                {
                    "name": "search_knowledge",
                    "description": "Search indexed notes.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                }
            ]
        ),
        encoding="utf-8",
    )

    server = StdioServerParameters(
        command=".venv/bin/python",
        args=[
            "-m",
            "deeptutor.services.llm.provider_core.claude_code_mcp",
            str(catalog_path),
        ],
    )
    async with stdio_client(server) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.list_tools()
            assert [tool.name for tool in result.tools] == ["search_knowledge"]
            assert result.tools[0].inputSchema["required"] == ["query"]

            call_result = await session.call_tool(
                "search_knowledge",
                {"query": "calculus"},
            )
            assert call_result.isError is not True
            assert "host agent loop" in call_result.content[0].text
