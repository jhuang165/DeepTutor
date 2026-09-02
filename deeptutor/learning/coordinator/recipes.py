"""Localized teaching recipe catalogs and deterministic selection."""

from __future__ import annotations

from functools import cache
from importlib import resources

import yaml

from deeptutor.learning.coordinator.models import TeachingRecipe
from deeptutor.learning.models import KnowledgeType


@cache
def load_recipes(language: str) -> dict[str, TeachingRecipe]:
    """Load and validate the catalog for ``language`` (English by default)."""
    lang = "zh" if str(language).lower().startswith("zh") else "en"
    raw = yaml.safe_load(
        resources.files(__package__).joinpath("recipes", f"{lang}.yaml").read_text("utf-8")
    )
    recipes = [TeachingRecipe.model_validate(item) for item in raw["recipes"]]
    by_id = {recipe.id: recipe for recipe in recipes}
    if len(by_id) != len(recipes):
        raise ValueError("Duplicate teaching recipe id")
    if {recipe.knowledge_type for recipe in recipes} != set(KnowledgeType):
        raise ValueError("Teaching recipe catalog must cover every knowledge type")
    return by_id


def recipe_for_knowledge_type(knowledge_type: KnowledgeType, language: str) -> TeachingRecipe:
    """Return the localized recipe assigned to one knowledge type."""
    return next(
        recipe
        for recipe in load_recipes(language).values()
        if recipe.knowledge_type is knowledge_type
    )


class TeachingStrategist:
    """Select deterministic, recipe-owned learning activities."""

    def select(self, knowledge_type: KnowledgeType, language: str) -> TeachingRecipe:
        return recipe_for_knowledge_type(knowledge_type, language)
