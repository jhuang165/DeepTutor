from __future__ import annotations

from collections.abc import Collection
import sqlite3
from types import SimpleNamespace
from typing import Any

from pydantic import ValidationError
import pytest

from deeptutor.core.context import UnifiedContext
from deeptutor.learning.coordinator import models as coordinator_models
from deeptutor.learning.coordinator.models import ActivityPlan, LearningDecision
from deeptutor.learning.coordinator.service import LearningCoordinator
from deeptutor.learning.models import (
    EvidenceOutcome,
    KnowledgePoint,
    KnowledgeType,
    LearningModule,
    LearningThread,
)
from deeptutor.learning.queue import LearningQueueReason, LearningQueueService
from deeptutor.learning.service import LearningService
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
    knowledge_type: KnowledgeType = KnowledgeType.CONCEPT,
    recipe_id: str = "concept-transfer",
    recipe_version: int = 1,
    assessment_method: str = "teach_back",
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
            knowledge_type=knowledge_type,
            recipe_id=recipe_id,
            recipe_version=recipe_version,
            recipe_step=recipe_step,
            help_level=help_level,
            assessment_method=assessment_method,
            independent_required=independent_required,
            transfer_required=transfer_required,
        ),
        reason="concept",
        confidence=0.9,
        requires_approval=scope == "path",
    )


def _valid_result(**updates: Any):
    payload = {
        "artifact_ref": "",
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


def _audit_event_types(store: LearningStore) -> list[str]:
    with sqlite3.connect(store.db_path) as conn:
        return [row[0] for row in conn.execute("SELECT event_type FROM learning_audit_events")]


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
    assert _audit_event_types(store) == [
        "thread.created",
        "evidence.appended",
        "thread.next_activity",
    ]


@pytest.mark.asyncio
async def test_path_replay_reconciles_mastery_without_duplicate_next_activity_audit(
    store: LearningStore, learning_service: RecordingLearningService
) -> None:
    LearningService(store).replace_modules_for_path(
        "path-1",
        [
            LearningModule(
                id="module-1",
                name="Linear algebra",
                order=0,
                knowledge_points=[
                    KnowledgePoint(
                        id="objective-1",
                        name="Eigenvectors",
                        type=KnowledgeType.CONCEPT,
                        module_id="module-1",
                    )
                ],
            )
        ],
    )
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
    decision = _decision(thread_id="path-thread")
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

    assert first == second
    assert learning_service.recalculations == [
        ("path-1", "objective-1"),
        ("path-1", "objective-1"),
    ]
    assert _audit_event_types(store) == [
        "thread.created",
        "evidence.appended",
        "thread.next_activity",
    ]


@pytest.mark.asyncio
async def test_replay_repairs_recalculation_failure_after_evidence_append(
    store: LearningStore,
) -> None:
    LearningService(store).replace_modules_for_path(
        "path-1",
        [
            LearningModule(
                id="module-1",
                name="Linear algebra",
                order=0,
                knowledge_points=[
                    KnowledgePoint(
                        id="objective-1",
                        name="Eigenvectors",
                        type=KnowledgeType.CONCEPT,
                        module_id="module-1",
                    )
                ],
            )
        ],
    )
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

    class FailOnceLearningService:
        def __init__(self) -> None:
            self.calls = 0

        def recalculate_evidence_mastery(self, path_id: str, objective_id: str) -> bool:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("injected post-append failure")
            return True

    learning_service = FailOnceLearningService()
    coordinator = LearningCoordinator(store=store, learning_service=learning_service)
    decision = _decision(thread_id="path-thread")

    with pytest.raises(RuntimeError, match="injected post-append failure"):
        await coordinator.finish(
            decision,
            _valid_result(),
            session_id="s",
            turn_id="t",
            learner_response="It keeps its direction.",
            allowed_source_refs=set(),
        )

    assert len(store.list_evidence(thread_id="path-thread")) == 1
    assert store.get_learning_thread("path-thread").next_activity == {}

    repaired = await coordinator.finish(
        decision,
        _valid_result(),
        session_id="s",
        turn_id="t",
        learner_response="It keeps its direction.",
        allowed_source_refs=set(),
    )

    assert repaired is not None
    assert learning_service.calls == 2
    assert store.get_learning_thread("path-thread").next_activity["recipe_step"] == 3
    assert _audit_event_types(store) == [
        "thread.created",
        "evidence.appended",
        "thread.next_activity",
    ]


@pytest.mark.asyncio
async def test_replay_repairs_next_activity_failure_after_evidence_append(
    coordinator: LearningCoordinator,
    store: LearningStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_update = store.set_learning_thread_next_activity
    calls = 0

    def fail_once(thread_id: str, next_activity: dict[str, Any]):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("injected next-activity failure")
        return real_update(thread_id, next_activity)

    monkeypatch.setattr(store, "set_learning_thread_next_activity", fail_once)
    decision = _decision()

    with pytest.raises(RuntimeError, match="injected next-activity failure"):
        await coordinator.finish(
            decision,
            _valid_result(),
            session_id="s",
            turn_id="t",
            learner_response="It keeps its direction.",
            allowed_source_refs=set(),
        )

    assert len(store.list_evidence()) == 1
    assert store.get_learning_thread("6cd8958a660475d4bbece77f455215cd").next_activity == {}

    repaired = await coordinator.finish(
        decision,
        _valid_result(),
        session_id="s",
        turn_id="t",
        learner_response="It keeps its direction.",
        allowed_source_refs=set(),
    )

    assert repaired is not None
    assert calls == 2
    assert (
        store.get_learning_thread("6cd8958a660475d4bbece77f455215cd").next_activity["recipe_step"]
        == 3
    )
    assert _audit_event_types(store) == [
        "thread.created",
        "evidence.appended",
        "thread.next_activity",
    ]


@pytest.mark.asyncio
async def test_successful_final_short_lesson_completes_once_on_replay(
    coordinator: LearningCoordinator, store: LearningStore
) -> None:
    decision = _decision(
        kind="guided_attempt",
        recipe_step=3,
        transfer_required=True,
        assessment_method="transfer_application",
    )

    first = await coordinator.finish(
        decision,
        _valid_result(),
        session_id="s",
        turn_id="final-turn",
        learner_response="It keeps its direction.",
        allowed_source_refs=set(),
    )
    second = await coordinator.finish(
        decision,
        _valid_result(),
        session_id="s",
        turn_id="final-turn",
        learner_response="It keeps its direction.",
        allowed_source_refs=set(),
    )

    thread = store.get_learning_thread("6cd8958a660475d4bbece77f455215cd")
    assert first == second
    assert thread is not None and thread.status == "completed"
    assert thread.next_activity == {}
    assert _audit_event_types(store) == [
        "thread.created",
        "evidence.appended",
        "thread.completed",
    ]


@pytest.mark.asyncio
async def test_path_final_step_completes_thread_but_leaves_path_continuation(
    store: LearningStore,
) -> None:
    service = LearningService(store)
    service.replace_modules_for_path(
        "path-1",
        [
            LearningModule(
                id="module-1",
                name="Linear algebra",
                order=0,
                knowledge_points=[
                    KnowledgePoint(
                        id="objective-1",
                        name="Eigenvectors",
                        type=KnowledgeType.CONCEPT,
                        module_id="module-1",
                    ),
                    KnowledgePoint(
                        id="objective-2",
                        name="Eigenspaces",
                        type=KnowledgeType.CONCEPT,
                        module_id="module-1",
                    ),
                ],
            )
        ],
    )
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
    coordinator = LearningCoordinator(store=store, learning_service=service)

    await coordinator.finish(
        _decision(
            thread_id="path-thread",
            kind="guided_attempt",
            recipe_step=3,
            transfer_required=True,
            assessment_method="transfer_application",
        ),
        _valid_result(),
        session_id="s",
        turn_id="final-path-turn",
        learner_response="It keeps its direction.",
        allowed_source_refs=set(),
    )

    thread = store.get_learning_thread("path-thread")
    items = LearningQueueService(store=store, learning_service=service).list_items()
    assert thread is not None and thread.status == "completed"
    assert [(item.path_id, item.reason) for item in items] == [
        ("path-1", LearningQueueReason.CONTINUE_PATH)
    ]


@pytest.mark.asyncio
async def test_final_path_step_stays_active_until_evidence_gate_passes(
    store: LearningStore,
) -> None:
    service = LearningService(store)
    service.replace_modules_for_path(
        "path-1",
        [
            LearningModule(
                id="module-1",
                name="Facts",
                order=0,
                knowledge_points=[
                    KnowledgePoint(
                        id="objective-1",
                        name="Remember the fact",
                        type=KnowledgeType.MEMORY,
                        module_id="module-1",
                    )
                ],
            )
        ],
    )
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
    coordinator = LearningCoordinator(store=store, learning_service=service)

    await coordinator.finish(
        _decision(
            thread_id="path-thread",
            kind="review",
            recipe_step=2,
            knowledge_type=KnowledgeType.MEMORY,
            recipe_id="memory-retrieval",
            assessment_method="delayed_retrieval",
        ),
        _valid_result(),
        session_id="s",
        turn_id="final-memory-turn",
        learner_response="It keeps its direction.",
        allowed_source_refs=set(),
    )

    thread = store.get_learning_thread("path-thread")
    progress = store.load("path-1")
    assert thread is not None and thread.status == "active"
    assert thread.next_activity["recipe_step"] == 2
    assert progress is not None
    assert progress.evidence_mastery == {"objective-1": False}


@pytest.mark.asyncio
async def test_replay_cannot_finalize_evidence_with_a_different_recipe(
    coordinator: LearningCoordinator, store: LearningStore
) -> None:
    await coordinator.finish(
        _decision(
            kind="guided_attempt",
            recipe_step=3,
            transfer_required=True,
            assessment_method="teach_back",
        ),
        _valid_result(),
        session_id="s",
        turn_id="same-turn",
        learner_response="It keeps its direction.",
        allowed_source_refs=set(),
    )

    with pytest.raises(ValueError, match="persisted evidence recipe"):
        await coordinator.finish(
            _decision(
                kind="guided_attempt",
                recipe_step=3,
                knowledge_type=KnowledgeType.PROCEDURE,
                recipe_id="procedure-fading",
                transfer_required=True,
                assessment_method="transfer_variation",
            ),
            _valid_result(),
            session_id="s",
            turn_id="same-turn",
            learner_response="It keeps its direction.",
            allowed_source_refs=set(),
        )

    thread = store.get_learning_thread("6cd8958a660475d4bbece77f455215cd")
    assert thread is not None and thread.status == "active"


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
async def test_long_regenerated_response_keeps_original_user_artifact_reference(
    coordinator: LearningCoordinator,
) -> None:
    record = await coordinator.finish(
        _decision(),
        coordinator_models.CapabilityLearningResult(),
        session_id="s",
        turn_id="regenerated-turn",
        learner_response="x" * 8_001,
        learner_response_ref="chat-message:42:user",
        allowed_source_refs=set(),
    )

    assert record is not None
    assert record.response_ref == "chat-message:42:user"
    assert len(record.response) == 8_000


@pytest.mark.asyncio
async def test_finish_drops_unverified_source_ids(
    coordinator: LearningCoordinator, caplog: pytest.LogCaptureFixture
) -> None:
    private_rejected_source = "private learner text disguised as a source id"
    record = await coordinator.finish(
        _decision(),
        _valid_result(source_refs=["attached-1", private_rejected_source]),
        session_id="s",
        turn_id="t",
        learner_response="It keeps its direction. PRIVATE LEARNER TEXT",
        allowed_source_refs={"attached-1"},
    )

    assert record is not None
    assert record.source_refs == ["attached-1"]
    assert "dropped_count=1" in caplog.text
    assert private_rejected_source not in caplog.text
    assert "PRIVATE LEARNER TEXT" not in caplog.text


@pytest.mark.asyncio
async def test_finish_keeps_only_executor_verified_artifact_refs(
    coordinator: LearningCoordinator, caplog: pytest.LogCaptureFixture
) -> None:
    rejected_ref = "private learner text disguised as an artifact id"

    accepted = await coordinator.finish(
        _decision(),
        _valid_result(artifact_ref="attachment-1"),
        session_id="s",
        turn_id="accepted-turn",
        learner_response="It keeps its direction.",
        allowed_source_refs=set(),
        allowed_artifact_refs={"attachment-1"},
    )
    rejected = await coordinator.finish(
        _decision(),
        _valid_result(artifact_ref=rejected_ref),
        session_id="s",
        turn_id="rejected-turn",
        learner_response="It keeps its direction. PRIVATE LEARNER TEXT",
        allowed_source_refs=set(),
        allowed_artifact_refs={"attachment-1"},
    )

    assert accepted is not None and accepted.artifact_ref == "attachment-1"
    assert rejected is not None and rejected.artifact_ref == ""
    assert "Dropped unverified learning artifact ref" in caplog.text
    assert rejected_ref not in caplog.text
    assert "PRIVATE LEARNER TEXT" not in caplog.text


@pytest.mark.asyncio
async def test_unverified_artifact_cannot_satisfy_a_design_mastery_gate(
    store: LearningStore,
) -> None:
    service = LearningService(store)
    service.replace_modules_for_path(
        "path-1",
        [
            LearningModule(
                id="module-1",
                name="Design",
                order=0,
                knowledge_points=[
                    KnowledgePoint(
                        id="objective-1",
                        name="Defend a design",
                        type=KnowledgeType.DESIGN,
                        module_id="module-1",
                    )
                ],
            )
        ],
    )
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
    coordinator = LearningCoordinator(store=store, learning_service=service)

    project = await coordinator.finish(
        _decision(
            thread_id="path-thread",
            kind="project_step",
            recipe_step=1,
            independent_required=False,
        ),
        _valid_result(artifact_ref="invented-artifact"),
        session_id="s",
        turn_id="project-turn",
        learner_response="It keeps its direction.",
        allowed_source_refs=set(),
        allowed_artifact_refs={"persisted-artifact"},
    )
    critique = await coordinator.finish(
        _decision(
            thread_id="path-thread",
            kind="evidence_comparison",
            recipe_step=2,
        ),
        _valid_result(artifact_ref=""),
        session_id="s",
        turn_id="critique-turn",
        learner_response="It keeps its direction.",
        allowed_source_refs=set(),
        allowed_artifact_refs={"persisted-artifact"},
    )

    progress = store.load("path-1")
    assert project is not None and project.artifact_ref == ""
    assert critique is not None and critique.independent is True
    assert progress is not None
    assert progress.evidence_mastery == {"objective-1": False}


@pytest.mark.asyncio
async def test_finish_derives_labels_and_next_activity_server_side(
    store: LearningStore, learning_service: RecordingLearningService
) -> None:
    LearningService(store).replace_modules_for_path(
        "path-1",
        [
            LearningModule(
                id="module-1",
                name="Linear algebra",
                order=0,
                knowledge_points=[
                    KnowledgePoint(
                        id="objective-1",
                        name="Eigenvectors",
                        type=KnowledgeType.CONCEPT,
                        module_id="module-1",
                    )
                ],
            )
        ],
    )
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

    monkeypatch.setattr(store, "append_evidence_if_absent", fail_append)

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


async def _assert_thread_rejected_before_mutation(
    store: LearningStore,
    learning_service: RecordingLearningService,
    thread: LearningThread,
    *,
    error: str,
) -> None:
    store.create_learning_thread(thread)
    coordinator = LearningCoordinator(store=store, learning_service=learning_service)

    with pytest.raises(ValueError, match=error):
        await coordinator.finish(
            _decision(thread_id=thread.thread_id),
            _valid_result(),
            session_id="s",
            turn_id="t",
            learner_response="It keeps its direction.",
            allowed_source_refs=set(),
        )

    assert store.list_evidence(thread_id=thread.thread_id) == []
    assert store.get_learning_thread(thread.thread_id).next_activity == {}
    assert learning_service.recalculations == []


@pytest.mark.asyncio
async def test_finish_rejects_thread_owned_by_another_session_before_mutation(
    store: LearningStore, learning_service: RecordingLearningService
) -> None:
    await _assert_thread_rejected_before_mutation(
        store,
        learning_service,
        LearningThread(
            thread_id="thread-1",
            session_id="other-session",
            scope="lesson",
            goal="Understand eigenvectors",
            status="active",
        ),
        error="session",
    )


@pytest.mark.asyncio
async def test_finish_rejects_thread_for_another_goal_before_mutation(
    store: LearningStore, learning_service: RecordingLearningService
) -> None:
    await _assert_thread_rejected_before_mutation(
        store,
        learning_service,
        LearningThread(
            thread_id="thread-1",
            session_id="s",
            scope="lesson",
            goal="Understand determinants",
            status="active",
        ),
        error="goal",
    )


@pytest.mark.asyncio
async def test_finish_rejects_non_active_thread_before_mutation(
    store: LearningStore, learning_service: RecordingLearningService
) -> None:
    await _assert_thread_rejected_before_mutation(
        store,
        learning_service,
        LearningThread(
            thread_id="thread-1",
            session_id="s",
            scope="lesson",
            goal="Understand eigenvectors",
            status="completed",
        ),
        error="active",
    )


@pytest.mark.asyncio
async def test_finish_rejects_non_lesson_thread_before_mutation(
    store: LearningStore, learning_service: RecordingLearningService
) -> None:
    await _assert_thread_rejected_before_mutation(
        store,
        learning_service,
        LearningThread(
            thread_id="thread-1",
            session_id="s",
            scope="path",
            goal="Understand eigenvectors",
            status="active",
        ),
        error="lesson",
    )


@pytest.mark.asyncio
async def test_finish_rejects_objective_not_bound_to_thread_path_before_append(
    store: LearningStore,
) -> None:
    LearningService(store).replace_modules_for_path(
        "path-1",
        [
            LearningModule(
                id="module-1",
                name="Linear algebra",
                order=0,
                knowledge_points=[
                    KnowledgePoint(
                        id="objective-1",
                        name="Eigenvectors",
                        type=KnowledgeType.CONCEPT,
                        module_id="module-1",
                    )
                ],
            )
        ],
    )
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
    coordinator = LearningCoordinator(store=store)

    with pytest.raises(ValueError, match="objective"):
        await coordinator.finish(
            _decision(thread_id="path-thread", objective_id="missing-objective"),
            _valid_result(),
            session_id="s",
            turn_id="t",
            learner_response="It keeps its direction.",
            allowed_source_refs=set(),
        )

    assert store.list_evidence(thread_id="path-thread") == []
    assert store.get_learning_thread("path-thread").next_activity == {}


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


def test_trusted_artifact_refs_come_only_from_persisted_turn_attachments() -> None:
    trusted_artifact_refs_from_turn = getattr(turn_executor, "trusted_artifact_refs_from_turn")

    assert trusted_artifact_refs_from_turn(
        [{"id": "attachment-1"}, {"id": ""}],
        [
            {
                "id": "generated-1",
                "url": "/files/outputs/turn/chart.png",
                "generated": True,
            },
            {"id": "not-generated", "url": "/files/attachments/untrusted"},
        ],
    ) == {
        "attachment-1",
        "generated-1",
        "/files/outputs/turn/chart.png",
    }


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
            allowed_artifact_refs: Collection[str],
            learner_response_ref: str = "",
        ) -> None:
            captured.update(
                learner_response=learner_response,
                allowed_source_refs=set(allowed_source_refs),
                allowed_artifact_refs=set(allowed_artifact_refs),
                decision=decision,
                result=result,
                session_id=session_id,
                turn_id=turn_id,
                learner_response_ref=learner_response_ref,
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
        generated_attachments=[
            {
                "id": "generated-1",
                "url": "/files/outputs/turn/chart.png",
                "generated": True,
            }
        ],
        done_metadata=done_metadata,
    )

    assert captured["learner_response"] == "raw answer"
    assert captured["allowed_source_refs"] == {
        "manifest-1",
        "attachment-1",
        "citation-1",
    }
    assert captured["allowed_artifact_refs"] == {
        "attachment-1",
        "generated-1",
        "/files/outputs/turn/chart.png",
    }
    assert done_metadata == {
        "status": "completed",
        "assistant_message_id": 7,
        "learning_evidence_status": "failed",
    }
