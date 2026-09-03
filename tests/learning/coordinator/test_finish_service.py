from __future__ import annotations

from collections.abc import Collection
from types import SimpleNamespace
from typing import Any

from pydantic import ValidationError
import pytest

from deeptutor.core.context import UnifiedContext
from deeptutor.learning.coordinator import models as coordinator_models
from deeptutor.learning.coordinator.models import ActivityPlan, LearningDecision
from deeptutor.learning.coordinator.service import LearningCoordinator
from deeptutor.learning.models import EvidenceOutcome, LearningThread
from deeptutor.learning.storage import LearningStore
from deeptutor.services.session.turns import executor as turn_executor


class RecordingLearningService:
    def __init__(self) -> None:
        self.recalculations: list[tuple[str, str]] = []

    def recalculate_evidence_mastery(self, path_id: str, objective_id: str) -> bool:
        self.recalculations.append((path_id, objective_id))
        return True


def _decision(
    *,
    scope: str = "lesson",
    thread_id: str = "",
    objective_id: str = "objective-1",
    kind: str = "teach_back",
    recipe_step: int = 2,
    help_level: int = 0,
    independent_required: bool = True,
    transfer_required: bool = False,
) -> LearningDecision:
    return LearningDecision(
        scope=scope,
        route="chat",
        goal="Understand eigenvectors",
        thread_id=thread_id,
        objective_id=objective_id,
        activity=ActivityPlan(
            kind=kind,
            objective="Understand eigenvectors",
            learner_action="Explain the concept in your own words.",
            recipe_id="concept-transfer",
            recipe_version=1,
            recipe_step=recipe_step,
            help_level=help_level,
            assessment_method="teach_back",
            independent_required=independent_required,
            transfer_required=transfer_required,
        ),
        reason="concept",
        confidence=0.9,
        requires_approval=scope == "path",
    )


def _valid_result(**updates: Any):
    payload = {
        "artifact_ref": "artifact://lesson",
        "assessment": {
            "outcome": "correct",
            "rubric": [{"id": "mechanism", "passed": True}],
            "cited_evidence": ["keeps its direction"],
            "uncertainty": 0.1,
        },
        "source_refs": [],
        **updates,
    }
    return coordinator_models.CapabilityLearningResult.model_validate(payload)


@pytest.fixture
def store(tmp_path) -> LearningStore:
    return LearningStore(root=tmp_path)


@pytest.fixture
def learning_service() -> RecordingLearningService:
    return RecordingLearningService()


@pytest.fixture
def coordinator(
    store: LearningStore, learning_service: RecordingLearningService
) -> LearningCoordinator:
    return LearningCoordinator(store=store, learning_service=learning_service)


@pytest.mark.parametrize(
    "untrusted_field",
    ["learner_response", "mastery", "independent", "transfer", "next_activity"],
)
def test_capability_result_rejects_server_owned_fields(untrusted_field: str) -> None:
    result_type = getattr(coordinator_models, "CapabilityLearningResult")

    with pytest.raises(ValidationError):
        result_type.model_validate({untrusted_field: "model supplied"})


@pytest.mark.asyncio
async def test_finish_does_not_record_answer_scope(
    coordinator: LearningCoordinator, store: LearningStore
) -> None:
    assert (
        await coordinator.finish(
            _decision(scope="answer"),
            _valid_result(),
            session_id="s",
            turn_id="t",
            learner_response="What is 7 times 8?",
            allowed_source_refs=set(),
        )
        is None
    )
    assert store.list_learning_threads() == []


@pytest.mark.asyncio
async def test_finish_does_not_record_draft_path(
    coordinator: LearningCoordinator, store: LearningStore
) -> None:
    assert (
        await coordinator.finish(
            _decision(scope="path"),
            _valid_result(),
            session_id="s",
            turn_id="t",
            learner_response="Build a course for me",
            allowed_source_refs=set(),
        )
        is None
    )
    assert store.list_learning_threads() == []


@pytest.mark.asyncio
async def test_invalid_assessment_records_unassessed(
    coordinator: LearningCoordinator,
    learning_service: RecordingLearningService,
) -> None:
    record = await coordinator.finish(
        _decision(),
        coordinator_models.CapabilityLearningResult(
            assessment={"outcome": "correct", "cited_evidence": ["not present"]},
        ),
        session_id="s",
        turn_id="t",
        learner_response="My explanation",
        allowed_source_refs=set(),
    )

    assert record is not None
    assert record.outcome is EvidenceOutcome.UNASSESSED
    assert learning_service.recalculations == []


@pytest.mark.asyncio
async def test_finish_is_idempotent_by_turn_and_objective(
    coordinator: LearningCoordinator, store: LearningStore
) -> None:
    decision = _decision()
    result = _valid_result()

    first = await coordinator.finish(
        decision,
        result,
        session_id="s",
        turn_id="t",
        learner_response="It keeps its direction.",
        allowed_source_refs=set(),
    )
    second = await coordinator.finish(
        decision,
        result,
        session_id="s",
        turn_id="t",
        learner_response="It keeps its direction.",
        allowed_source_refs=set(),
    )

    assert first is not None and second is not None
    assert first.evidence_id == second.evidence_id == "c1286eb1505726eb0145385ce3125510"
    assert len(store.list_evidence(thread_id=first.thread_id)) == 1


@pytest.mark.asyncio
async def test_long_response_keeps_durable_turn_reference(
    coordinator: LearningCoordinator,
) -> None:
    record = await coordinator.finish(
        _decision(),
        coordinator_models.CapabilityLearningResult(),
        session_id="s",
        turn_id="t",
        learner_response="x" * 8_001,
        allowed_source_refs=set(),
    )

    assert record is not None
    assert record.response_ref == "chat-turn:t:user"
    assert len(record.response) == 8_000


@pytest.mark.asyncio
async def test_finish_drops_unverified_source_ids(
    coordinator: LearningCoordinator, caplog: pytest.LogCaptureFixture
) -> None:
    record = await coordinator.finish(
        _decision(),
        _valid_result(source_refs=["attached-1", "invented-9"]),
        session_id="s",
        turn_id="t",
        learner_response="It keeps its direction. PRIVATE LEARNER TEXT",
        allowed_source_refs={"attached-1"},
    )

    assert record is not None
    assert record.source_refs == ["attached-1"]
    assert "invented-9" in caplog.text
    assert "PRIVATE LEARNER TEXT" not in caplog.text


@pytest.mark.asyncio
async def test_finish_derives_labels_and_next_activity_server_side(
    store: LearningStore, learning_service: RecordingLearningService
) -> None:
    store.create_learning_thread(
        LearningThread(
            thread_id="path-thread",
            session_id="s",
            scope="lesson",
            goal="Understand eigenvectors",
            status="active",
            path_id="path-1",
        )
    )
    coordinator = LearningCoordinator(store=store, learning_service=learning_service)
    decision = _decision(
        thread_id="path-thread",
        kind="guided_attempt",
        recipe_step=3,
        help_level=2,
        transfer_required=True,
    )

    record = await coordinator.finish(
        decision,
        _valid_result(),
        session_id="s",
        turn_id="t",
        learner_response="It keeps its direction.",
        allowed_source_refs=set(),
    )

    assert record is not None
    assert record.path_id == "path-1"
    assert record.independent is True
    assert record.transfer is True
    assert learning_service.recalculations == [("path-1", "objective-1")]
    assert store.get_learning_thread("path-thread").next_activity["kind"] == "guided_attempt"


@pytest.mark.asyncio
async def test_finish_saves_next_activity_only_after_evidence_append(
    store: LearningStore,
    learning_service: RecordingLearningService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordinator = LearningCoordinator(store=store, learning_service=learning_service)

    def fail_append(_record):
        raise RuntimeError("append failed")

    monkeypatch.setattr(store, "append_evidence", fail_append)

    with pytest.raises(RuntimeError, match="append failed"):
        await coordinator.finish(
            _decision(),
            _valid_result(),
            session_id="s",
            turn_id="t",
            learner_response="It keeps its direction.",
            allowed_source_refs=set(),
        )

    thread = store.get_learning_thread("6cd8958a660475d4bbece77f455215cd")
    assert thread is not None
    assert thread.next_activity == {}
    assert learning_service.recalculations == []


def test_trusted_source_ids_come_only_from_resolved_turn_state() -> None:
    trusted_source_ids_from_turn = getattr(turn_executor, "trusted_source_ids_from_turn")

    assert trusted_source_ids_from_turn(
        {"manifest-1": "text"},
        [{"id": "attachment-1"}, {"id": ""}],
        {
            "citations": [
                {"citation_id": "citation-1"},
                {"source_id": "citation-source-1"},
            ],
            "assessment": {"source_refs": ["invented-from-assessment"]},
        },
    ) == {
        "manifest-1",
        "attachment-1",
        "citation-1",
        "citation-source-1",
    }
    assert (
        trusted_source_ids_from_turn(
            {},
            [],
            {"citations": {"url": "https://example.test", "title": "No stable ID"}},
        )
        == set()
    )


@pytest.mark.asyncio
async def test_executor_finalization_uses_raw_message_and_preserves_done_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class FakeCoordinator:
        async def finish(
            self,
            decision: LearningDecision,
            result: Any,
            *,
            session_id: str,
            turn_id: str,
            learner_response: str,
            allowed_source_refs: Collection[str],
        ) -> None:
            captured.update(
                learner_response=learner_response,
                allowed_source_refs=set(allowed_source_refs),
                decision=decision,
                result=result,
                session_id=session_id,
                turn_id=turn_id,
            )
            raise RuntimeError("finalization failed")

    monkeypatch.setattr("deeptutor.learning.coordinator.LearningCoordinator", FakeCoordinator)
    context = UnifiedContext(
        session_id="s",
        user_message="[Workspace Context]\nprivate source text\n\n[User Question]\nraw answer",
        extension_state={
            "learning_coordinator": {
                "decision": _decision().model_dump(mode="json"),
                "result": _valid_result(
                    source_refs=["manifest-1", "invented-from-assessment"]
                ).model_dump(mode="json"),
            }
        },
    )
    context.capability_output.event_metadata = {
        "citations": [{"id": "citation-1"}],
        "assessment": {"source_refs": ["invented-from-assessment"]},
    }
    done_metadata = {"status": "completed", "assistant_message_id": 7}

    await turn_executor._finalize_learning_evidence(
        context,
        session_id="s",
        turn_id="t",
        raw_user_content="raw answer",
        source_index={"manifest-1": "private source text"},
        attachment_records=[{"id": "attachment-1"}],
        done_metadata=done_metadata,
    )

    assert captured["learner_response"] == "raw answer"
    assert captured["allowed_source_refs"] == {
        "manifest-1",
        "attachment-1",
        "citation-1",
    }
    assert done_metadata == {
        "status": "completed",
        "assistant_message_id": 7,
        "learning_evidence_status": "failed",
    }
