"""Read-only projection of a learner's next useful actions."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TypeVar

from deeptutor.learning import policy
from deeptutor.learning.coordinator.models import LearningQueueItem, LearningQueueReason
from deeptutor.learning.models import InteractionStatus, LearningProgress, LearningThreadStatus
from deeptutor.learning.service import LearningService
from deeptutor.learning.storage import LearningReadSnapshot, LearningStore

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
            learning_service.store
            if learning_service is not None
            else LearningStore(initialize=False)
        )

    @staticmethod
    def _unfinished(
        snapshot: LearningReadSnapshot,
        path_ids: set[str],
        session_id: str,
    ) -> list[LearningQueueItem]:
        items: list[LearningQueueItem] = []
        for path_id, interaction in snapshot.active_interactions(path_ids).items():
            if session_id and interaction.session_id != session_id:
                continue
            objective_id = interaction.question.knowledge_point_id
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
                    reason_data={
                        "objective": objective_id,
                        "answer_state": "pending_grading" if answered else "pending_answer",
                    },
                    priority=0,
                )
            )
        return items

    @staticmethod
    def _threads(snapshot: LearningReadSnapshot, session_id: str) -> list[LearningQueueItem]:
        items: list[LearningQueueItem] = []
        for thread in snapshot.learning_threads(session_id, status=LearningThreadStatus.ACTIVE):
            activity = dict(thread.next_activity)
            objective_id = str(activity.get("objective_id") or "")
            transfer_required = bool(activity.get("transfer_required", False))
            reason = (
                LearningQueueReason.NEEDS_TRANSFER
                if transfer_required
                else LearningQueueReason.RESUME_LESSON
            )
            items.append(
                LearningQueueItem(
                    thread_id=thread.thread_id,
                    path_id=thread.path_id,
                    objective_id=objective_id,
                    activity=activity,
                    reason=reason,
                    reason_data={"goal": thread.goal},
                    priority=30 if transfer_required else 10,
                )
            )
        return items

    @staticmethod
    def _reviews(
        now: float | None,
        path_ids: set[str],
        progress_by_id: dict[str, LearningProgress],
    ) -> list[LearningQueueItem]:
        items: list[LearningQueueItem] = []
        for path_id in sorted(path_ids):
            progress = progress_by_id.get(path_id)
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
                        reason_data={"objective": review.knowledge_point_id},
                        priority=20,
                        due_at=review.due_at,
                    )
                )
        return items

    @staticmethod
    def _paths(
        path_ids: set[str], progress_by_id: dict[str, LearningProgress]
    ) -> list[LearningQueueItem]:
        items: list[LearningQueueItem] = []
        overviews: list[dict[str, object]] = []
        for path_id in path_ids:
            progress = progress_by_id.get(path_id)
            if progress is None:
                continue
            summary = policy.map_summary(progress)
            counts = summary["counts"]
            overviews.append(
                {
                    "path_id": progress.book_id,
                    "name": policy.path_display_name(progress),
                    "objectives": counts["total"],
                    "mastered": counts["mastered"],
                    "complete": summary["complete"],
                    "updated_at": progress.updated_at,
                }
            )
        overviews.sort(key=lambda overview: float(overview["updated_at"]), reverse=True)
        for overview in overviews:
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
                    reason_data={"path_name": name},
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
        with self._store.read_snapshot() as snapshot:
            progress_by_id = snapshot.progress_by_id()
            all_path_ids = set(progress_by_id)
            path_ids = snapshot.session_path_ids(session_id) if session_id else all_path_ids
            candidates = [
                *self._unfinished(snapshot, all_path_ids, session_id),
                *self._threads(snapshot, session_id),
                *self._reviews(now, path_ids, progress_by_id),
                *self._paths(path_ids, progress_by_id),
            ]
        deduped = keep_best_identity(candidates, key=_rank)
        return sorted(deduped, key=_rank)[: max(0, limit)]
