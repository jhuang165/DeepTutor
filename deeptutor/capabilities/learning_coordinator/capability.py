"""Validated coordinator decisions as a turn-scoped teaching-loop extension."""

from __future__ import annotations

from collections.abc import Mapping
from importlib import resources
import logging
from typing import Any

from pydantic import ValidationError
import yaml

from deeptutor.capabilities.learning_coordinator.tools import (
    LearningPathDraftTool,
    LearningReportAssessmentTool,
    bounded_topic_sources,
)
from deeptutor.capabilities.protocol import PromptBlock
from deeptutor.core.context import UnifiedContext
from deeptutor.learning.coordinator.models import LearningDecision

logger = logging.getLogger(__name__)

LEARNING_COORDINATOR_NAME = "learning_coordinator"
LEARNING_COORDINATOR_TOOL_NAMES = (
    "learning_path_draft",
    "learning_report_assessment",
)


def load_learning_prompt(language: str) -> str:
    """Load the local coordinator policy, falling back to English."""

    lang = "zh" if str(language or "en").lower().startswith("zh") else "en"
    try:
        text = (
            resources.files(__package__)
            .joinpath("prompts", lang, "learning_coordinator.yaml")
            .read_text(encoding="utf-8")
        )
        parsed = yaml.safe_load(text)
    except Exception:
        logger.warning("Failed to load Learning Coordinator prompt (%s)", lang, exc_info=True)
        parsed = None
    if not isinstance(parsed, dict):
        return ""
    return str(parsed.get("policy") or "").strip()


class LearningCoordinatorLoopCapability:
    """Guide the ordinary chat loop with a server-validated learning activity."""

    name = LEARNING_COORDINATOR_NAME
    owned_tools = LEARNING_COORDINATOR_TOOL_NAMES

    def _decision(self, context: UnifiedContext) -> LearningDecision | None:
        state = context.extension_state.get(self.name)
        if not isinstance(state, Mapping):
            return None
        raw = state.get("decision")
        if raw is None:
            return None
        try:
            return LearningDecision.model_validate(raw)
        except (TypeError, ValidationError):
            logger.warning("Ignoring invalid Learning Coordinator decision")
            return None

    def is_active(self, context: UnifiedContext) -> bool:
        return self._decision(context) is not None

    def system_block(
        self,
        context: UnifiedContext,
        *,
        language: str,
        prompts: dict[str, Any],
    ) -> PromptBlock | None:
        del prompts
        decision = self._decision(context)
        if decision is None:
            return None
        policy = load_learning_prompt(language)
        if not policy:
            return None
        activity = decision.activity
        content = policy.format(
            activity_kind=activity.kind.value,
            objective=activity.objective,
            learner_action=activity.learner_action,
            help_level=activity.help_level,
            source_policy=decision.source_policy.value,
            assessment_method=activity.assessment_method,
            next_action=activity.next_action,
        )
        return PromptBlock(self.name, content)

    def augment_kwargs(
        self,
        tool_name: str,
        kwargs: dict[str, Any],
        context: UnifiedContext,
    ) -> dict[str, Any]:
        decision = self._decision(context)
        if decision is None:
            return kwargs
        if tool_name == LearningReportAssessmentTool.name:
            return {
                **kwargs,
                "_scope": decision.scope.value,
                "_record_result": lambda value: context.extension(self.name).__setitem__(
                    "result", value
                ),
            }
        if tool_name == LearningPathDraftTool.name:
            return {
                **kwargs,
                "_scope": decision.scope.value,
                "_thread_id": decision.thread_id,
                "_goal": decision.goal,
                "_language": context.language,
                "_sources": bounded_topic_sources(context),
            }
        return kwargs

    def pre_loop_seed(self, context: UnifiedContext) -> str:
        del context
        return ""


__all__ = [
    "LEARNING_COORDINATOR_NAME",
    "LEARNING_COORDINATOR_TOOL_NAMES",
    "LearningCoordinatorLoopCapability",
    "load_learning_prompt",
]
