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
    ScopeResult,
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
        # This state is supplied only after runtime-owned user/session lookup;
        # public request validation rejects caller-supplied learning_state.
        state = payload.get("learning_state") or {}
        retained_scope = state.get("server_scope")
        scope = await detector.detect(
            request,
            retained_scope=ScopeResult.model_validate(retained_scope) if retained_scope else None,
        )
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
        allowed_artifact_refs: Collection[str] = (),
        learner_response_ref: str = "",
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
        dropped_source_count = len(set(result.source_refs) - set(source_refs))
        if dropped_source_count:
            logger.warning(
                "Dropped unverified learning source refs turn=%s dropped_count=%d",
                turn_id,
                dropped_source_count,
            )
        artifact_ref = (
            result.artifact_ref
            if result.artifact_ref and result.artifact_ref in set(allowed_artifact_refs)
            else ""
        )
        if result.artifact_ref and not artifact_ref:
            logger.warning(
                "Dropped unverified learning artifact ref turn=%s dropped_count=1",
                turn_id,
            )

        thread_id = (
            decision.thread_id
            or hashlib.sha256(f"{session_id}:{decision.goal}".encode()).hexdigest()[:32]
        )
        evidence_id = hashlib.sha256(
            f"{turn_id}:{decision.objective_id}:{decision.activity.kind.value}:"
            f"{decision.activity.recipe_step}".encode()
        ).hexdigest()[:32]
        existing_evidence = self._store.get_evidence(evidence_id)
        thread = self._store.get_learning_thread(thread_id)
        if existing_evidence is not None:
            if thread is None:
                raise ValueError(
                    "The persisted evidence recipe or binding refers to an unavailable thread"
                )
            self._validate_evidence_replay(
                existing_evidence,
                thread_id=thread_id,
                thread_path_id=thread.path_id,
                decision=decision,
                session_id=session_id,
                turn_id=turn_id,
            )
        if thread is None:
            if decision.thread_id:
                raise ValueError("Supplied learning thread does not exist")
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
        else:
            self._validate_lesson_thread(
                thread,
                decision=decision,
                session_id=session_id,
                allow_completed=existing_evidence is not None,
            )

        validated = validate_open_assessment(result.assessment, learner_response)
        outcome = (
            EvidenceOutcome(validated.outcome)
            if validated is not None
            else EvidenceOutcome.UNASSESSED
        )
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
            response_ref=(
                (learner_response_ref or f"chat-turn:{turn_id}:user")
                if len(learner_response) > 8_000
                else ""
            ),
            artifact_ref=artifact_ref,
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
        stored, _inserted = self._store.append_evidence_if_absent(record)
        self._validate_evidence_replay(
            stored,
            thread_id=thread_id,
            thread_path_id=thread.path_id,
            decision=decision,
            session_id=session_id,
            turn_id=turn_id,
        )
        mastery_passed: bool | None = None
        if (
            stored.removed_at is None
            and stored.outcome is not EvidenceOutcome.UNASSESSED
            and stored.path_id
            and stored.objective_id
        ):
            mastery_passed = self._learning_service.recalculate_evidence_mastery(
                stored.path_id, stored.objective_id
            )
        terminal = (
            stored.removed_at is None
            and stored.outcome is EvidenceOutcome.CORRECT
            and self._planner.is_terminal(decision)
            and (not stored.path_id or mastery_passed is True)
        )
        if terminal:
            self._store.complete_learning_thread(thread_id)
            return stored
        if thread.status is LearningThreadStatus.COMPLETED:
            return stored
        next_activity = self._planner.next_after(decision, stored.outcome.value)
        self._store.set_learning_thread_next_activity(
            thread_id, next_activity.model_dump(mode="json")
        )
        return stored

    @staticmethod
    def _validate_evidence_replay(
        stored: EvidenceRecord,
        *,
        thread_id: str,
        thread_path_id: str,
        decision: LearningDecision,
        session_id: str,
        turn_id: str,
    ) -> None:
        if (
            stored.thread_id != thread_id
            or stored.path_id != thread_path_id
            or stored.objective_id != decision.objective_id
            or stored.activity_kind != decision.activity.kind.value
            or stored.recipe_id != decision.activity.recipe_id
            or stored.recipe_version != decision.activity.recipe_version
            or stored.session_id != session_id
            or stored.turn_id != turn_id
        ):
            raise ValueError("The persisted evidence recipe or binding does not match the replay")

    def _validate_lesson_thread(
        self,
        thread: LearningThread,
        *,
        decision: LearningDecision,
        session_id: str,
        allow_completed: bool = False,
    ) -> None:
        if thread.session_id != session_id:
            raise ValueError("Learning thread belongs to another session")
        if thread.goal != decision.goal:
            raise ValueError("Learning thread goal does not match the decision")
        if thread.scope != "lesson":
            raise ValueError("Learning thread scope is not lesson")
        if thread.status is LearningThreadStatus.COMPLETED and allow_completed:
            pass
        elif thread.status is not LearningThreadStatus.ACTIVE:
            raise ValueError("Learning thread is not active")
        if not thread.path_id:
            return
        if not decision.objective_id:
            raise ValueError("Path-bound learning thread requires an objective")

        from deeptutor.learning.policy import find_knowledge_point

        progress = self._store.load(thread.path_id) if self._store is not None else None
        objective = (
            find_knowledge_point(progress, decision.objective_id)[0]
            if progress is not None
            else None
        )
        if objective is None:
            raise ValueError("Learning objective is not bound to the thread path")


__all__ = ["LearningCoordinator", "decision_payload", "learning_request_from_payload"]
