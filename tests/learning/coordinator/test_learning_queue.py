from __future__ import annotations

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
from deeptutor.learning.queue import LearningQueueReason, LearningQueueService
from deeptutor.learning.service import LearningService
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
                status=InteractionStatus.AWAITING_INPUT,
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


def test_queue_orders_unfinished_attempt_before_due_review(
    service: LearningQueueService, store: LearningStore, learning_service: LearningService
) -> None:
    _seed_path(learning_service, "path-pending")
    _seed_path(learning_service, "path-review")
    _seed_active_interaction(store, "path-pending")
    _seed_due_review(store, "path-review")

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
