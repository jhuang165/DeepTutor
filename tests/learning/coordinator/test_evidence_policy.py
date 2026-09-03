from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3

import pytest

from deeptutor.learning.evidence import evidence_gate, validate_open_assessment
from deeptutor.learning.models import (
    EvidenceOutcome,
    EvidenceRecord,
    KnowledgePoint,
    KnowledgeType,
    LearningModule,
    LearningThread,
    RepetitionState,
    ReviewTask,
)
from deeptutor.learning.service import LearningService
from deeptutor.learning.storage import LearningStore

NOW = 1_700_000_000.0


def _evidence(
    evidence_id: str,
    *,
    objective_id: str = "objective-1",
    activity_kind: str = "retrieval",
    outcome: EvidenceOutcome = EvidenceOutcome.CORRECT,
    help_level: int = 0,
    independent: bool = True,
    transfer: bool = False,
    artifact_ref: str = "",
    created_at: float = NOW,
    removed_at: float | None = None,
    thread_id: str = "thread-1",
    path_id: str = "path-1",
) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=evidence_id,
        thread_id=thread_id,
        path_id=path_id,
        objective_id=objective_id,
        activity_kind=activity_kind,
        recipe_id="recipe",
        recipe_version=1,
        response="The learner response",
        artifact_ref=artifact_ref,
        outcome=outcome,
        help_level=help_level,
        independent=independent,
        transfer=transfer,
        session_id="session-1",
        turn_id="turn-1",
        created_at=created_at,
        removed_at=removed_at,
    )


def test_assessment_requires_cited_learner_text() -> None:
    assert (
        validate_open_assessment(
            {
                "outcome": "correct",
                "rubric": [{"id": "mechanism", "passed": True}],
                "cited_evidence": ["words the learner never wrote"],
                "uncertainty": 0.1,
            },
            "Eigenvectors keep their direction under the transform.",
        )
        is None
    )


def test_high_uncertainty_is_unassessed() -> None:
    assert (
        validate_open_assessment(
            {
                "outcome": "correct",
                "rubric": [{"id": "mechanism", "passed": True}],
                "cited_evidence": ["keep their direction"],
                "uncertainty": 0.6,
            },
            "They keep their direction.",
        )
        is None
    )


def test_memory_gate_requires_independent_retrievals_on_separate_days() -> None:
    independent_now = _evidence("now")
    independent_delayed = _evidence("delayed", created_at=NOW - 20 * 60 * 60)
    independent_same_day = _evidence("same-day", created_at=NOW - 19 * 60 * 60)

    assert evidence_gate(KnowledgeType.MEMORY, [independent_now, independent_delayed]) is True
    assert evidence_gate(KnowledgeType.MEMORY, [independent_now, independent_same_day]) is False


def test_concept_gate_requires_independent_teach_back_or_transfer() -> None:
    independent_teach_back = _evidence("teach-back", activity_kind="teach_back")
    guided_teach_back = _evidence("guided", activity_kind="teach_back", independent=False)

    assert evidence_gate(KnowledgeType.CONCEPT, [independent_teach_back]) is True
    assert evidence_gate(KnowledgeType.CONCEPT, [guided_teach_back]) is False


def test_procedure_gate_requires_independent_solution_and_transfer() -> None:
    independent_solution = _evidence("solution", activity_kind="solution")
    transfer_variation = _evidence("transfer", activity_kind="solution", transfer=True)

    assert (
        evidence_gate(KnowledgeType.PROCEDURE, [independent_solution, transfer_variation]) is True
    )


def test_design_gate_requires_artifact_and_independent_critique() -> None:
    project_artifact = _evidence(
        "artifact", activity_kind="project", independent=False, artifact_ref="artifact://design"
    )
    independent_critique = _evidence("critique", activity_kind="critique")

    assert evidence_gate(KnowledgeType.DESIGN, [project_artifact, independent_critique]) is True
    assert evidence_gate(KnowledgeType.DESIGN, [project_artifact]) is False


def test_gate_ignores_removed_unassessed_and_guided_evidence() -> None:
    delayed = _evidence("delayed", created_at=NOW - 20 * 60 * 60)
    removed = _evidence("removed", removed_at=NOW)
    unassessed = _evidence("unassessed", outcome=EvidenceOutcome.UNASSESSED)
    guided = _evidence("guided", independent=False)

    assert evidence_gate(KnowledgeType.MEMORY, [delayed, removed, unassessed, guided]) is False


def test_recalculate_evidence_mastery_changes_only_evidence_gate_and_records_event(
    tmp_path,
) -> None:
    store = LearningStore(root=tmp_path)
    service = LearningService(store)
    objective = KnowledgePoint(
        id="objective-1", name="Remember", type=KnowledgeType.MEMORY, module_id="module-1"
    )
    service.replace_modules_for_path(
        "path-1",
        [LearningModule(id="module-1", name="Module", order=0, knowledge_points=[objective])],
    )
    store.create_learning_thread(
        LearningThread(
            thread_id="thread-1",
            session_id="session-1",
            scope="lesson",
            goal="Remember",
            status="active",
            path_id="path-1",
        )
    )
    store.append_evidence(_evidence("retrieval-now"))
    store.append_evidence(_evidence("retrieval-delayed", created_at=NOW - 20 * 60 * 60))

    def preserve_authoritative_state(tx) -> None:
        tx.progress.qualitative_mastery["objective-1"] = True
        tx.progress.repetition_states["objective-1"] = RepetitionState(next_review_at=NOW)
        tx.touch()

    store.mutate("path-1", preserve_authoritative_state)
    assert service.recalculate_evidence_mastery("path-1", "objective-1") is True

    progress = store.load("path-1")
    assert progress is not None
    assert progress.evidence_mastery == {"objective-1": True}
    assert progress.qualitative_mastery == {"objective-1": True}
    assert [task.knowledge_point_id for task in progress.review_queue] == ["objective-1"]
    assert store.list_events("path-1")[-1].event_type == "mastery.evidence_recalculated"


def test_recalculate_ignores_evidence_from_a_thread_bound_to_another_path(tmp_path) -> None:
    store = LearningStore(root=tmp_path)
    service = LearningService(store)
    objective = KnowledgePoint(
        id="objective-1", name="Remember", type=KnowledgeType.MEMORY, module_id="module-1"
    )
    module = LearningModule(id="module-1", name="Module", order=0, knowledge_points=[objective])
    service.replace_modules_for_path("path-1", [module])
    service.replace_modules_for_path("path-2", [module])
    store.create_learning_thread(
        LearningThread(
            thread_id="thread-1",
            session_id="session-1",
            scope="lesson",
            goal="Path one",
            status="active",
            path_id="path-1",
        )
    )
    store.create_learning_thread(
        LearningThread(
            thread_id="thread-2",
            session_id="session-2",
            scope="lesson",
            goal="Path two",
            status="active",
            path_id="path-2",
        )
    )
    store.append_evidence(_evidence("wrong-now", thread_id="thread-2", path_id="path-2"))
    store.append_evidence(
        _evidence(
            "wrong-delayed",
            created_at=NOW - 20 * 60 * 60,
            thread_id="thread-2",
            path_id="path-2",
        )
    )

    with sqlite3.connect(store.db_path) as conn:
        rows = conn.execute("SELECT evidence_id, payload_json FROM learning_evidence").fetchall()
        for evidence_id, payload_json in rows:
            payload = json.loads(payload_json)
            payload["path_id"] = "path-1"
            conn.execute(
                "UPDATE learning_evidence SET payload_json = ? WHERE evidence_id = ?",
                (json.dumps(payload), evidence_id),
            )
        conn.commit()

    assert service.recalculate_evidence_mastery("path-1", "objective-1") is False


def test_recalculate_false_gate_preserves_an_existing_review_queue(tmp_path) -> None:
    store = LearningStore(root=tmp_path)
    service = LearningService(store)
    objective = KnowledgePoint(
        id="objective-1", name="Remember", type=KnowledgeType.MEMORY, module_id="module-1"
    )
    service.replace_modules_for_path(
        "path-1",
        [LearningModule(id="module-1", name="Module", order=0, knowledge_points=[objective])],
    )
    preserved_state = RepetitionState(next_review_at=NOW)

    def seed_review_queue(tx) -> None:
        tx.progress.review_queue = [
            ReviewTask(
                id="review_objective-1",
                knowledge_point_id="objective-1",
                knowledge_type=KnowledgeType.MEMORY,
                due_at=NOW,
                priority=2,
                state=preserved_state,
            )
        ]
        tx.touch()

    store.mutate("path-1", seed_review_queue)

    assert service.recalculate_evidence_mastery("path-1", "objective-1") is False
    progress = store.load("path-1")
    assert progress is not None
    assert progress.evidence_mastery == {"objective-1": False}
    assert progress.review_queue[0].id == "review_objective-1"


def test_concurrent_recalculation_emits_only_the_state_change(tmp_path) -> None:
    store = LearningStore(root=tmp_path)
    service = LearningService(store)
    objective = KnowledgePoint(
        id="objective-1",
        name="Concept",
        type=KnowledgeType.CONCEPT,
        module_id="module-1",
    )
    service.replace_modules_for_path(
        "path-1",
        [LearningModule(id="module-1", name="Module", order=0, knowledge_points=[objective])],
    )
    store.create_learning_thread(
        LearningThread(
            thread_id="thread-1",
            session_id="session-1",
            scope="lesson",
            goal="Learn the concept",
            status="active",
            path_id="path-1",
        )
    )
    store.append_evidence(_evidence("evidence-1", activity_kind="teach_back"))

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda _index: service.recalculate_evidence_mastery("path-1", "objective-1"),
                range(2),
            )
        )

    assert results == [True, True]
    recalculation_events = [
        event
        for event in store.list_events("path-1")
        if event.event_type == "mastery.evidence_recalculated"
    ]
    assert len(recalculation_events) == 1


def test_service_removal_recalculates_only_the_bound_objective(tmp_path) -> None:
    store = LearningStore(root=tmp_path)
    service = LearningService(store)
    objectives = [
        KnowledgePoint(
            id=objective_id,
            name=objective_id,
            type=KnowledgeType.CONCEPT,
            module_id="module-1",
        )
        for objective_id in ("objective-1", "objective-2")
    ]
    service.replace_modules_for_path(
        "path-1",
        [LearningModule(id="module-1", name="Module", order=0, knowledge_points=objectives)],
    )
    store.create_learning_thread(
        LearningThread(
            thread_id="thread-1",
            session_id="session-1",
            scope="lesson",
            goal="Learn both objectives",
            status="active",
            path_id="path-1",
        )
    )
    for objective_id in ("objective-1", "objective-2"):
        store.append_evidence(
            _evidence(
                f"evidence-{objective_id}",
                objective_id=objective_id,
                activity_kind="teach_back",
            )
        )
        assert service.recalculate_evidence_mastery("path-1", objective_id) is True

    removed = service.remove_evidence("evidence-objective-1")

    progress = store.load("path-1")
    assert removed is not None and removed.removed_at is not None
    assert progress is not None
    assert progress.evidence_mastery == {"objective-1": False, "objective-2": True}


def test_service_removal_replay_repairs_failed_recalculation_without_duplicate_audit(
    tmp_path, monkeypatch
) -> None:
    store = LearningStore(root=tmp_path)
    service = LearningService(store)
    objective = KnowledgePoint(
        id="objective-1",
        name="Concept",
        type=KnowledgeType.CONCEPT,
        module_id="module-1",
    )
    service.replace_modules_for_path(
        "path-1",
        [LearningModule(id="module-1", name="Module", order=0, knowledge_points=[objective])],
    )
    store.create_learning_thread(
        LearningThread(
            thread_id="thread-1",
            session_id="session-1",
            scope="lesson",
            goal="Learn the concept",
            status="active",
            path_id="path-1",
        )
    )
    store.append_evidence(_evidence("evidence-1", activity_kind="teach_back"))
    assert service.recalculate_evidence_mastery("path-1", "objective-1") is True
    real_recalculate = service.recalculate_evidence_mastery
    calls = 0

    def fail_once(path_id: str, objective_id: str) -> bool:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("injected recalculation failure")
        return real_recalculate(path_id, objective_id)

    monkeypatch.setattr(service, "recalculate_evidence_mastery", fail_once)

    with pytest.raises(RuntimeError, match="injected recalculation failure"):
        service.remove_evidence("evidence-1")
    repaired = service.remove_evidence("evidence-1")

    progress = store.load("path-1")
    assert repaired is not None and repaired.removed_at is not None
    assert progress is not None
    assert progress.evidence_mastery == {"objective-1": False}
    with sqlite3.connect(store.db_path) as connection:
        removal_audits = connection.execute(
            "SELECT COUNT(*) FROM learning_audit_events WHERE event_type = 'evidence.removed'"
        ).fetchone()[0]
    assert removal_audits == 1
