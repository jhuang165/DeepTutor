from pydantic import ValidationError
import pytest

from deeptutor.learning.coordinator.models import (
    ActivityKind,
    ActivityPlan,
    LearningDecision,
    LearningScope,
    SourcePolicy,
)
from deeptutor.learning.coordinator.recipes import load_recipes, recipe_for_knowledge_type
from deeptutor.learning.models import KnowledgeType


def test_activity_plan_rejects_help_outside_ladder() -> None:
    with pytest.raises(ValidationError):
        ActivityPlan(
            kind=ActivityKind.PREDICTION,
            objective="Predict the spectrum",
            learner_action="Choose a spectrum",
            help_level=5,
        )


def test_decision_requires_approval_only_for_path() -> None:
    plan = ActivityPlan(
        kind=ActivityKind.EXPLANATION,
        objective="Explain one fact",
        learner_action="Read and ask a follow-up",
    )
    with pytest.raises(ValidationError):
        LearningDecision(
            scope=LearningScope.ANSWER,
            route="chat",
            goal="One fact",
            activity=plan,
            reason="Narrow request",
            confidence=1.0,
            requires_approval=True,
            source_policy=SourcePolicy.OPEN,
        )


def test_recipe_catalogs_have_identical_ids() -> None:
    assert set(load_recipes("en")) == set(load_recipes("zh"))


@pytest.mark.parametrize("kind", list(KnowledgeType))
def test_each_knowledge_type_has_a_recipe(kind: KnowledgeType) -> None:
    recipe = recipe_for_knowledge_type(kind, "en")
    assert recipe.knowledge_type == kind
    assert recipe.activity_sequence
    assert recipe.evidence_requirements
