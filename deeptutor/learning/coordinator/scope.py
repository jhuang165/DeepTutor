"""Deterministic-first scope detection for Learning Coordinator requests."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol

from deeptutor.learning.coordinator.models import LearningRequest, LearningScope, ScopeResult
from deeptutor.learning.coordinator.prompts import load_scope_prompt
from deeptutor.learning.models import KnowledgeType
from deeptutor.services.llm.client import LLMClient
from deeptutor.services.llm.config import LLMConfig
from deeptutor.utils.json_parser import parse_json_response


class StructuredScopeClassifier(Protocol):
    """An isolated service capable of returning a validated scope decision."""

    def classify(self, request: LearningRequest) -> Awaitable[ScopeResult]: ...


class LLMScopeClassifier:
    """Call the LLM with only the current request and a bounded JSON contract."""

    def __init__(self, language: str, *, config: LLMConfig) -> None:
        self.language = language
        self._client = LLMClient(config, configure_env=False)

    async def classify(self, request: LearningRequest) -> ScopeResult:
        raw = await self._client.complete(
            request.message,
            system_prompt=load_scope_prompt(self.language),
            temperature=0,
            max_tokens=300,
            response_format={"type": "json_object"},
        )
        return ScopeResult.model_validate(parse_json_response(raw, fallback=None))


_DIRECT_ANSWER_PHRASES = (
    "just tell me the answer",
    "just tell me",
    "tell me the answer",
    "answer only",
    "give me the answer",
    "直接告诉我答案",
    "只要答案",
    "直接给答案",
)
_BROAD_PATH_PHRASES = (
    "from scratch",
    "complete course",
    "learning path",
    "entire field",
    "整个领域",
    "系统学习",
)
_MEMORY_PHRASES = ("define", "definition", "recall", "name", "定义", "是什么", "叫做")
_PROCEDURE_PHRASES = (
    "solve",
    "calculate",
    "compute",
    "how to",
    "怎么做",
    "如何",
    "计算",
    "求解",
)
_DESIGN_PHRASES = (
    "design",
    "critique",
    "interpret",
    "compare",
    "设计",
    "批判",
    "解读",
    "比较",
)
_NARROW_REQUEST_PHRASES = (
    "what is",
    "define",
    "definition",
    "calculate",
    "compute",
    "translate",
    "short answer",
    "是什么",
    "定义",
    "计算",
    "翻译",
    "简答",
)
_NARROW_REQUEST_PREFIXES = ("who ", "when ", "where ")
_NARROW_CHINESE_FACT_PHRASES = ("谁", "什么时候", "在哪里", "何时", "何地")
_TEACHING_PHRASES = (
    "help me understand",
    "teach me",
    "walk me through",
    "why does",
    "帮我理解",
    "讲解",
)
_TEACHING_REQUEST_PREFIXES = ("explain ", "please explain ", "show me how ")
_TEACHING_CHINESE_PREFIXES = ("请解释", "解释一下", "解释")


def _normalized_message(request: LearningRequest) -> str:
    return request.message.casefold().strip()


def _goal(request: LearningRequest) -> str:
    return request.message.strip()[:2_000] or "Learning request"


def _contains_any(message: str, phrases: tuple[str, ...]) -> bool:
    return any(phrase in message for phrase in phrases)


def _deterministic_knowledge_type(message: str) -> KnowledgeType:
    if _contains_any(message, _MEMORY_PHRASES):
        return KnowledgeType.MEMORY
    if _contains_any(message, _PROCEDURE_PHRASES):
        return KnowledgeType.PROCEDURE
    if _contains_any(message, _DESIGN_PHRASES):
        return KnowledgeType.DESIGN
    return KnowledgeType.CONCEPT


def _is_narrow_request(message: str) -> bool:
    return (
        _contains_any(message, _NARROW_REQUEST_PHRASES)
        or message.startswith(_NARROW_REQUEST_PREFIXES)
        or _contains_any(message, _NARROW_CHINESE_FACT_PHRASES)
    )


def _is_explicit_teaching_request(message: str) -> bool:
    return (
        _contains_any(message, _TEACHING_PHRASES)
        or message.startswith(_TEACHING_REQUEST_PREFIXES)
        or message.startswith(_TEACHING_CHINESE_PREFIXES)
    )


class ScopeDetector:
    """Resolve obvious scope signals before consulting an optional classifier."""

    def __init__(
        self,
        classifier: StructuredScopeClassifier
        | Callable[[LearningRequest], Awaitable[ScopeResult]]
        | None = None,
    ) -> None:
        self._classifier = classifier

    async def detect(self, request: LearningRequest) -> ScopeResult:
        message = _normalized_message(request)
        goal = _goal(request)

        if request.requested_capability != "chat":
            return self._result(LearningScope.ANSWER, goal, reason="explicit_capability")

        if request.course_id or request.mastery_path_id or request.workspace_mode:
            return self._result(LearningScope.LESSON, goal, reason="bound_learning_context")

        if request.direct_answer_requested or _contains_any(message, _DIRECT_ANSWER_PHRASES):
            return self._result(
                LearningScope.ANSWER,
                goal,
                reason="direct_answer_requested",
                direct_answer_requested=True,
            )

        if _contains_any(message, _BROAD_PATH_PHRASES):
            return self._result(LearningScope.PATH, goal, reason="broad_learning_path")

        knowledge_type = _deterministic_knowledge_type(message)

        if _is_narrow_request(message):
            return self._result(
                LearningScope.ANSWER,
                goal,
                knowledge_type=knowledge_type,
                reason="narrow_request",
            )

        if _is_explicit_teaching_request(message):
            return self._result(
                LearningScope.LESSON,
                goal,
                knowledge_type=knowledge_type,
                reason="explicit_teaching_request",
            )

        if self._classifier is None:
            return self._fallback(goal, knowledge_type)

        try:
            result = ScopeResult.model_validate(await self._classify(request))
            if result.confidence < 0.65:
                return self._fallback(goal, knowledge_type)
        except Exception:
            return self._fallback(goal, knowledge_type)
        return result

    async def _classify(self, request: LearningRequest) -> ScopeResult:
        if callable(self._classifier):
            return await self._classifier(request)
        if self._classifier is None:
            raise RuntimeError("No structured scope classifier is configured")
        return await self._classifier.classify(request)

    @staticmethod
    def _result(
        scope: LearningScope,
        goal: str,
        *,
        reason: str,
        knowledge_type: KnowledgeType = KnowledgeType.CONCEPT,
        direct_answer_requested: bool = False,
    ) -> ScopeResult:
        return ScopeResult(
            scope=scope,
            goal=goal,
            knowledge_type=knowledge_type,
            confidence=1.0,
            reason=reason,
            direct_answer_requested=direct_answer_requested,
        )

    @staticmethod
    def _fallback(goal: str, knowledge_type: KnowledgeType) -> ScopeResult:
        return ScopeResult(
            scope=LearningScope.ANSWER,
            goal=goal,
            knowledge_type=knowledge_type,
            confidence=0.0,
            reason="classifier_fallback",
        )
