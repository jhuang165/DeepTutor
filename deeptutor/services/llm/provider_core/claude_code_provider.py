"""Claude Code CLI provider backed by the user's local Claude subscription.

Claude Code does not expose an OpenAI-compatible inference endpoint.  This
adapter uses its supported headless ``-p``/``stream-json`` interface and an
ephemeral MCP server to advertise DeepTutor's tools.  Tool execution remains
in DeepTutor's agent loop: the adapter returns the first Claude tool request
and terminates that CLI turn before the MCP placeholder can cause a second
agent loop to run inside Claude Code.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import json
import logging
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any

from deeptutor.services.subagent.process import stream_process_lines

from .base import LLMProvider, LLMResponse, ToolCallRequest

logger = logging.getLogger(__name__)

DEFAULT_CLAUDE_CODE_MODEL = "sonnet"
CLAUDE_CODE_CLI = "claude"
_MCP_TOOL_PREFIX = "mcp__deeptutor__"
_SUPPORTED_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max"})


def _text_content(content: Any) -> str:
    """Render an OpenAI-style content value into a safe transcript fragment."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                item_type = str(item.get("type") or "")
                if item_type in {"text", "input_text", "output_text"}:
                    text = item.get("text")
                    if text is not None:
                        parts.append(str(text))
                elif item_type in {"image_url", "input_image", "image"}:
                    parts.append("[image attachment omitted from the Claude Code transcript]")
                else:
                    parts.append(json.dumps(item, ensure_ascii=False, default=str))
            else:
                parts.append(str(item))
        return "\n".join(part for part in parts if part)
    if isinstance(content, dict):
        return json.dumps(content, ensure_ascii=False, default=str)
    return str(content)


def _tool_call_fields(tool_call: Any) -> tuple[str, Any]:
    if isinstance(tool_call, dict):
        function = tool_call.get("function")
        if isinstance(function, dict):
            return str(function.get("name") or ""), function.get("arguments")
        return str(tool_call.get("name") or ""), tool_call.get("arguments")
    function = getattr(tool_call, "function", None)
    if function is not None:
        return str(getattr(function, "name", "") or ""), getattr(function, "arguments", None)
    return str(getattr(tool_call, "name", "") or ""), getattr(tool_call, "arguments", None)


def _arguments_text(arguments: Any) -> str:
    if isinstance(arguments, str):
        return arguments
    if arguments is None:
        return "{}"
    return json.dumps(arguments, ensure_ascii=False, default=str)


def _render_messages(messages: list[dict[str, Any]]) -> str:
    """Preserve prior tool rounds in the single prompt accepted by ``claude -p``."""
    sections = [
        "Conversation transcript from DeepTutor. The last user entry is the current request. "
        "Use prior assistant messages and tool results as context, then answer the current request.",
    ]
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "message").strip().lower()
        if role == "system":
            continue
        body = _text_content(message.get("content"))
        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list):
            rendered_calls: list[str] = []
            for tool_call in tool_calls:
                name, arguments = _tool_call_fields(tool_call)
                if name:
                    rendered_calls.append(f"Tool call: {name}({_arguments_text(arguments)})")
            if rendered_calls:
                body = "\n".join(part for part in [body, *rendered_calls] if part)
        if role == "tool":
            tool_call_id = str(message.get("tool_call_id") or "")
            heading = f"tool result{f' ({tool_call_id})' if tool_call_id else ''}"
        else:
            heading = role
        sections.append(f"[{heading}]\n{body or '(empty)'}")
    return "\n\n".join(sections)


def _system_prompt(messages: list[dict[str, Any]]) -> str:
    system_parts = [
        _text_content(message.get("content"))
        for message in messages
        if isinstance(message, dict) and str(message.get("role") or "").lower() == "system"
    ]
    system_parts = [part for part in system_parts if part.strip()]
    system_parts.append(
        "You are the primary language model inside DeepTutor. Return the answer in the format "
        "requested by the DeepTutor instructions. Use only the DeepTutor MCP tools listed in "
        "this session when a tool is needed; do not attempt to use unavailable built-in tools. "
        "The DeepTutor host will execute any tool call and send its result in a later turn."
    )
    return "\n\n".join(system_parts)


def _normalise_tool_definitions(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    definitions: list[dict[str, Any]] = []
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function")
        source = function if isinstance(function, dict) else tool
        name = str(source.get("name") or "").strip()
        if not name:
            continue
        parameters = source.get("parameters")
        if not isinstance(parameters, dict):
            parameters = {"type": "object", "properties": {}}
        description = source.get("description")
        definitions.append(
            {
                "name": name,
                "description": str(description) if description else "",
                "inputSchema": parameters,
            }
        )
    return definitions


def _parse_arguments(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _usage(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    usage: dict[str, int] = {}
    for key, raw in value.items():
        if isinstance(raw, bool):
            continue
        try:
            number = int(raw)
        except (TypeError, ValueError):
            continue
        usage[str(key)] = number
    if "total_tokens" not in usage:
        input_tokens = usage.get("input_tokens", usage.get("prompt_tokens", 0))
        output_tokens = usage.get("output_tokens", usage.get("completion_tokens", 0))
        if input_tokens or output_tokens:
            usage["total_tokens"] = input_tokens + output_tokens
    return usage


def _event_message_text(event: dict[str, Any]) -> tuple[str, str, list[dict[str, Any]]]:
    message = event.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        return "", "", []
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_blocks: list[dict[str, Any]] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("type") or "")
        if block_type == "text":
            text_parts.append(str(block.get("text") or ""))
        elif block_type == "thinking":
            reasoning_parts.append(str(block.get("thinking") or block.get("text") or ""))
        elif block_type == "tool_use":
            tool_blocks.append(block)
    return "".join(text_parts), "".join(reasoning_parts), tool_blocks


class ClaudeCodeProvider(LLMProvider):
    """Run Claude Code in print mode while retaining DeepTutor's host loop."""

    cli_command = CLAUDE_CODE_CLI

    def __init__(
        self,
        api_key: str | None = None,  # noqa: ARG002 - auth belongs to Claude Code
        api_base: str | None = None,  # noqa: ARG002 - transport is the local CLI
        default_model: str = DEFAULT_CLAUDE_CODE_MODEL,
    ):
        super().__init__(api_key=None, api_base=None)
        self.default_model = default_model or DEFAULT_CLAUDE_CODE_MODEL

    def get_default_model(self) -> str:
        return self.default_model or DEFAULT_CLAUDE_CODE_MODEL

    def _model_name(self, model: str | None) -> str:
        value = (model or self.get_default_model()).strip()
        if value.lower().startswith("claude_code/") or value.lower().startswith("claude-code/"):
            value = value.split("/", 1)[1]
        return value or DEFAULT_CLAUDE_CODE_MODEL

    def _build_command(
        self,
        prompt: str,
        *,
        system_prompt: str,
        model: str | None,
        reasoning_effort: str | None,
        mcp_config: Path | None,
        allowed_tools: list[str],
    ) -> list[str]:
        command = [
            self.cli_command,
            "-p",
            prompt,
            "--output-format",
            "stream-json",
            "--verbose",
            "--include-partial-messages",
            "--no-session-persistence",
            "--system-prompt",
            system_prompt,
            # Keep the subprocess from inheriting Claude Code's built-in shell,
            # file, and web tools. DeepTutor owns those capabilities.
            "--tools",
            "",
            "--restricted",
            "--permission-mode",
            "dontAsk",
        ]
        if mcp_config is not None:
            command.extend(["--mcp-config", str(mcp_config), "--strict-mcp-config"])
        if allowed_tools:
            command.extend(["--allowed-tools", *allowed_tools])
        effort = (reasoning_effort or "").strip().lower()
        if effort in _SUPPORTED_EFFORTS:
            command.extend(["--effort", effort])
        model_name = self._model_name(model)
        if model_name:
            command.extend(["--model", model_name])
        return command

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        return await self._run(
            messages=messages,
            tools=tools if tool_choice != "none" else None,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            reasoning_effort=reasoning_effort,
            on_content_delta=None,
            on_reasoning_delta=None,
            cwd=kwargs.pop("cwd", None),
        )

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        on_content_delta: Callable[[str], Awaitable[None]] | None = None,
        on_reasoning_delta: Callable[[str], Awaitable[None]] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        return await self._run(
            messages=messages,
            tools=tools if tool_choice != "none" else None,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            reasoning_effort=reasoning_effort,
            on_content_delta=on_content_delta,
            on_reasoning_delta=on_reasoning_delta,
            cwd=kwargs.pop("cwd", None),
        )

    async def _run(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model: str | None,
        max_tokens: int,  # noqa: ARG002 - Claude Code controls its own output budget
        temperature: float,  # noqa: ARG002 - not a Claude Code CLI setting
        reasoning_effort: str | None,
        on_content_delta: Callable[[str], Awaitable[None]] | None,
        on_reasoning_delta: Callable[[str], Awaitable[None]] | None,
        cwd: str | None,
    ) -> LLMResponse:
        if shutil.which(self.cli_command) is None:
            return LLMResponse(
                content=(
                    "Error calling Claude Code: the `claude` CLI was not found on PATH. "
                    "Install Claude Code and sign in with `claude` before selecting this provider."
                ),
                finish_reason="error",
            )

        definitions = _normalise_tool_definitions(tools)
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        assistant_text_parts: list[str] = []
        assistant_reasoning_parts: list[str] = []
        tool_calls: list[ToolCallRequest] = []
        seen_tool_ids: set[str] = set()
        stderr_lines: list[str] = []
        result_text = ""
        result_usage: dict[str, int] = {}
        error_text = ""
        exit_code: str | None = None
        stopped_for_tool = False
        partial_content_seen = False
        partial_reasoning_seen = False

        with tempfile.TemporaryDirectory(prefix="deeptutor-claude-") as temp_dir:
            temp_root = Path(temp_dir)
            mcp_config: Path | None = None
            allowed_tools: list[str] = []
            if definitions:
                tool_catalog = temp_root / "tools.json"
                tool_catalog.write_text(
                    json.dumps(definitions, ensure_ascii=False),
                    encoding="utf-8",
                )
                mcp_config = temp_root / "mcp.json"
                mcp_config.write_text(
                    json.dumps(
                        {
                            "mcpServers": {
                                "deeptutor": {
                                    "command": sys.executable,
                                    "args": [
                                        "-m",
                                        "deeptutor.services.llm.provider_core.claude_code_mcp",
                                        str(tool_catalog),
                                    ],
                                }
                            }
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                allowed_tools = [_MCP_TOOL_PREFIX + item["name"] for item in definitions]

            command = self._build_command(
                _render_messages(messages),
                system_prompt=_system_prompt(messages),
                model=model,
                reasoning_effort=reasoning_effort,
                mcp_config=mcp_config,
                allowed_tools=allowed_tools,
            )

            try:
                async for channel, line in stream_process_lines(command, cwd=cwd):
                    if channel == "stderr":
                        if line.strip():
                            stderr_lines.append(line.strip())
                            del stderr_lines[:-8]
                        continue
                    if channel == "exit":
                        exit_code = line
                        continue
                    try:
                        event = json.loads(line)
                    except (TypeError, ValueError):
                        continue
                    if not isinstance(event, dict):
                        continue

                    event_type = str(event.get("type") or "")
                    if event_type == "stream_event":
                        inner = event.get("event")
                        if not isinstance(inner, dict):
                            continue
                        delta = inner.get("delta")
                        if not isinstance(delta, dict):
                            continue
                        delta_type = str(delta.get("type") or "")
                        if delta_type == "text_delta":
                            delta_text = str(delta.get("text") or "")
                            if delta_text:
                                partial_content_seen = True
                                content_parts.append(delta_text)
                                if on_content_delta:
                                    await on_content_delta(delta_text)
                        elif delta_type == "thinking_delta":
                            delta_text = str(delta.get("thinking") or "")
                            if delta_text:
                                partial_reasoning_seen = True
                                reasoning_parts.append(delta_text)
                                if on_reasoning_delta:
                                    await on_reasoning_delta(delta_text)
                        continue

                    if event_type == "assistant":
                        text, reasoning, blocks = _event_message_text(event)
                        if text:
                            assistant_text_parts.append(text)
                            if not partial_content_seen and on_content_delta:
                                await on_content_delta(text)
                        if reasoning:
                            assistant_reasoning_parts.append(reasoning)
                            if not partial_reasoning_seen and on_reasoning_delta:
                                await on_reasoning_delta(reasoning)
                        for block in blocks:
                            name = str(block.get("name") or "")
                            if not name.startswith(_MCP_TOOL_PREFIX):
                                continue
                            call_id = str(block.get("id") or f"call_{len(tool_calls)}")
                            if call_id in seen_tool_ids:
                                continue
                            seen_tool_ids.add(call_id)
                            tool_calls.append(
                                ToolCallRequest(
                                    id=call_id,
                                    name=name[len(_MCP_TOOL_PREFIX) :],
                                    arguments=_parse_arguments(block.get("input")),
                                )
                            )
                        if tool_calls:
                            stopped_for_tool = True
                            break
                        continue

                    if event_type == "result":
                        result_text = str(event.get("result") or "").strip()
                        result_usage = _usage(event.get("usage"))
                        subtype = str(event.get("subtype") or "")
                        if event.get("is_error") is True or subtype not in {"", "success"}:
                            error_text = result_text or subtype or "Claude Code returned an error."
                        continue

                    if event_type == "error":
                        error_text = str(event.get("error") or event.get("message") or "")
            except FileNotFoundError:
                return LLMResponse(
                    content=(
                        "Error calling Claude Code: the `claude` CLI could not be started. "
                        "Install it or make sure it is available on the backend PATH."
                    ),
                    finish_reason="error",
                )
            except Exception as exc:  # pragma: no cover - defensive runtime guard
                logger.warning("Claude Code primary provider failed: %s", exc, exc_info=True)
                error_text = str(exc)

        content = "".join(assistant_text_parts) if assistant_text_parts else "".join(content_parts)
        if not content:
            content = result_text
        reasoning_content = (
            "".join(assistant_reasoning_parts)
            if assistant_reasoning_parts
            else "".join(reasoning_parts)
        ) or None

        if not stopped_for_tool and exit_code not in {None, "0"} and not error_text:
            detail = " ".join(stderr_lines).strip()
            error_text = f"Claude Code exited with code {exit_code}."
            if detail:
                error_text = f"{error_text} {detail[:1000]}"
        if error_text and not content:
            content = f"Error calling Claude Code: {error_text}"
        if not content and not tool_calls:
            content = "Error calling Claude Code: no response was returned."
            error_text = error_text or "no response"

        return LLMResponse(
            content=content or None,
            tool_calls=tool_calls,
            finish_reason=("tool_calls" if tool_calls else "error" if error_text else "stop"),
            usage=result_usage,
            reasoning_content=reasoning_content,
            provider_specific_fields={"transport": "claude_code_cli"},
        )


__all__ = [
    "CLAUDE_CODE_CLI",
    "ClaudeCodeProvider",
    "DEFAULT_CLAUDE_CODE_MODEL",
]
