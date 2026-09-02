from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from deeptutor.api.routers import auth, unified_ws


class _Turns:
    def __init__(self) -> None:
        self.cancelled: list[tuple[str, str]] = []

    async def cancel_turn(self, turn_id: str, *, command_id: str) -> bool:
        self.cancelled.append((turn_id, command_id))
        return True

    async def check_active_turn(self, _session_id: str) -> dict[str, str]:
        return {"turn_id": "turn-1", "status": "recovering", "owner_id": "worker-b"}


class _CompletingTurns(_Turns):
    async def start_turn(self, _payload):
        return {"id": "session-1"}, {"id": "turn-1"}

    async def subscribe_turn(self, _turn_id: str, *, after_seq: int = 0):
        assert after_seq == 0
        yield {
            "type": "content",
            "turn_id": "turn-1",
            "session_id": "session-1",
            "seq": 1,
            "timestamp": 1.0,
            "content": "finished answer",
            "metadata": {},
        }
        yield {
            "type": "done",
            "turn_id": "turn-1",
            "session_id": "session-1",
            "seq": 2,
            "timestamp": 2.0,
            "content": "",
            "metadata": {"status": "completed"},
        }


class _DoneFailingWebSocket:
    def __init__(self, turns: _CompletingTurns) -> None:
        self.app = SimpleNamespace(
            state=SimpleNamespace(application_container=SimpleNamespace(turns=turns))
        )
        self.closed = asyncio.Event()
        self.close_calls = 0
        self.sent_types: list[str] = []
        self._received_start = False

    async def accept(self) -> None:
        return None

    async def receive_text(self) -> str:
        if not self._received_start:
            self._received_start = True
            return json.dumps(
                {
                    "type": "start_turn",
                    "content": "hello",
                    "protocol_version": "2.0",
                }
            )
        await self.closed.wait()
        raise unified_ws.WebSocketDisconnect

    async def send_text(self, payload: str) -> None:
        event_type = str(json.loads(payload).get("type") or "")
        self.sent_types.append(event_type)
        if event_type == "done":
            raise RuntimeError("terminal frame send failed")

    async def close(self) -> None:
        self.close_calls += 1
        self.closed.set()


@pytest.fixture
def protocol_client(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, _Turns]:
    turns = _Turns()

    async def allow(_ws):
        return None

    monkeypatch.setattr(auth, "ws_require_auth", allow)
    app = FastAPI()
    app.state.application_container = SimpleNamespace(turns=turns)
    app.include_router(unified_ws.router)
    return TestClient(app), turns


def test_ws_rejects_missing_and_future_protocol_versions(protocol_client) -> None:
    client, _turns = protocol_client
    with client.websocket_connect("/ws") as socket:
        socket.send_json({"type": "ping"})
        missing = socket.receive_json()
        socket.send_json({"type": "ping", "protocol_version": "3.0"})
        future = socket.receive_json()

    for frame in (missing, future):
        assert frame == {
            "type": "protocol_error",
            "error_code": "unsupported_protocol_version",
            "message": "Unsupported or missing protocol_version; expected 2.0.",
            "retryable": False,
            "session_id": "",
            "turn_id": "",
            "protocol_version": "2.0",
        }


def test_ws_versions_heartbeats_active_state_and_command_ack(protocol_client) -> None:
    client, turns = protocol_client
    with client.websocket_connect("/ws") as socket:
        socket.send_json({"type": "ping", "protocol_version": "2.0"})
        assert socket.receive_json() == {"type": "pong", "protocol_version": "2.0"}

        socket.send_json(
            {
                "type": "check_active_turn",
                "session_id": "session-1",
                "protocol_version": "2.0",
            }
        )
        assert socket.receive_json() == {
            "type": "active_turn_info",
            "turn_id": "turn-1",
            "status": "recovering",
            "owner_id": "worker-b",
            "protocol_version": "2.0",
        }

        socket.send_json(
            {
                "type": "cancel_turn",
                "turn_id": "turn-1",
                "command_id": "cancel-1",
                "protocol_version": "2.0",
            }
        )
        assert socket.receive_json() == {
            "type": "command_ack",
            "command_id": "cancel-1",
            "command_type": "cancel_turn",
            "accepted": True,
            "turn_id": "turn-1",
            "error_code": "",
            "message": "",
            "protocol_version": "2.0",
        }

    assert turns.cancelled == [("turn-1", "cancel-1")]


def test_ws_requires_command_ids_for_retryable_mutations(protocol_client) -> None:
    client, turns = protocol_client
    with client.websocket_connect("/ws") as socket:
        socket.send_json(
            {
                "type": "cancel_turn",
                "turn_id": "turn-1",
                "protocol_version": "2.0",
            }
        )
        frame = socket.receive_json()

    assert frame["type"] == "protocol_error"
    assert frame["error_code"] == "invalid_command"
    assert frame["protocol_version"] == "2.0"
    assert turns.cancelled == []


@pytest.mark.asyncio
async def test_ws_send_failure_closes_socket_so_client_can_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    turns = _CompletingTurns()
    socket = _DoneFailingWebSocket(turns)

    async def allow(_ws):
        return None

    monkeypatch.setattr(auth, "ws_require_auth", allow)

    await asyncio.wait_for(unified_ws.unified_websocket(socket), timeout=1)

    assert socket.sent_types == ["content", "done"]
    assert socket.close_calls == 1
