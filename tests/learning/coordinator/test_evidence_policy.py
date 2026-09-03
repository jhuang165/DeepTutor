from __future__ import annotations

from deeptutor.learning.evidence import evidence_gate, validate_open_assessment
from deeptutor.learning.models import (
    EvidenceOutcome,
    EvidenceRecord,
    KnowledgePoint,
    KnowledgeType,
    LearningModule,
    LearningThread,
    RepetitionState,
)
from deeptutor.learning.service import LearningService
from deeptutor.learning.storage import LearningStore

NOW = 1_700_000_000.0


def _evidence(
    evidence_id: str,
    *,
    activity_kind: str = "retrieval",
    outcome: EvidenceOutcome = EvidenceOutcome.CORRECT,
    help_level: int = 0,
    independent: bool = True,
    transfer: bool = False,
    artifact_ref: str = "",
    created_at: float = NOW,
    removed_at: float | None = None,
) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=evidence_id,
        thread_id="thread-1",
        path_id="path-1",
        objective_id="objective-1",
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
    assert validate_open_assessment(
        {
            "outcome": "correct",
            "rubric": [{"id": "mechanism", "passed": True}],
            "cited_evidence": ["words the learner never wrote"],
            "uncertainty": 0.1,
        },
        "Eigenvectors keep their direction under the transform.",
    ) is None


def test_high_uncertainty_is_unassessed() -> None:
    assert validate_open_assessment(
        {
            "outcome": "correct",
            "rubric": [{"id": "mechanism", "passed": True}],
            "cited_evidence": ["keep their direction"],
            "uncertainty": 0.6,
        },
        "They keep their direction.",
    ) is None


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

    assert evidence_gate(KnowledgeType.PROCEDURE, [independent_solution, transfer_variation]) is True


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


def test_recalculate_evidence_mastery_changes_only_evidence_gate_and_records_event(tmp_path) -> None:
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
