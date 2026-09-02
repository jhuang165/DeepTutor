"""Deterministic selection of one learner activity per coordinated turn."""

from __future__ import annotations

from typing import Literal

from deeptutor.learning.coordinator.models import (
    ActivityKind,
    ActivityPlan,
    LearningDecision,
    LearningRequest,
    LearningScope,
    ScopeResult,
    SourcePolicy,
    TeachingRecipe,
)
from deeptutor.learning.coordinator.recipes import TeachingStrategist, load_recipes

Outcome = Literal["correct", "partial", "incorrect", "unassessed"]


ROUTE_PREFERENCES: dict[ActivityKind, tuple[str, ...]] = {
    ActivityKind.EXPLANATION: ("chat",),
    ActivityKind.PREDICTION: ("mastery_path", "chat"),
    ActivityKind.WORKED_EXAMPLE: ("deep_solve", "chat"),
    ActivityKind.GUIDED_ATTEMPT: ("mastery_path", "chat"),
    ActivityKind.RETRIEVAL: ("mastery_path", "chat"),
    ActivityKind.TEACH_BACK: ("mastery_path", "chat"),
    ActivityKind.EVIDENCE_COMPARISON: ("chat",),
    ActivityKind.PROJECT_STEP: ("chat",),
    ActivityKind.REVIEW: ("mastery_path", "chat"),
    ActivityKind.VISUAL_EXPLORATION: ("visualize", "math_animator", "chat"),
}


def _normalized_language(language: str) -> Literal["en", "zh"]:
    return "zh" if str(language).lower().startswith("zh") else "en"


def _normalized_goal(goal: str) -> str:
    return " ".join(goal.split())[:2_000] or "Learning request"


def _help_level(request: LearningRequest) -> int:
    if request.direct_answer_requested:
        return 4
    needs_more = (
        request.stuck_signal or request.repeated_request or request.last_outcome == "incorrect"
    )
    return min(3, max(1, request.previous_help_level + 1)) if needs_more else 0


def _source_policy(request: LearningRequest) -> SourcePolicy:
    if request.attached_only_requested:
        return SourcePolicy.ATTACHED_ONLY
    if request.has_sources:
        return SourcePolicy.ATTACHED_PREFERRED
    return SourcePolicy.OPEN


class ActivityPlanner:
    """Build one recipe-backed activity without consulting mutable state."""

    def __init__(self, strategist: TeachingStrategist | None = None) -> None:
        self._strategist = strategist or TeachingStrategist()

    def plan(
        self,
        scope: ScopeResult,
        request: LearningRequest,
        available_capabilities: set[str],
    ) -> LearningDecision:
        language = _normalized_language(request.language)
        goal = _normalized_goal(scope.goal)
        recipe = self._strategist.select(scope.knowledge_type, request.language)
        effective_request = request.model_copy(
            update={
                "language": language,
                "direct_answer_requested": (
                    request.direct_answer_requested or scope.direct_answer_requested
                ),
            }
        )

        if scope.scope is LearningScope.PATH:
            activity = self._path_activity(
                recipe,
                goal=goal,
                language=language,
                help_level=_help_level(effective_request),
            )
            route = "chat"
        else:
            step = self._validated_saved_step(
                request.server_next_activity,
                recipe=recipe,
                goal=goal,
            )
            if step is None:
                step = self._initial_step(scope.scope, recipe)
            activity = self._recipe_activity(
                recipe,
                step=step,
                goal=goal,
                help_level=_help_level(effective_request),
            )
            if request.requested_capability != "chat":
                route = request.requested_capability
            else:
                route = self._route_for(activity.kind, available_capabilities)

        return LearningDecision(
            scope=scope.scope,
            route=route,
            goal=goal,
            language=language,
            activity=activity,
            reason=scope.reason,
            confidence=scope.confidence,
            requires_approval=scope.scope is LearningScope.PATH,
            source_policy=_source_policy(request),
        )

    def next_after(self, decision: LearningDecision, outcome: Outcome) -> ActivityPlan:
        recipe = load_recipes(decision.language).get(decision.activity.recipe_id)
        if recipe is None or recipe.version != decision.activity.recipe_version:
            raise ValueError("The activity recipe version is unavailable")
        current_step = decision.activity.recipe_step
        if current_step >= len(recipe.activity_sequence):
            raise ValueError("The activity recipe step is out of bounds")
        next_step = (
            min(current_step + 1, len(recipe.activity_sequence) - 1)
            if outcome == "correct"
            else current_step
        )
        return self._recipe_activity(
            recipe,
            step=next_step,
            goal=decision.goal,
            help_level=0,
            source_refs=decision.activity.source_refs,
        )

    @staticmethod
    def _initial_step(scope: LearningScope, recipe: TeachingRecipe) -> int:
        if scope is not LearningScope.ANSWER:
            return 0
        return next(
            (
                index
                for index, activity in enumerate(recipe.activity_sequence)
                if activity.kind is ActivityKind.EXPLANATION
            ),
            0,
        )

    @staticmethod
    def _validated_saved_step(
        saved: ActivityPlan | None,
        *,
        recipe: TeachingRecipe,
        goal: str,
    ) -> int | None:
        if saved is None:
            return None
        valid = (
            saved.recipe_id == recipe.id
            and saved.recipe_version == recipe.version
            and saved.recipe_step < len(recipe.activity_sequence)
            and saved.objective == goal
        )
        return saved.recipe_step if valid else None

    @staticmethod
    def _recipe_activity(
        recipe: TeachingRecipe,
        *,
        step: int,
        goal: str,
        help_level: int,
        source_refs: list[str] | None = None,
    ) -> ActivityPlan:
        selected = recipe.activity_sequence[step]
        next_action = (
            recipe.activity_sequence[step + 1].learner_action
            if step + 1 < len(recipe.activity_sequence)
            else ""
        )
        return ActivityPlan(
            kind=selected.kind,
            objective=goal,
            learner_action=selected.learner_action,
            knowledge_type=recipe.knowledge_type,
            recipe_id=recipe.id,
            recipe_version=recipe.version,
            recipe_step=step,
            help_level=help_level,
            source_refs=list(source_refs or []),
            assessment_method=selected.assessment_method,
            independent_required=selected.independent_required,
            transfer_required=selected.transfer_required,
            next_action=next_action,
        )

    @staticmethod
    def _path_activity(
        recipe: TeachingRecipe,
        *,
        goal: str,
        language: Literal["en", "zh"],
        help_level: int,
    ) -> ActivityPlan:
        learner_action = (
            "审阅并编辑学习路径草案。"
            if language == "zh"
            else "Review and edit the proposed learning path."
        )
        return ActivityPlan(
            kind=ActivityKind.EXPLANATION,
            objective=goal,
            learner_action=learner_action,
            knowledge_type=recipe.knowledge_type,
            recipe_id=recipe.id,
            recipe_version=recipe.version,
            recipe_step=0,
            help_level=help_level,
            assessment_method="path_proposal",
            independent_required=False,
            transfer_required=False,
        )

    @staticmethod
    def _route_for(kind: ActivityKind, available_capabilities: set[str]) -> str:
        for route in ROUTE_PREFERENCES[kind]:
            if route in available_capabilities:
                return route
        raise ValueError(f"No available capability can render {kind.value}")


__all__ = ["ActivityPlanner", "Outcome", "ROUTE_PREFERENCES"]
