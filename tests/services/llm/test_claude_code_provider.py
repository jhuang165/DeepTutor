from __future__ import annotations

import json
from pathlib import Path

import pytest

from deeptutor.services.llm.provider_core import claude_code_provider as module
from deeptutor.services.llm.provider_core.claude_code_provider import ClaudeCodeProvider


def _line(event: dict) -> tuple[str, str]:
    return "stdout", json.dumps(event)


@pytest.mark.asyncio
async def test_claude_code_provider_streams_text_and_uses_cli_flags(monkeypatch) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr(module.shutil, "which", lambda command: "/usr/local/bin/claude")

    async def fake_stream(command, *, cwd=None, env=None):
        captured["command"] = list(command)
        captured["cwd"] = cwd
        captured["env"] = env
        yield _line(
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_delta",
                    "delta": {"type": "text_delta", "text": "Hello"},
                },
            }
        )
        yield _line(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "Hello"}]},
            }
        )
        yield _line(
            {
                "type": "result",
                "subtype": "success",
                "result": "Hello",
                "usage": {"input_tokens": 4, "output_tokens": 1},
            }
        )
        yield "exit", "0"

    monkeypatch.setattr(module, "stream_process_lines", fake_stream)
    deltas: list[str] = []

    async def on_content_delta(value: str) -> None:
        deltas.append(value)

    response = await ClaudeCodeProvider(default_model="sonnet").chat_stream(
        messages=[
            {"role": "system", "content": "Be concise."},
            {"role": "user", "content": "Say hello."},
        ],
        model="claude_code/sonnet",
        reasoning_effort="high",
        on_content_delta=on_content_delta,
    )

    command = captured["command"]
    assert isinstance(command, list)
    assert response.content == "Hello"
    assert response.finish_reason == "stop"
    assert response.usage == {"input_tokens": 4, "output_tokens": 1, "total_tokens": 5}
    assert deltas == ["Hello"]
    assert command[command.index("--model") + 1] == "sonnet"
    assert command[command.index("--effort") + 1] == "high"
    assert command[command.index("--tools") + 1] == ""
    assert "--restricted" in command
    assert "--no-session-persistence" in command
    assert captured["env"] is None


@pytest.mark.asyncio
async def test_claude_code_provider_isolates_login_with_config_dir(monkeypatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(module.shutil, "which", lambda command: "/usr/local/bin/claude")

    async def fake_stream(command, *, cwd=None, env=None):
        captured["env"] = env
        yield _line({"type": "result", "subtype": "success", "result": "ok"})
        yield "exit", "0"

    monkeypatch.setattr(module, "stream_process_lines", fake_stream)

    response = await ClaudeCodeProvider(
        default_model="sonnet", config_dir="~/.claude-work"
    ).chat(messages=[{"role": "user", "content": "hello"}])

    assert response.content == "ok"
    assert captured["env"] == {"CLAUDE_CONFIG_DIR": "~/.claude-work"}


@pytest.mark.asyncio
async def test_claude_code_provider_exposes_deeptutor_mcp_tool_call(monkeypatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(module.shutil, "which", lambda command: "/usr/local/bin/claude")

    async def fake_stream(command, *, cwd=None, env=None):
        captured["command"] = list(command)
        mcp_path = Path(command[command.index("--mcp-config") + 1])
        mcp_config = json.loads(mcp_path.read_text(encoding="utf-8"))
        server_config = mcp_config["mcpServers"]["deeptutor"]
        catalog_path = Path(server_config["args"][-1])
        captured["catalog"] = json.loads(catalog_path.read_text(encoding="utf-8"))
        yield _line(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": "I will search."},
                        {
                            "type": "tool_use",
                            "id": "toolu_123",
                            "name": "mcp__deeptutor__search_knowledge",
                            "input": {"query": "calculus"},
                        },
                    ]
                },
            }
        )
        # The provider must stop consuming the CLI after the structured tool
        # request, so this result is intentionally never observed by it.
        yield _line({"type": "result", "subtype": "success", "result": "wrong"})

    monkeypatch.setattr(module, "stream_process_lines", fake_stream)

    response = await ClaudeCodeProvider().chat(
        messages=[{"role": "user", "content": "Find this in my notes."}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "search_knowledge",
                    "description": "Search indexed notes.",
                    "parameters": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                },
            }
        ],
    )

    assert response.content == "I will search."
    assert response.finish_reason == "tool_calls"
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].id == "toolu_123"
    assert response.tool_calls[0].name == "search_knowledge"
    assert response.tool_calls[0].arguments == {"query": "calculus"}
    assert captured["catalog"] == [
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
    command = captured["command"]
    assert isinstance(command, list)
    assert "mcp__deeptutor__search_knowledge" in command


@pytest.mark.asyncio
async def test_claude_code_provider_reports_missing_cli_without_spawning(monkeypatch) -> None:
    monkeypatch.setattr(module.shutil, "which", lambda command: None)

    response = await ClaudeCodeProvider().chat(
        messages=[{"role": "user", "content": "hello"}],
    )

    assert response.finish_reason == "error"
    assert response.content is not None
    assert "claude" in response.content
