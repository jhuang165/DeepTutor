"""Payload adapter and coordinator facade for deterministic learning plans."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from deeptutor.learning.coordinator.models import LearningDecision, LearningRequest
from deeptutor.learning.coordinator.planner import ActivityPlanner
from deeptutor.learning.coordinator.scope import LLMScopeClassifier, ScopeDetector
from deeptutor.services.llm.config import LLMConfig

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
    ) -> None:
        self._planner = planner or ActivityPlanner()
        self._detector = detector

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


__all__ = ["LearningCoordinator", "decision_payload", "learning_request_from_payload"]
