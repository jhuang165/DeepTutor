import sqlite3

import pytest

from deeptutor.learning.models import EvidenceRecord, LearningThread
from deeptutor.learning.storage import LearningStore, LearningStoreError


@pytest.fixture
def store(tmp_path) -> LearningStore:
    return LearningStore(root=tmp_path)


@pytest.fixture
def thread() -> LearningThread:
    return LearningThread(
        thread_id="thread-1",
        session_id="session-1",
        scope="lesson",
        goal="Understand eigenvectors",
        status="active",
        path_id="path-1",
        source_refs=["source-1"],
    )


def _record(*, evidence_id: str = "evidence-1") -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=evidence_id,
        thread_id="thread-1",
        path_id="path-1",
        objective_id="objective-1",
        activity_kind="guided_attempt",
        recipe_id="procedure-fading",
        recipe_version=1,
        response="An eigenvector keeps its direction.",
        outcome="correct",
        help_level=2,
        independent=True,
        session_id="session-1",
        turn_id="turn-1",
    )


def _audit_event_types(store: LearningStore) -> list[str]:
    with sqlite3.connect(store.db_path) as conn:
        return [row[0] for row in conn.execute("SELECT event_type FROM learning_audit_events")]


def test_learning_thread_round_trip_and_lifecycle(store: LearningStore, thread: LearningThread) -> None:
    created = store.create_learning_thread(thread)

    assert created == thread
    assert store.get_learning_thread(thread.thread_id) == thread
    assert store.list_learning_threads("session-1") == [thread]

    updated = store.set_learning_thread_next_activity(
        thread.thread_id, {"kind": "retrieval", "objective_id": "objective-1"}
    )
    completed = store.complete_learning_thread(thread.thread_id)

    assert updated.next_activity == {"kind": "retrieval", "objective_id": "objective-1"}
    assert completed.status == "completed"
    assert completed.next_activity == updated.next_activity
    assert _audit_event_types(store) == [
        "thread.created",
        "thread.next_activity",
        "thread.completed",
    ]


def test_append_evidence_requires_an_existing_thread(store: LearningStore) -> None:
    with pytest.raises(LearningStoreError, match="Unknown learning thread: thread-1"):
        store.append_evidence(_record())


def test_evidence_replay_and_removal_are_idempotent(
    store: LearningStore, thread: LearningThread
) -> None:
    store.create_learning_thread(thread)
    record = _record()

    assert store.append_evidence(record) == record
    assert store.append_evidence(record) == record
    assert store.list_evidence(path_id="path-1", objective_id="objective-1") == [record]

    removed = store.remove_evidence(record.evidence_id)
    replayed_removal = store.remove_evidence(record.evidence_id)

    assert removed is not None and removed.removed_at is not None
    assert replayed_removal == removed
    assert store.list_evidence(path_id="path-1", objective_id="objective-1") == []
    assert store.list_evidence(thread_id="thread-1", include_removed=True) == [removed]
    assert _audit_event_types(store) == ["thread.created", "evidence.appended", "evidence.removed"]
