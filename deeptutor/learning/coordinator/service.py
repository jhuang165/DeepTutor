"""Payload adapter and coordinator facade for deterministic learning plans."""

from __future__ import annotations

from collections.abc import Collection, Mapping
import hashlib
import logging
from typing import Any

from deeptutor.learning.coordinator.models import (
    CapabilityLearningResult,
    LearningDecision,
    LearningRequest,
    LearningScope,
)
from deeptutor.learning.coordinator.planner import ActivityPlanner
from deeptutor.learning.coordinator.scope import LLMScopeClassifier, ScopeDetector
from deeptutor.learning.evidence import validate_open_assessment
from deeptutor.learning.models import (
    EvidenceOutcome,
    EvidenceRecord,
    LearningThread,
    LearningThreadStatus,
)
from deeptutor.learning.service import LearningService
from deeptutor.learning.storage import LearningStore
from deeptutor.services.llm.config import LLMConfig

logger = logging.getLogger(__name__)

_ATTACHED_ONLY_PHRASES = (
    "use only my attached",
    "use only the attached",
    "only use my attached",
    "only use the attached",
    "use only my uploaded",
    "only use my uploaded",
    "只使用我上传的",
    "仅使用我上传的",
    "只使用附件",
    "仅使用附件",
)
_STUCK_PHRASES = (
    "i'm stuck",
    "i am stuck",
    "i’m stuck",
    "stuck on",
    "我卡住了",
    "我卡住",
)
_SOURCE_FIELDS = (
    "attachments",
    "knowledge_bases",
    "book_references",
    "reading_references",
)


def _text(value: Any, default: str = "") -> str:
    return str(value).strip() if value is not None else default


def _contains_phrase(message: str, phrases: tuple[str, ...]) -> bool:
    normalized = message.casefold()
    return any(phrase in normalized for phrase in phrases)


def learning_request_from_payload(payload: Mapping[str, Any]) -> LearningRequest:
    """Project a validated turn payload onto the coordinator's narrow contract."""
    message = _text(payload.get("content"))
    raw_state = payload.get("learning_state")
    learning_state: Mapping[str, Any] = raw_state if isinstance(raw_state, Mapping) else {}
    return LearningRequest(
        message=message,
        requested_capability=_text(
            payload.get("requested_capability") or payload.get("capability"),
            "chat",
        ),
        language=_text(payload.get("language"), "en"),
        has_sources=any(bool(payload.get(field)) for field in _SOURCE_FIELDS),
        attached_only_requested=_contains_phrase(message, _ATTACHED_ONLY_PHRASES),
        course_id=_text(payload.get("course_id")),
        mastery_path_id=_text(payload.get("mastery_path_id")),
        workspace_mode=_text(payload.get("workspace_mode")),
        direct_answer_requested=bool(payload.get("direct_answer_requested", False)),
        stuck_signal=_contains_phrase(message, _STUCK_PHRASES),
        repeated_request=bool(learning_state.get("repeated_request", False)),
        previous_help_level=learning_state.get("previous_help_level", 0),
        last_outcome=learning_state.get("last_outcome", ""),
        server_next_activity=learning_state.get("server_next_activity"),
    )


def decision_payload(decision: LearningDecision) -> dict[str, Any]:
    """Serialize a validated decision for runtime extension metadata."""
    return decision.model_dump(mode="json")


class LearningCoordinator:
    """Classify a validated payload and select one deterministic activity."""

    def __init__(
        self,
        *,
        planner: ActivityPlanner | None = None,
        detector: ScopeDetector | None = None,
        store: LearningStore | None = None,
        learning_service: LearningService | None = None,
    ) -> None:
        self._planner = planner or ActivityPlanner()
        self._detector = detector
        self._store = store
        self._learning_service = learning_service

    async def prepare_payload(
        self,
        payload: Mapping[str, Any],
        available_capabilities: set[str],
        llm_config: LLMConfig,
    ) -> LearningDecision:
        request = learning_request_from_payload(payload)
        detector = self._detector or ScopeDetector(
            classifier=LLMScopeClassifier(request.language, config=llm_config)
        )
        scope = await detector.detect(request)
        return self._planner.plan(scope, request, available_capabilities)

    async def finish(
        self,
        decision: LearningDecision,
        result: CapabilityLearningResult,
        *,
        session_id: str,
        turn_id: str,
        learner_response: str,
        allowed_source_refs: Collection[str],
    ) -> EvidenceRecord | None:
        """Persist server-derived evidence after a successful teaching turn."""

        if decision.scope in {LearningScope.ANSWER, LearningScope.PATH}:
            return None

        if self._store is None:
            self._store = (
                self._learning_service.store
                if self._learning_service is not None
                else LearningStore()
            )
        if self._learning_service is None:
            self._learning_service = LearningService(self._store)

        source_refs = sorted(set(result.source_refs) & set(allowed_source_refs))
        dropped_source_refs = sorted(set(result.source_refs) - set(source_refs))
        if dropped_source_refs:
            logger.warning(
                "Dropped unverified learning source refs turn=%s source_refs=%s",
                turn_id,
                dropped_source_refs,
            )

        thread_id = (
            decision.thread_id
            or hashlib.sha256(f"{session_id}:{decision.goal}".encode()).hexdigest()[:32]
        )
        thread = self._store.get_learning_thread(thread_id)
        if thread is None:
            thread = self._store.create_learning_thread(
                LearningThread(
                    thread_id=thread_id,
                    session_id=session_id,
                    scope="lesson",
                    goal=decision.goal,
                    status=LearningThreadStatus.ACTIVE,
                    source_refs=source_refs,
                )
            )

        validated = validate_open_assessment(result.assessment, learner_response)
        outcome = (
            EvidenceOutcome(validated.outcome)
            if validated is not None
            else EvidenceOutcome.UNASSESSED
        )
        evidence_id = hashlib.sha256(
            f"{turn_id}:{decision.objective_id}:{decision.activity.kind.value}:"
            f"{decision.activity.recipe_step}".encode()
        ).hexdigest()[:32]
        independent = (
            validated is not None
            and decision.activity.independent_required
            and decision.activity.help_level <= 2
        )
        record = EvidenceRecord(
            evidence_id=evidence_id,
            thread_id=thread_id,
            path_id=thread.path_id,
            objective_id=decision.objective_id,
            activity_kind=decision.activity.kind.value,
            recipe_id=decision.activity.recipe_id,
            recipe_version=decision.activity.recipe_version,
            response=learner_response[:8_000],
            response_ref=(f"chat-turn:{turn_id}:user" if len(learner_response) > 8_000 else ""),
            artifact_ref=result.artifact_ref,
            outcome=outcome,
            help_level=decision.activity.help_level,
            independent=independent,
            transfer=independent and decision.activity.transfer_required,
            rubric=(
                [item.model_dump(mode="json") for item in validated.rubric]
                if validated is not None
                else []
            ),
            cited_evidence=(validated.cited_evidence if validated is not None else []),
            uncertainty=(validated.uncertainty if validated is not None else 1.0),
            source_refs=source_refs,
            session_id=session_id,
            turn_id=turn_id,
        )
        stored = self._store.append_evidence(record)
        if validated is not None and thread.path_id and decision.objective_id:
            self._learning_service.recalculate_evidence_mastery(
                thread.path_id, decision.objective_id
            )
        next_activity = self._planner.next_after(decision, outcome.value)
        self._store.set_learning_thread_next_activity(
            thread_id, next_activity.model_dump(mode="json")
        )
        return stored


__all__ = ["LearningCoordinator", "decision_payload", "learning_request_from_payload"]
