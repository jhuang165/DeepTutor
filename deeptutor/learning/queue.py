"""Read-only projection of a learner's next useful actions."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TypeVar

from deeptutor.learning import policy
from deeptutor.learning.coordinator.models import LearningQueueItem, LearningQueueReason
from deeptutor.learning.models import InteractionStatus, LearningThreadStatus
from deeptutor.learning.service import LearningService
from deeptutor.learning.storage import LearningStore

_QueueItem = TypeVar("_QueueItem", bound=LearningQueueItem)


def _rank(item: LearningQueueItem) -> tuple[float, float, str, str]:
    due_at = float("inf") if item.due_at is None else item.due_at
    return (float(item.priority), due_at, item.thread_id, item.path_id)


def keep_best_identity(
    candidates: Iterable[_QueueItem],
    *,
    key: Callable[[_QueueItem], tuple[float, float, str, str]],
) -> list[_QueueItem]:
    """Keep the best-ranked candidate for each thread/path identity."""

    selected: dict[tuple[str, str], _QueueItem] = {}
    for item in candidates:
        identity = (item.thread_id, item.path_id)
        current = selected.get(identity)
        if current is None or key(item) < key(current):
            selected[identity] = item
    return list(selected.values())


class LearningQueueService:
    """Project durable learning state without advancing or changing it."""

    def __init__(
        self,
        *,
        store: LearningStore | None = None,
        learning_service: LearningService | None = None,
    ) -> None:
        self._store = store or (
            learning_service.store if learning_service is not None else LearningStore()
        )
        self._learning_service = learning_service or LearningService(self._store)

    def _unfinished(self, session_id: str) -> list[LearningQueueItem]:
        items: list[LearningQueueItem] = []
        for path_id in self._store.list_all_read_only():
            interaction = self._store.get_active_interaction_read_only(path_id)
            if interaction is None or (session_id and interaction.session_id != session_id):
                continue
            objective_id = interaction.question.knowledge_point_id
            objective = objective_id or "this objective"
            answered = interaction.status is InteractionStatus.ANSWERED
            items.append(
                LearningQueueItem(
                    path_id=path_id,
                    objective_id=objective_id,
                    activity={
                        "kind": "grade_pending" if answered else "answer_pending",
                        "interaction_id": interaction.interaction_id,
                        "prompt": interaction.question.prompt,
                    },
                    reason=LearningQueueReason.UNFINISHED_ATTEMPT,
                    reason_text=(
                        f"Your answer for {objective} is waiting for grading."
                        if answered
                        else f"Answer the outstanding question for {objective}."
                    ),
                    priority=0,
                )
            )
        return items

    def _threads(self, session_id: str) -> list[LearningQueueItem]:
        items: list[LearningQueueItem] = []
        for thread in self._store.list_learning_threads_read_only(
            session_id, status=LearningThreadStatus.ACTIVE
        ):
            activity = dict(thread.next_activity)
            objective_id = str(activity.get("objective_id") or "")
            transfer_required = bool(activity.get("transfer_required", False))
            reason = (
                LearningQueueReason.NEEDS_TRANSFER
                if transfer_required
                else LearningQueueReason.RESUME_LESSON
            )
            reason_text = (
                f"Apply {thread.goal} to a new situation to show transfer."
                if transfer_required
                else f"Resume the lesson: {thread.goal}."
            )
            items.append(
                LearningQueueItem(
                    thread_id=thread.thread_id,
                    path_id=thread.path_id,
                    objective_id=objective_id,
                    activity=activity,
                    reason=reason,
                    reason_text=reason_text,
                    priority=30 if transfer_required else 10,
                )
            )
        return items

    def _reviews(self, now: float | None, path_ids: set[str]) -> list[LearningQueueItem]:
        items: list[LearningQueueItem] = []
        for path_id in sorted(path_ids):
            progress = self._store.load_read_only(path_id)
            if progress is None:
                continue
            for review in policy.due_reviews(progress, now=now):
                items.append(
                    LearningQueueItem(
                        path_id=path_id,
                        objective_id=review.knowledge_point_id,
                        activity={
                            "kind": "review",
                            "review_id": review.id,
                            "knowledge_type": review.knowledge_type.value,
                        },
                        reason=LearningQueueReason.DUE_REVIEW,
                        reason_text=(
                            f"Review {review.knowledge_point_id}; its spaced-repetition "
                            "practice is due."
                        ),
                        priority=20,
                        due_at=review.due_at,
                    )
                )
        return items

    def _paths(self, path_ids: set[str]) -> list[LearningQueueItem]:
        items: list[LearningQueueItem] = []
        for overview in self._learning_service.list_path_overviews_read_only(path_ids=path_ids):
            if overview["complete"]:
                continue
            path_id = str(overview["path_id"])
            name = str(overview["name"])
            items.append(
                LearningQueueItem(
                    path_id=path_id,
                    activity={
                        "kind": "continue_path",
                        "name": name,
                        "objectives": overview["objectives"],
                        "mastered": overview["mastered"],
                    },
                    reason=LearningQueueReason.CONTINUE_PATH,
                    reason_text=f"Continue {name} with its next unmastered objective.",
                    priority=40,
                )
            )
        return items

    def list_items(
        self,
        *,
        session_id: str = "",
        limit: int = 10,
        now: float | None = None,
    ) -> list[LearningQueueItem]:
        path_ids = (
            {
                str(binding["path_id"])
                for binding in self._store.list_paths_for_session_read_only(session_id)
            }
            if session_id
            else set(self._store.list_all_read_only())
        )
        candidates = [
            *self._unfinished(session_id),
            *self._threads(session_id),
            *self._reviews(now, path_ids),
            *self._paths(path_ids),
        ]
        deduped = keep_best_identity(candidates, key=_rank)
        return sorted(deduped, key=_rank)[: max(0, limit)]
