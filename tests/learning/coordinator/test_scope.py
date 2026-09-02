from unittest.mock import AsyncMock

import pytest

from deeptutor.learning.coordinator.models import LearningRequest, LearningScope
from deeptutor.learning.coordinator.scope import LLMScopeClassifier, ScopeDetector
from deeptutor.learning.models import KnowledgeType
from deeptutor.services.llm.config import LLMConfig


@pytest.mark.asyncio
async def test_explicit_capability_is_not_reclassified() -> None:
    """An explicit route must take precedence over message interpretation."""
    result = await ScopeDetector().detect(
        LearningRequest(message="Explain it", requested_capability="deep_solve")
    )
    assert result.scope is LearningScope.ANSWER
    assert result.confidence == 1.0


@pytest.mark.asyncio
async def test_broad_field_becomes_path() -> None:
    """A curriculum request must remain a path rather than a single lesson."""
    result = await ScopeDetector().detect(
        LearningRequest(message="Teach me undergraduate thermodynamics from scratch")
    )
    assert result.scope is LearningScope.PATH


@pytest.mark.asyncio
async def test_direct_answer_signal_is_preserved() -> None:
    """An answer request must retain its explicit-answer marker downstream."""
    result = await ScopeDetector().detect(
        LearningRequest(message="Just tell me the answer: what is 7 times 8?")
    )
    assert result.scope is LearningScope.ANSWER
    assert result.direct_answer_requested is True


@pytest.mark.asyncio
async def test_narrow_fact_does_not_call_classifier() -> None:
    """A closed fact request must avoid the external classifier entirely."""
    classifier = AsyncMock()
    result = await ScopeDetector(classifier=classifier).detect(
        LearningRequest(message="What is the derivative of sine?")
    )
    assert result.scope is LearningScope.ANSWER
    classifier.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_understanding_goal_becomes_lesson() -> None:
    """A learner's request for understanding must open a lesson."""
    result = await ScopeDetector().detect(
        LearningRequest(message="Help me understand eigenvectors")
    )
    assert result.scope is LearningScope.LESSON


@pytest.mark.asyncio
async def test_classifier_failure_falls_back_to_answer() -> None:
    """A provider outage must produce a safe answer-scoped fallback."""
    classifier = AsyncMock(side_effect=RuntimeError("provider unavailable"))
    result = await ScopeDetector(classifier=classifier).detect(
        LearningRequest(message="Eigenvectors keep appearing in my work")
    )
    assert result.scope is LearningScope.ANSWER
    assert result.reason == "classifier_fallback"


@pytest.mark.asyncio
async def test_procedure_language_selects_procedure_recipe_input() -> None:
    """A solve request must carry the procedure objective type."""
    result = await ScopeDetector().detect(
        LearningRequest(message="Show me how to solve a second-order differential equation")
    )
    assert result.knowledge_type is KnowledgeType.PROCEDURE


@pytest.mark.asyncio
async def test_llm_classifier_returns_validated_structured_result(monkeypatch) -> None:
    """The isolated adapter must validate the model's JSON result."""
    agent = LLMScopeClassifier(
        language="en",
        config=LLMConfig(model="test-model", api_key="test-key"),
    )
    monkeypatch.setattr(
        agent._client,
        "complete",
        AsyncMock(
            return_value=(
                '{"scope":"lesson","goal":"Eigenvectors","knowledge_type":"concept",'
                '"confidence":0.82,"reason":"dependency-heavy"}'
            )
        ),
    )
    result = await agent.classify(
        LearningRequest(message="I cannot form a picture of eigenvectors")
    )
    assert result.scope is LearningScope.LESSON
    assert result.confidence == 0.82
