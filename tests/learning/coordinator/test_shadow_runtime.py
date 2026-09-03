"""Runtime integration coverage for Learning Coordinator shadow mode."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from pydantic import ValidationError
import pytest

from deeptutor.core.stream import StreamEvent, StreamEventType
from deeptutor.core.turn_request import TurnRequest
from deeptutor.learning.coordinator.models import ActivityPlan, LearningDecision
from deeptutor.learning.models import EvidenceRecord, LearningThread
from deeptutor.learning.storage import LearningStore
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


def _decision(*, route: str = "mastery_path", scope: str = "lesson") -> LearningDecision:
    return LearningDecision(
        scope=scope,
        route=route,
        goal="Understand eigenvectors",
        activity=ActivityPlan(
            kind="prediction",
            objective="Understand eigenvectors",
            learner_action="Predict what the transformation does.",
        ),
        reason="concept",
        confidence=0.9,
        requires_approval=scope == "path",
    )


def _configure_runtime(
    monkeypatch: pytest.MonkeyPatch,
    captured: dict[str, Any],
    *,
    mode: str,
    saved_opt_in: bool = False,
    decision_route: str = "mastery_path",
    decision_scope: str = "lesson",
    auto_route: bool = False,
    coordinator_error: Exception | None = None,
    orchestrator_outcome: str = "done",
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
            learning_store = captured.get("learning_store")
            if learning_store is not None:
                captured["threads_before_orchestration"] = learning_store.list_learning_threads()
            captured["active_capability"] = context.active_capability
            captured["enabled_tools"] = context.enabled_tools
            captured["extension_state"] = context.extension_state
            captured["context_metadata"] = context.metadata
            captured["orchestrator_started"] = True
            if orchestrator_outcome == "exception":
                raise RuntimeError("capability failed")
            if orchestrator_outcome == "cancelled":
                await asyncio.Event().wait()
            yield StreamEvent(
                type=StreamEventType.CONTENT,
                content="ok",
                metadata={"call_kind": "llm_final_response"},
            )
            if orchestrator_outcome == "learning_provenance":
                context.capability_output.event_metadata = {"citations": [{"id": "citation-1"}]}
                yield StreamEvent(
                    type=StreamEventType.SOURCES,
                    metadata={
                        "sources": [
                            {
                                "type": "artifact",
                                "filename": "chart.png",
                                "mime_type": "image/png",
                                "url": "/files/outputs/turn/chart.png",
                            }
                        ]
                    },
                )
            if orchestrator_outcome in {"learning_result", "learning_provenance"}:
                context.extension("learning_coordinator")["result"] = {
                    "artifact_ref": (
                        "/files/outputs/turn/chart.png"
                        if orchestrator_outcome == "learning_provenance"
                        else ""
                    ),
                    "assessment": None,
                    "source_refs": (
                        ["citation-1"] if orchestrator_outcome == "learning_provenance" else []
                    ),
                }
            if orchestrator_outcome == "no_done":
                return
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
            return _decision(route=decision_route, scope=decision_scope)

        async def finish(self, *_args, **kwargs):
            captured["finish_calls"] = captured.get("finish_calls", 0) + 1
            captured.setdefault("finish_kwargs", []).append(dict(kwargs))

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
    monkeypatch.setattr(
        "deeptutor.services.settings.interface_settings.get_learning_coordinator_enabled",
        lambda: saved_opt_in,
        raising=False,
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
    monkeypatch.setattr(
        "deeptutor.services.session.turn_runtime.TurnRuntimeManager._maybe_generate_session_title",
        _noop_async,
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
    saved_opt_in: bool = False,
    decision_route: str = "mastery_path",
    decision_scope: str = "lesson",
    auto_route: bool = False,
    coordinator_error: Exception | None = None,
    orchestrator_outcome: str = "done",
    learning_store: LearningStore | None = None,
    **overrides: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    _configure_runtime(
        monkeypatch,
        captured,
        mode=mode,
        saved_opt_in=saved_opt_in,
        decision_route=decision_route,
        decision_scope=decision_scope,
        auto_route=auto_route,
        coordinator_error=coordinator_error,
        orchestrator_outcome=orchestrator_outcome,
    )
    learning_store = learning_store or LearningStore(root=tmp_path / "learning-store")
    captured["learning_store"] = learning_store
    monkeypatch.setattr(
        "deeptutor.learning.storage.LearningStore",
        lambda *_args, **_kwargs: learning_store,
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
    if orchestrator_outcome == "cancelled":
        while not captured.get("orchestrator_started"):
            await asyncio.sleep(0)
        assert await runtime.cancel_turn(turn["id"]) is True
    events = [event async for event in runtime.subscribe_turn(turn["id"], after_seq=0)]
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
@pytest.mark.parametrize(
    ("orchestrator_outcome", "terminal_status"),
    [
        ("no_done", "completed"),
        ("cancelled", "cancelled"),
        ("exception", "failed"),
    ],
    ids=["no-done", "cancellation", "exception"],
)
async def test_synthesized_done_preserves_learning_metadata(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    orchestrator_outcome: str,
    terminal_status: str,
) -> None:
    captured: dict[str, Any] = {}

    await _run_turn(
        tmp_path,
        monkeypatch,
        captured,
        orchestrator_outcome=orchestrator_outcome,
    )

    assert captured["done_metadata"]["status"] == terminal_status
    assert captured["done_metadata"]["learning_decision"]["scope"] == "lesson"
    assert captured["done_metadata"]["learning_decision_status"] == "prepared"


@pytest.mark.asyncio
@pytest.mark.parametrize("workspace_mode", ["", None], ids=["empty", "null"])
async def test_empty_workspace_binding_keeps_normal_chat_eligible_for_shadow_coordination(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    workspace_mode: str | None,
) -> None:
    captured: dict[str, Any] = {}

    turn, _session = await _run_turn(
        tmp_path,
        monkeypatch,
        captured,
        workspace_mode=workspace_mode,
    )

    assert captured.get("coordinator_constructed", 0) == 1
    assert turn["capability"] == "chat"
    assert captured["active_capability"] == "chat"
    assert captured["done_metadata"]["learning_decision_status"] == "prepared"


@pytest.mark.asyncio
async def test_off_mode_does_not_construct_coordinator(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    turn, session = await _run_turn(tmp_path, monkeypatch, captured, mode="off")

    assert captured.get("coordinator_constructed", 0) == 0
    assert turn["capability"] == "chat"
    assert captured["active_capability"] == "chat"
    assert "learning_decision" not in captured["session_metadata"]
    assert "learning_decision" not in captured["done_metadata"]
    assert session["preferences"]["capability"] == "chat"


@pytest.mark.asyncio
async def test_active_mode_routes_default_chat(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    turn, session = await _run_turn(
        tmp_path,
        monkeypatch,
        captured,
        mode="active",
        saved_opt_in=True,
    )

    assert turn["capability"] == "mastery_path"
    assert captured["active_capability"] == "mastery_path"
    assert captured["coordinator_payload"]["requested_capability"] == "chat"
    assert captured["session_metadata"]["learning_decision"]["route"] == "mastery_path"
    assert session["preferences"]["capability"] == "chat"


@pytest.mark.asyncio
async def test_active_route_refilters_optional_tools_against_selected_manifest(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    await _run_turn(
        tmp_path,
        monkeypatch,
        captured,
        mode="active",
        saved_opt_in=True,
        tools=["web_search", "reason"],
    )

    assert captured["active_capability"] == "mastery_path"
    assert captured["enabled_tools"] == []


@pytest.mark.parametrize(
    "message",
    [
        "Can we try again?",
        "I still don't understand this.",
        "请再讲一次。",
        "我还是不明白。",
    ],
)
def test_repeated_learning_request_phrases_are_deterministic(message: str) -> None:
    from deeptutor.services.session.turns.request_preparer import (
        _is_repeated_learning_request,
    )

    assert _is_repeated_learning_request(message, "A different request") is True


@pytest.mark.asyncio
async def test_active_mode_does_not_override_explicit_capability(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    turn, _session = await _run_turn(
        tmp_path,
        monkeypatch,
        captured,
        mode="active",
        saved_opt_in=True,
        capability="deep_solve",
    )

    assert turn["capability"] == "deep_solve"
    assert captured["active_capability"] == "deep_solve"
    assert captured.get("coordinator_constructed", 0) == 0


@pytest.mark.asyncio
async def test_active_mode_does_not_override_bound_reading_workspace(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    turn, _session = await _run_turn(
        tmp_path,
        monkeypatch,
        captured,
        mode="active",
        saved_opt_in=True,
        capability="immersive_reading",
        workspace_mode="reading",
    )

    assert turn["capability"] == "immersive_reading"
    assert captured["active_capability"] == "immersive_reading"
    assert captured.get("coordinator_constructed", 0) == 0


@pytest.mark.asyncio
async def test_active_deployment_keeps_unopted_user_on_chat(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    turn, _session = await _run_turn(
        tmp_path,
        monkeypatch,
        captured,
        mode="active",
        saved_opt_in=False,
    )

    assert turn["capability"] == "chat"
    assert captured["active_capability"] == "chat"
    assert captured.get("coordinator_constructed", 0) == 0
    assert "learning_decision" not in captured["session_metadata"]


@pytest.mark.asyncio
async def test_per_turn_false_overrides_saved_opt_in(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    turn, _session = await _run_turn(
        tmp_path,
        monkeypatch,
        captured,
        mode="active",
        saved_opt_in=True,
        learning_coordinator=False,
    )

    assert turn["capability"] == "chat"
    assert captured["active_capability"] == "chat"
    assert captured.get("coordinator_constructed", 0) == 0


@pytest.mark.asyncio
async def test_per_turn_true_enables_active_mode_without_saved_opt_in(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    turn, _session = await _run_turn(
        tmp_path,
        monkeypatch,
        captured,
        mode="active",
        saved_opt_in=False,
        learning_coordinator=True,
    )

    assert turn["capability"] == "mastery_path"
    assert captured["active_capability"] == "mastery_path"


@pytest.mark.asyncio
async def test_per_turn_false_disables_shadow_observation(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    turn, _session = await _run_turn(
        tmp_path,
        monkeypatch,
        captured,
        mode="shadow",
        learning_coordinator=False,
    )

    assert turn["capability"] == "chat"
    assert captured.get("coordinator_constructed", 0) == 0


@pytest.mark.asyncio
async def test_active_mode_falls_back_to_chat_when_decision_route_is_unavailable(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    turn, _session = await _run_turn(
        tmp_path,
        monkeypatch,
        captured,
        mode="active",
        saved_opt_in=True,
        decision_route="not_registered",
    )

    assert turn["capability"] == "chat"
    assert captured["active_capability"] == "chat"
    assert captured["session_metadata"]["learning_decision"]["route"] == "not_registered"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scope", "status"),
    [("lesson", "active"), ("path", "draft")],
)
async def test_active_mode_persists_thread_and_activity_before_orchestration(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    scope: str,
    status: str,
) -> None:
    captured: dict[str, Any] = {}

    await _run_turn(
        tmp_path,
        monkeypatch,
        captured,
        mode="active",
        saved_opt_in=True,
        decision_scope=scope,
    )

    threads = captured["threads_before_orchestration"]
    assert len(threads) == 1
    thread = threads[0]
    assert thread.status.value == status
    assert thread.next_activity["kind"] == "prediction"
    assert captured["extension_state"]["learning_coordinator"]["decision"]["thread_id"] == (
        thread.thread_id
    )


@pytest.mark.asyncio
async def test_cancelled_active_turn_keeps_resumable_activity_without_evidence(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    await _run_turn(
        tmp_path,
        monkeypatch,
        captured,
        mode="active",
        saved_opt_in=True,
        orchestrator_outcome="cancelled",
    )

    store = captured["learning_store"]
    thread = store.list_learning_threads()[0]
    assert thread.next_activity["kind"] == "prediction"
    assert store.list_evidence(thread_id=thread.thread_id) == []


@pytest.mark.asyncio
async def test_client_supplied_learning_thread_must_belong_to_current_session(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}
    learning_store = LearningStore(root=tmp_path / "owned-learning-store")
    learning_store.create_learning_thread(
        LearningThread(
            thread_id="thread-other-session",
            session_id="session-other",
            scope="lesson",
            goal="Understand eigenvectors",
            status="active",
        )
    )

    with pytest.raises(RuntimeError, match="session"):
        await _run_turn(
            tmp_path,
            monkeypatch,
            captured,
            mode="active",
            saved_opt_in=True,
            learning_store=learning_store,
            learning_thread_id="thread-other-session",
        )

    assert captured.get("orchestrator_started") is None


@pytest.mark.asyncio
async def test_resumed_thread_supplies_server_owned_learning_state(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}
    learning_store = LearningStore(root=tmp_path / "resume-learning-store")
    next_activity = {
        "kind": "prediction",
        "objective": "Understand eigenvectors",
        "learner_action": "Try again.",
        "help_level": 2,
        "recipe_step": 3,
    }
    learning_store.create_learning_thread(
        LearningThread(
            thread_id="thread-resume",
            session_id="session-resume",
            scope="lesson",
            goal="Understand eigenvectors",
            status="active",
            next_activity=next_activity,
        )
    )
    learning_store.append_evidence(
        EvidenceRecord(
            evidence_id="evidence-prior",
            thread_id="thread-resume",
            activity_kind="prediction",
            recipe_id="concept-transfer",
            recipe_version=1,
            response="I am not sure.",
            outcome="incorrect",
            help_level=1,
            session_id="session-resume",
            turn_id="turn-prior",
        )
    )
    session_store = SQLiteSessionStore(tmp_path / "shadow-runtime.db")
    session = await session_store.create_session(session_id="session-resume")
    await session_store.add_message(
        session["id"],
        role="user",
        content="Help me understand eigenvectors",
        capability="chat",
    )

    _configure_runtime(
        monkeypatch,
        captured,
        mode="active",
        saved_opt_in=True,
    )
    captured["learning_store"] = learning_store
    monkeypatch.setattr(
        "deeptutor.learning.storage.LearningStore",
        lambda *_args, **_kwargs: learning_store,
    )
    runtime = TurnRuntimeManager(session_store)
    await runtime.start_turn(
        {
            "session_id": session["id"],
            "content": "Help me understand eigenvectors",
            "capability": "chat",
            "tools": [],
            "knowledge_bases": [],
            "attachments": [],
            "language": "en",
            "config": {},
            "learning_thread_id": "thread-resume",
        }
    )

    state = captured["coordinator_payload"]["learning_state"]
    assert state == {
        "previous_help_level": 2,
        "last_outcome": "incorrect",
        "server_next_activity": next_activity,
        "repeated_request": True,
    }


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
@pytest.mark.parametrize("mode", ["shadow", "active"])
@pytest.mark.parametrize(
    "binding",
    [
        {"course_id": "course-1"},
        {"mastery_path_id": "path-1"},
        {"workspace_mode": "immersive_reading"},
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
    mode: str,
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

    await _run_turn(
        tmp_path,
        monkeypatch,
        captured,
        mode=mode,
        saved_opt_in=True,
        **binding,
    )

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
        coordinator_error=RuntimeError(secret),
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
async def test_lost_terminal_lease_does_not_finalize_learning_evidence(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}
    _configure_runtime(
        monkeypatch,
        captured,
        mode="shadow",
        orchestrator_outcome="learning_result",
    )
    runtime = TurnRuntimeManager(SQLiteSessionStore(tmp_path / "lost-lease.db"))

    async def lose_terminal_lease(*_args, **_kwargs) -> bool:
        captured["terminal_transition_calls"] = 1
        return False

    monkeypatch.setattr(runtime, "_transition_execution", lose_terminal_lease)
    _session, turn = await runtime.start_turn(
        {
            "content": "Help me understand eigenvectors",
            "capability": "chat",
            "tools": [],
            "knowledge_bases": [],
            "attachments": [],
            "language": "en",
            "config": {},
        }
    )

    events = [event async for event in runtime.subscribe_turn(turn["id"], after_seq=0)]

    assert captured["terminal_transition_calls"] == 1
    assert captured.get("finish_calls", 0) == 0
    assert not any(event["type"] == "done" for event in events)


@pytest.mark.asyncio
async def test_regeneration_finalization_references_original_persisted_user(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}
    _configure_runtime(
        monkeypatch,
        captured,
        mode="shadow",
        orchestrator_outcome="learning_provenance",
    )
    store = SQLiteSessionStore(tmp_path / "regenerated-evidence.db")
    runtime = TurnRuntimeManager(store)
    session = await store.create_session()
    learner_response = "x" * 8_001
    original_user_id = await store.add_message(
        session["id"],
        role="user",
        content=learner_response,
        capability="chat",
    )

    _session, turn = await runtime.start_turn(
        {
            "session_id": session["id"],
            "content": learner_response,
            "capability": "chat",
            "tools": [],
            "knowledge_bases": [],
            "attachments": [],
            "language": "en",
            "config": {},
            "persist_user_message": False,
            "regenerate": True,
            "regenerated_from_message_id": original_user_id,
        }
    )
    events = [event async for event in runtime.subscribe_turn(turn["id"], after_seq=0)]

    assert any(event["type"] == "done" for event in events)
    assert captured["finish_kwargs"][-1]["learner_response"] == learner_response
    assert captured["finish_kwargs"][-1]["learner_response_ref"] == (
        f"chat-message:{original_user_id}:user"
    )
    assert captured["finish_kwargs"][-1]["allowed_source_refs"] == {"citation-1"}
    assert captured["finish_kwargs"][-1]["allowed_artifact_refs"] == {
        "/files/outputs/turn/chart.png"
    }
    messages = await store.get_messages(session["id"])
    assert [message["id"] for message in messages if message["role"] == "user"] == [
        original_user_id
    ]


@pytest.mark.asyncio
async def test_completed_turn_finalization_resolves_streamed_source_and_artifact_provenance(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    await _run_turn(
        tmp_path,
        monkeypatch,
        captured,
        mode="shadow",
        orchestrator_outcome="learning_provenance",
    )

    assert captured["finish_kwargs"][-1]["allowed_source_refs"] == {"citation-1"}
    assert captured["finish_kwargs"][-1]["allowed_artifact_refs"] == {
        "/files/outputs/turn/chart.png"
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["shadow", "active"])
async def test_explicit_quiz_auto_route_keeps_precedence(
    tmp_path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    captured: dict[str, Any] = {}

    turn, session = await _run_turn(
        tmp_path,
        monkeypatch,
        captured,
        mode=mode,
        saved_opt_in=True,
        auto_route=True,
        content="Please generate 3 quiz questions",
    )

    assert turn["capability"] == "deep_question"
    assert captured["active_capability"] == "deep_question"
    assert captured["done_metadata"]["capability_route"]["capability"] == "deep_question"
    assert session["preferences"]["capability"] == "chat"
    assert captured.get("coordinator_constructed", 0) == 0


def test_client_cannot_supply_top_level_learning_state() -> None:
    with pytest.raises(ValidationError):
        TurnRequest.model_validate(
            {
                "content": "continue",
                "learning_state": {"previous_help_level": 4},
            }
        )


def test_turn_request_exposes_learning_coordinator_contract() -> None:
    request = TurnRequest(content="Continue", learning_thread_id="thread-1")
    assert request.to_payload()["learning_thread_id"] == "thread-1"
    assert (
        TurnRequest(content="Learn", learning_coordinator=True).to_payload()["learning_coordinator"]
        is True
    )
    with pytest.raises(ValidationError):
        TurnRequest(content="Continue", learning_thread_id="x" * 129)


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
@pytest.mark.parametrize("mode", ["shadow", "active"])
async def test_non_admin_coordinator_uses_resolved_assigned_model(
    tmp_path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    captured: dict[str, Any] = {}
    _configure_runtime(monkeypatch, captured, mode=mode, saved_opt_in=True)
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
    monkeypatch.setattr("deeptutor.multi_user.tool_access.allowed_optional_tools", lambda: None)
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
