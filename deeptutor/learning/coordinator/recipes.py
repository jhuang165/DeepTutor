"""Localized teaching recipe catalogs and deterministic selection."""

from __future__ import annotations

from functools import cache
from importlib import resources

import yaml

from deeptutor.learning.coordinator.models import TeachingRecipe
from deeptutor.learning.models import KnowledgeType


@cache
def _load_recipe_versions(language: str) -> dict[tuple[str, int], TeachingRecipe]:
    """Load every retained recipe version for ``language``."""
    lang = "zh" if str(language).lower().startswith("zh") else "en"
    raw = yaml.safe_load(
        resources.files(__package__).joinpath("recipes", f"{lang}.yaml").read_text("utf-8")
    )
    recipes = [TeachingRecipe.model_validate(item) for item in raw["recipes"]]
    by_version = {(recipe.id, recipe.version): recipe for recipe in recipes}
    if len(by_version) != len(recipes):
        raise ValueError("Duplicate teaching recipe id and version")
    if {recipe.knowledge_type for recipe in recipes} != set(KnowledgeType):
        raise ValueError("Teaching recipe catalog must cover every knowledge type")
    return by_version


@cache
def load_recipes(language: str) -> dict[str, TeachingRecipe]:
    """Return the latest retained recipe for each ID (English by default)."""
    by_id: dict[str, TeachingRecipe] = {}
    for recipe in _load_recipe_versions(language).values():
        current = by_id.get(recipe.id)
        if current is None or recipe.version > current.version:
            by_id[recipe.id] = recipe
    return by_id


def recipe_for_version(recipe_id: str, version: int, language: str) -> TeachingRecipe:
    """Return one exact retained recipe version for resumable activities."""
    try:
        return _load_recipe_versions(language)[(recipe_id, version)]
    except KeyError as exc:
        raise ValueError("The activity recipe version is unavailable") from exc


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
