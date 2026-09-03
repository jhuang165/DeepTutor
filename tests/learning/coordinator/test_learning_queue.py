from __future__ import annotations

import json
import sqlite3

import pytest

from deeptutor.learning.coordinator.models import ActivityKind, ActivityPlan
from deeptutor.learning.models import (
    InteractionStatus,
    KnowledgePoint,
    KnowledgeType,
    LearningModule,
    LearningThread,
    MasteryInteraction,
    PendingQuestion,
    RepetitionState,
    ReviewTask,
)
from deeptutor.learning.queue import (
    LearningQueueItem,
    LearningQueueReason,
    LearningQueueService,
    keep_best_identity,
)
from deeptutor.learning.service import LearningService
import deeptutor.learning.storage as storage_module
from deeptutor.learning.storage import LearningStore


@pytest.fixture
def store(tmp_path) -> LearningStore:
    return LearningStore(root=tmp_path)


@pytest.fixture
def learning_service(store: LearningStore) -> LearningService:
    return LearningService(store)


@pytest.fixture
def service(store: LearningStore, learning_service: LearningService) -> LearningQueueService:
    return LearningQueueService(store=store, learning_service=learning_service)


def _seed_path(
    learning_service: LearningService,
    path_id: str,
    *,
    objective_id: str = "objective-1",
) -> None:
    learning_service.replace_modules_for_path(
        path_id,
        [
            LearningModule(
                id="module-1",
                name=f"{path_id} module",
                order=0,
                knowledge_points=[
                    KnowledgePoint(
                        id=objective_id,
                        name=f"{path_id} objective",
                        type=KnowledgeType.CONCEPT,
                        module_id="module-1",
                    )
                ],
            )
        ],
        name=path_id,
    )


def _seed_due_review(
    store: LearningStore,
    path_id: str,
    *,
    objective_id: str = "objective-1",
    due_at: float = 999.0,
) -> None:
    def add_review(tx) -> None:
        tx.progress.review_queue = [
            ReviewTask(
                id=f"review-{path_id}",
                knowledge_point_id=objective_id,
                knowledge_type=KnowledgeType.CONCEPT,
                due_at=due_at,
                priority=99,
                state=RepetitionState(next_review_at=due_at),
            )
        ]
        tx.touch()

    store.mutate(path_id, add_review)


def _seed_active_interaction(
    store: LearningStore,
    path_id: str,
    *,
    session_id: str = "s",
    objective_id: str = "objective-1",
    status: InteractionStatus = InteractionStatus.AWAITING_INPUT,
) -> None:
    question = PendingQuestion(
        question_id=f"question-{path_id}",
        knowledge_point_id=objective_id,
        module_id="module-1",
        prompt="What is the next step?",
        expected_answer="A learner answer",
    )

    def add_interaction(tx) -> None:
        tx.progress.pending_question = question
        tx.put_interaction(
            MasteryInteraction(
                interaction_id=f"interaction-{path_id}",
                path_id=path_id,
                question=question,
                status=status,
                session_id=session_id,
            )
        )

    store.mutate(path_id, add_interaction)


def _seed_thread(
    store: LearningStore,
    thread_id: str,
    *,
    path_id: str = "",
    next_activity: dict | None = None,
) -> None:
    store.create_learning_thread(
        LearningThread(
            thread_id=thread_id,
            session_id="s",
            scope="lesson",
            goal=f"Goal for {thread_id}",
            status="active",
            path_id=path_id,
            next_activity=next_activity or {},
        )
    )


def _storage_snapshot(root) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_queue_orders_unfinished_attempt_before_due_review(
    service: LearningQueueService, store: LearningStore, learning_service: LearningService
) -> None:
    _seed_path(learning_service, "path-pending")
    _seed_path(learning_service, "path-review")
    _seed_active_interaction(store, "path-pending")
    _seed_due_review(store, "path-review")
    store.bind_session("path-review", "s")

    items = service.list_items(session_id="s", now=1_000.0)

    assert [item.reason for item in items[:2]] == [
        LearningQueueReason.UNFINISHED_ATTEMPT,
        LearningQueueReason.DUE_REVIEW,
    ]


def test_queue_contains_one_item_per_thread_or_path(
    service: LearningQueueService, store: LearningStore, learning_service: LearningService
) -> None:
    _seed_path(learning_service, "path-1")
    _seed_due_review(store, "path-1")
    _seed_thread(store, "thread-1", path_id="path-1")

    items = service.list_items(session_id="s", now=1_000.0)
    identities = [(item.thread_id, item.path_id) for item in items]

    assert len(identities) == len(set(identities))


def test_queue_reason_is_learner_readable(
    service: LearningQueueService, store: LearningStore, learning_service: LearningService
) -> None:
    _seed_path(learning_service, "path-1")
    _seed_active_interaction(store, "path-1")

    item = service.list_items(session_id="s")[0]

    assert item.reason_text
    assert "unknown" not in item.reason_text.lower()
    assert item.activity["kind"] == "answer_pending"
    assert item.priority == 0


def test_queue_projects_resume_and_transfer_thread_activities(
    service: LearningQueueService, store: LearningStore
) -> None:
    _seed_thread(
        store,
        "thread-resume",
        next_activity=ActivityPlan(
            kind=ActivityKind.RETRIEVAL,
            objective="Recall the definition",
            learner_action="Answer from memory.",
        ).model_dump(mode="json"),
    )
    _seed_thread(
        store,
        "thread-transfer",
        next_activity=ActivityPlan(
            kind=ActivityKind.GUIDED_ATTEMPT,
            objective="Apply the idea",
            learner_action="Solve a new case.",
            transfer_required=True,
        ).model_dump(mode="json"),
    )

    items = service.list_items(session_id="s")

    assert [(item.thread_id, item.reason) for item in items] == [
        ("thread-resume", LearningQueueReason.RESUME_LESSON),
        ("thread-transfer", LearningQueueReason.NEEDS_TRANSFER),
    ]
    assert items[1].activity["learner_action"] == "Solve a new case."


def test_queue_uses_due_time_then_identity_for_stable_ranking(
    service: LearningQueueService, store: LearningStore, learning_service: LearningService
) -> None:
    _seed_path(learning_service, "path-later")
    _seed_path(learning_service, "path-earlier")
    _seed_due_review(store, "path-later", due_at=900.0)
    _seed_due_review(store, "path-earlier", due_at=800.0)

    items = service.list_items(now=1_000.0)

    assert [(item.path_id, item.due_at) for item in items[:2]] == [
        ("path-earlier", 800.0),
        ("path-later", 900.0),
    ]


def test_queue_ranks_zero_due_time_before_a_later_due_review(
    service: LearningQueueService, store: LearningStore, learning_service: LearningService
) -> None:
    _seed_path(learning_service, "path-zero")
    _seed_path(learning_service, "path-later")
    _seed_due_review(store, "path-zero", due_at=0.0)
    _seed_due_review(store, "path-later", due_at=1.0)

    items = service.list_items(now=1_000.0)

    assert [(item.path_id, item.due_at) for item in items[:2]] == [
        ("path-zero", 0.0),
        ("path-later", 1.0),
    ]


def test_queue_filters_reviews_and_paths_to_the_bound_session(
    service: LearningQueueService, store: LearningStore, learning_service: LearningService
) -> None:
    _seed_path(learning_service, "path-owned")
    _seed_path(learning_service, "path-other")
    _seed_due_review(store, "path-owned")
    _seed_due_review(store, "path-other")
    store.bind_session("path-owned", "s")
    store.bind_session("path-other", "other-session")

    session_items = service.list_items(session_id="s", now=1_000.0)
    global_items = service.list_items(now=1_000.0)

    assert {item.path_id for item in session_items} == {"path-owned"}
    assert {item.path_id for item in global_items} == {"path-owned", "path-other"}


def test_queue_does_not_migrate_or_archive_a_legacy_path(
    service: LearningQueueService, store: LearningStore, tmp_path
) -> None:
    legacy_path = tmp_path / "legacy-path.json"
    legacy_path.write_text(
        json.dumps(
            {
                "book_id": "legacy-path",
                "name": "Legacy path",
                "modules": [
                    {
                        "id": "module-1",
                        "name": "Legacy module",
                        "order": 0,
                        "knowledge_points": [
                            {
                                "id": "objective-1",
                                "name": "Legacy objective",
                                "type": "concept",
                                "module_id": "module-1",
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    before = _storage_snapshot(tmp_path)

    items = service.list_items()

    assert [(item.path_id, item.reason) for item in items] == [
        ("legacy-path", LearningQueueReason.CONTINUE_PATH)
    ]
    assert _storage_snapshot(tmp_path) == before


def test_queue_reads_committed_wal_state_without_changing_source_files(
    service: LearningQueueService, store: LearningStore, learning_service: LearningService, tmp_path
) -> None:
    _seed_path(learning_service, "wal-path")
    progress = store.load("wal-path")
    assert progress is not None
    progress.name = "Visible only from WAL"
    connection = sqlite3.connect(store.db_path)
    try:
        connection.execute(
            "UPDATE mastery_paths SET state_json = ? WHERE path_id = ?",
            (json.dumps(progress.model_dump(mode="json")), "wal-path"),
        )
        connection.commit()
        assert (tmp_path / "mastery.sqlite3-wal").exists()
        before = _storage_snapshot(tmp_path)

        items = service.list_items()

        assert [(item.path_id, item.activity["name"]) for item in items] == [
            ("wal-path", "Visible only from WAL")
        ]
        assert _storage_snapshot(tmp_path) == before
    finally:
        connection.close()


def test_queue_read_only_apis_ignore_existing_database_without_required_tables(
    service: LearningQueueService, store: LearningStore, tmp_path
) -> None:
    for path in tmp_path.glob("mastery.sqlite3*"):
        path.unlink()
    sqlite3.connect(store.db_path).close()
    before = _storage_snapshot(tmp_path)

    assert store.list_all_read_only() == []
    assert store.load_read_only("missing") is None
    assert store.list_learning_threads_read_only() == []
    assert store.list_paths_for_session_read_only("s") == []
    assert store.get_active_interaction_read_only("missing") is None
    assert service.list_items() == []
    assert _storage_snapshot(tmp_path) == before


def test_queue_read_only_apis_skip_partial_column_rows_without_source_changes(
    service: LearningQueueService, store: LearningStore, tmp_path
) -> None:
    for path in tmp_path.glob("mastery.sqlite3*"):
        path.unlink()
    with sqlite3.connect(store.db_path) as connection:
        connection.execute("CREATE TABLE mastery_paths (path_id TEXT PRIMARY KEY)")
        connection.execute("INSERT INTO mastery_paths (path_id) VALUES ('partial')")
    before = _storage_snapshot(tmp_path)

    assert store.list_all_read_only() == []
    assert store.load_read_only("partial") is None
    assert store.list_learning_threads_read_only() == []
    assert store.list_paths_for_session_read_only("s") == []
    assert store.get_active_interaction_read_only("partial") is None
    assert service.list_items() == []
    assert _storage_snapshot(tmp_path) == before


def test_queue_read_only_apis_skip_malformed_progress_rows_without_source_changes(
    service: LearningQueueService, store: LearningStore, tmp_path
) -> None:
    for path in tmp_path.glob("mastery.sqlite3*"):
        path.unlink()
    with sqlite3.connect(store.db_path) as connection:
        connection.execute(
            """
            CREATE TABLE mastery_paths (
                path_id TEXT PRIMARY KEY,
                state_json TEXT NOT NULL,
                revision INTEGER NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO mastery_paths (path_id, state_json, revision, created_at, updated_at)
            VALUES ('malformed', '{not json', 1, 0.0, 0.0)
            """
        )
    before = _storage_snapshot(tmp_path)

    assert store.list_all_read_only() == []
    assert store.load_read_only("malformed") is None
    assert store.list_learning_threads_read_only() == []
    assert store.list_paths_for_session_read_only("s") == []
    assert store.get_active_interaction_read_only("malformed") is None
    assert service.list_items() == []
    assert _storage_snapshot(tmp_path) == before


def test_queue_retries_an_interleaved_snapshot_instead_of_returning_mixed_state(
    service: LearningQueueService,
    store: LearningStore,
    learning_service: LearningService,
    monkeypatch,
    tmp_path,
) -> None:
    _seed_path(learning_service, "interleaved-path")
    progress = store.load("interleaved-path")
    assert progress is not None
    progress.name = "After checkpoint"
    source_before_interleave = _storage_snapshot(tmp_path)
    original_copyfile = storage_module.shutil.copyfile
    interleaved = False
    source_after_interleave: dict[str, bytes] = {}

    def copyfile_with_writer(source, destination, *args, **kwargs):
        nonlocal interleaved, source_after_interleave
        copied = original_copyfile(source, destination, *args, **kwargs)
        if not interleaved and source == store.db_path:
            interleaved = True
            store.save(progress)
            with sqlite3.connect(store.db_path) as connection:
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            source_after_interleave = _storage_snapshot(tmp_path)
        return copied

    monkeypatch.setattr(storage_module.shutil, "copyfile", copyfile_with_writer)

    loaded = store.load_read_only("interleaved-path")
    items = service.list_items()

    assert interleaved is True
    assert source_after_interleave != source_before_interleave
    assert loaded is not None
    assert loaded.name == "After checkpoint"
    assert [(item.path_id, item.activity["name"]) for item in items] == [
        ("interleaved-path", "After checkpoint")
    ]
    assert _storage_snapshot(tmp_path) == source_after_interleave


def test_queue_projects_answered_interactions_as_waiting_for_grading(
    service: LearningQueueService, store: LearningStore, learning_service: LearningService
) -> None:
    _seed_path(learning_service, "path-1")
    _seed_active_interaction(store, "path-1", status=InteractionStatus.ANSWERED)

    item = service.list_items(session_id="s")[0]

    assert item.reason is LearningQueueReason.UNFINISHED_ATTEMPT
    assert item.activity["kind"] == "grade_pending"
    assert item.priority == 0
    assert "grading" in item.reason_text.lower()
    assert "answer the outstanding" not in item.reason_text.lower()


def test_queue_preserves_equal_rank_first_candidate_and_clamps_nonpositive_limits(
    service: LearningQueueService,
) -> None:
    first = LearningQueueItem(
        path_id="path-1",
        activity={"id": "first"},
        reason=LearningQueueReason.CONTINUE_PATH,
        reason_text="Continue.",
        priority=40,
    )
    second = first.model_copy(update={"activity": {"id": "second"}})

    assert keep_best_identity([first, second], key=lambda item: (0.0, 0.0, "", "")) == [first]
    assert service.list_items(limit=0) == []
    assert service.list_items(limit=-1) == []


def test_queue_listing_does_not_mutate_projected_learning_state(
    service: LearningQueueService, store: LearningStore, learning_service: LearningService
) -> None:
    _seed_path(learning_service, "path-1")
    _seed_due_review(store, "path-1")
    _seed_active_interaction(store, "path-1")
    _seed_thread(store, "thread-1", path_id="path-1")
    before = {
        "thread": store.get_learning_thread("thread-1").model_dump(mode="json"),
        "progress": store.load("path-1").model_dump(mode="json"),
        "interaction": store.get_active_interaction("path-1").model_dump(mode="json"),
        "events": [event.model_dump(mode="json") for event in store.list_events("path-1")],
    }

    service.list_items(session_id="s", now=1_000.0)

    after = {
        "thread": store.get_learning_thread("thread-1").model_dump(mode="json"),
        "progress": store.load("path-1").model_dump(mode="json"),
        "interaction": store.get_active_interaction("path-1").model_dump(mode="json"),
        "events": [event.model_dump(mode="json") for event in store.list_events("path-1")],
    }
    assert after == before
