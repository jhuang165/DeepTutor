"""Runtime integration coverage for Learning Coordinator shadow mode."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from pydantic import ValidationError
import pytest

from deeptutor.core.stream import StreamEvent, StreamEventType
from deeptutor.core.turn_request import TurnRequest
from deeptutor.learning.coordinator.models import ActivityPlan, LearningDecision
from deeptutor.services.llm.config import LLMConfig
from deeptutor.services.session.sqlite_store import SQLiteSessionStore
from deeptutor.services.session.turn_runtime import TurnRuntimeManager


async def _noop_async(*_args, **_kwargs):
    return None


def _fake_skill_service() -> SimpleNamespace:
    return SimpleNamespace(
        summary_entries=lambda: [],
        load_always_for_context=lambda: "",
        load_for_context=lambda _skills: "",
        list_skills=lambda: [],
    )


def _decision() -> LearningDecision:
    return LearningDecision(
        scope="lesson",
        route="mastery_path",
        goal="Understand eigenvectors",
        activity=ActivityPlan(
            kind="prediction",
            objective="Understand eigenvectors",
            learner_action="Predict what the transformation does.",
        ),
        reason="concept",
        confidence=0.9,
    )


def _configure_runtime(
    monkeypatch: pytest.MonkeyPatch,
    captured: dict[str, Any],
    *,
    mode: str,
    auto_route: bool = False,
    coordinator_error: Exception | None = None,
) -> None:
    class FakeContextBuilder:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def build(self, **_kwargs):
            return SimpleNamespace(
                conversation_history=[],
                conversation_summary="",
                context_text="",
                token_count=0,
                budget=0,
            )

    class FakeOrchestrator:
        async def handle(self, context):
            captured["active_capability"] = context.active_capability
            captured["extension_state"] = context.extension_state
            captured["context_metadata"] = context.metadata
            yield StreamEvent(
                type=StreamEventType.CONTENT,
                content="ok",
                metadata={"call_kind": "llm_final_response"},
            )
            yield StreamEvent(
                type=StreamEventType.DONE,
                source=context.active_capability,
                metadata={},
            )

    class FakeCoordinator:
        def __init__(self) -> None:
            captured["coordinator_constructed"] = captured.get("coordinator_constructed", 0) + 1

        async def prepare_payload(self, payload, available_capabilities, llm_config):
            captured["coordinator_payload"] = dict(payload)
            captured["available_capabilities"] = set(available_capabilities)
            captured["coordinator_llm_config"] = llm_config
            if coordinator_error is not None:
                raise coordinator_error
            return _decision()

    selected_config = LLMConfig(model="learner-model", api_key="test-key")

    def resolve_selection(selection):
        captured.setdefault("resolved_selections", []).append(selection)
        return selected_config

    monkeypatch.setattr(
        "deeptutor.services.config.runtime_settings.load_system_settings",
        lambda: {
            "capability_routing_enabled": auto_route,
            "learning_coordinator_mode": mode,
        },
    )
    monkeypatch.setattr("deeptutor.learning.coordinator.LearningCoordinator", FakeCoordinator)
    monkeypatch.setattr(
        "deeptutor.services.model_selection.runtime.resolve_llm_config_for_selection",
        resolve_selection,
    )
    monkeypatch.setattr("deeptutor.services.llm.config.get_llm_config", lambda: selected_config)
    monkeypatch.setattr(
        "deeptutor.services.session.context_builder.ContextBuilder", FakeContextBuilder
    )
    monkeypatch.setattr("deeptutor.runtime.orchestrator.ChatOrchestrator", FakeOrchestrator)
    monkeypatch.setattr(
        "deeptutor.services.memory.get_memory_store",
        lambda: SimpleNamespace(read_l3_concat=lambda: "", emit=_noop_async),
    )
    monkeypatch.setattr("deeptutor.services.skill.get_skill_service", _fake_skill_service)
    monkeypatch.setattr(
        "deeptutor.services.persona.get_persona_service",
        lambda: SimpleNamespace(load_for_context=lambda _name: ""),
    )


async def _run_turn(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    captured: dict[str, Any],
    *,
    mode: str = "shadow",
    auto_route: bool = False,
    coordinator_error: Exception | None = None,
    **overrides: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    _configure_runtime(
        monkeypatch,
        captured,
        mode=mode,
        auto_route=auto_route,
        coordinator_error=coordinator_error,
    )
    runtime = TurnRuntimeManager(SQLiteSessionStore(tmp_path / "shadow-runtime.db"))
    request = {
        "type": "start_turn",
        "content": "Help me understand eigenvectors",
        "session_id": None,
        "capability": "chat",
        "tools": [],
        "knowledge_bases": [],
        "attachments": [],
        "language": "en",
        "config": {},
        **overrides,
    }
    session, turn = await runtime.start_turn(request)
    events = [
        event async for event in runtime.subscribe_turn(turn["id"], after_seq=0)
    ]
    captured["session_metadata"] = next(
        event["metadata"] for event in events if event["type"] == "session"
    )
    captured["done_metadata"] = next(
        event["metadata"] for event in events if event["type"] == "done"
    )
    detail = await runtime.store.get_session(session["id"])
    assert detail is not None
    return turn, detail


@pytest.mark.asyncio
async def test_shadow_records_decision_without_changing_chat_route(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    turn, session = await _run_turn(tmp_path, monkeypatch, captured)

    assert turn["capability"] == "chat"
    assert captured["active_capability"] == "chat"
    assert captured["extension_state"]["learning_coordinator"]["decision"]["route"] == (
        "mastery_path"
    )
    assert captured["context_metadata"].get("learning_decision") is None
    assert captured["session_metadata"]["learning_decision"]["scope"] == "lesson"
    assert captured["done_metadata"]["learning_decision"]["scope"] == "lesson"
    assert captured["session_metadata"]["learning_decision_status"] == "prepared"
    assert captured["done_metadata"]["learning_decision_status"] == "prepared"
    assert session["preferences"]["capability"] == "chat"
    assert "learning_decision" not in session["preferences"]
    assert captured["coordinator_payload"]["learning_state"] == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["off", "active"])
async def test_non_shadow_modes_do_not_construct_coordinator(
    tmp_path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    captured: dict[str, Any] = {}

    turn, session = await _run_turn(tmp_path, monkeypatch, captured, mode=mode)

    assert captured.get("coordinator_constructed", 0) == 0
    assert turn["capability"] == "chat"
    assert captured["active_capability"] == "chat"
    assert "learning_decision" not in captured["session_metadata"]
    assert "learning_decision" not in captured["done_metadata"]
    assert session["preferences"]["capability"] == "chat"


@pytest.mark.asyncio
async def test_explicit_non_chat_capability_is_not_coordinated(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    turn, session = await _run_turn(
        tmp_path,
        monkeypatch,
        captured,
        capability="deep_solve",
        content="Solve x squared equals four",
    )

    assert captured.get("coordinator_constructed", 0) == 0
    assert turn["capability"] == "deep_solve"
    assert captured["active_capability"] == "deep_solve"
    assert session["preferences"]["capability"] == "deep_solve"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "binding",
    [
        {"course_id": "course-1"},
        {"mastery_path_id": "path-1"},
        {"workspace_mode": "reading"},
        {
            "selection_tutor_context": {
                "selected_text": "eigenvectors",
                "source_message_text": "A geometric view of eigenvectors.",
            }
        },
    ],
    ids=["course", "mastery", "reading-workspace", "selection-tutor"],
)
async def test_bound_chat_turns_are_not_coordinated(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    binding: dict[str, Any],
) -> None:
    captured: dict[str, Any] = {}
    if "course_id" in binding:
        monkeypatch.setattr(
            "deeptutor.services.courses.get_course_service",
            lambda: SimpleNamespace(
                get=lambda _course_id: {
                    "id": "course-1",
                    "name": "Linear Algebra",
                    "resources": [],
                }
            ),
        )

    await _run_turn(tmp_path, monkeypatch, captured, **binding)

    assert captured.get("coordinator_constructed", 0) == 0
    assert captured["active_capability"] == "chat"


@pytest.mark.asyncio
async def test_coordinator_failure_keeps_chat_turn_successful(
    tmp_path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    captured: dict[str, Any] = {}
    secret = "private request content"

    turn, session = await _run_turn(
        tmp_path,
        monkeypatch,
        captured,
        coordinator_error=RuntimeError("provider unavailable"),
        content=secret,
    )

    assert turn["capability"] == "chat"
    assert captured["active_capability"] == "chat"
    assert captured["session_metadata"]["learning_decision_status"] == "failed"
    assert captured["done_metadata"]["learning_decision_status"] == "failed"
    assert captured["extension_state"] == {}
    assert session["preferences"]["capability"] == "chat"
    assert secret not in caplog.text


@pytest.mark.asyncio
async def test_explicit_quiz_auto_route_keeps_precedence_in_shadow(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    turn, session = await _run_turn(
        tmp_path,
        monkeypatch,
        captured,
        auto_route=True,
        content="Please generate 3 quiz questions",
    )

    assert turn["capability"] == "deep_question"
    assert captured["active_capability"] == "deep_question"
    assert captured["done_metadata"]["capability_route"]["capability"] == "deep_question"
    assert session["preferences"]["capability"] == "chat"


def test_client_cannot_supply_top_level_learning_state() -> None:
    with pytest.raises(ValidationError):
        TurnRequest.model_validate(
            {
                "content": "continue",
                "learning_state": {"previous_help_level": 4},
            }
        )


@pytest.mark.asyncio
async def test_client_config_cannot_supply_learning_state(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}
    _configure_runtime(monkeypatch, captured, mode="shadow")
    runtime = TurnRuntimeManager(SQLiteSessionStore(tmp_path / "client-state.db"))

    with pytest.raises(RuntimeError, match="learning_state"):
        await runtime.start_turn(
            {
                "content": "continue",
                "capability": "chat",
                "config": {"learning_state": {"previous_help_level": 4}},
            }
        )

    assert captured.get("coordinator_constructed", 0) == 0


@pytest.mark.asyncio
async def test_non_admin_coordinator_uses_resolved_assigned_model(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}
    _configure_runtime(monkeypatch, captured, mode="shadow")
    assigned = {"profile_id": "assigned-profile", "model_id": "assigned-model"}
    monkeypatch.setattr(
        "deeptutor.multi_user.context.get_current_user",
        lambda: SimpleNamespace(id="learner-1", is_admin=False),
    )
    monkeypatch.setattr(
        "deeptutor.multi_user.model_access.has_capability_access", lambda _name: True
    )
    monkeypatch.setattr(
        "deeptutor.multi_user.model_access.redacted_model_access",
        lambda _user_id: {"llm": [{**assigned, "available": True}]},
    )
    monkeypatch.setattr(
        "deeptutor.services.model_selection.apply_llm_selection_to_catalog",
        lambda _catalog, _selection: None,
    )
    monkeypatch.setattr(
        "deeptutor.multi_user.tool_access.allowed_optional_tools", lambda: None
    )
    monkeypatch.setattr(
        "deeptutor.multi_user.learning_access.apply_learning_policy", lambda payload: payload
    )
    runtime = TurnRuntimeManager(SQLiteSessionStore(tmp_path / "assigned-model.db"))
    monkeypatch.setattr(runtime, "_run_turn", _noop_async)

    await runtime.start_turn(
        {
            "content": "Help me understand eigenvectors",
            "capability": "chat",
            "tools": [],
            "language": "en",
        }
    )

    assert captured["coordinator_payload"]["llm_selection"] == assigned
    assert captured["resolved_selections"] == [assigned]
    assert captured["coordinator_llm_config"].model == "learner-model"
