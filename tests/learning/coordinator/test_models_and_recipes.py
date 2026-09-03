import copy

from pydantic import ValidationError
import pytest
import yaml

from deeptutor.learning.coordinator.models import (
    ActivityKind,
    ActivityPlan,
    LearningDecision,
    LearningScope,
    SourcePolicy,
)
import deeptutor.learning.coordinator.recipes as recipe_module
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


def test_catalog_retains_old_versions_while_latest_view_selects_newest(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [recipe.model_dump(mode="json") for recipe in load_recipes("en").values()]
    concept_v2 = copy.deepcopy(next(row for row in rows if row["id"] == "concept-transfer"))
    concept_v2["version"] = 2
    concept_v2["activity_sequence"][0]["learner_action"] = "Start with the v2 prediction."
    rows.append(concept_v2)
    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    (recipes_dir / "en.yaml").write_text(
        yaml.safe_dump({"recipes": rows}, sort_keys=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(recipe_module.resources, "files", lambda _package: tmp_path)
    recipe_module.load_recipes.cache_clear()
    versioned_loader = getattr(recipe_module, "_load_recipe_versions", None)
    if versioned_loader is not None:
        versioned_loader.cache_clear()

    try:
        latest = recipe_module.load_recipes("en")
        retained_v1 = recipe_module.recipe_for_version("concept-transfer", 1, "en")
        retained_v2 = recipe_module.recipe_for_version("concept-transfer", 2, "en")

        assert latest["concept-transfer"].version == 2
        assert retained_v1.version == 1
        assert retained_v1.activity_sequence[0].learner_action == (
            "Make a prediction before seeing the explanation."
        )
        assert retained_v2.version == 2
        assert retained_v2.activity_sequence[0].learner_action == "Start with the v2 prediction."
    finally:
        recipe_module.load_recipes.cache_clear()
        versioned_loader = getattr(recipe_module, "_load_recipe_versions", None)
        if versioned_loader is not None:
            versioned_loader.cache_clear()
