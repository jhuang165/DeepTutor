"""Structured teaching-loop tools with runtime-owned coordinator bindings."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import ValidationError

from deeptutor.core.context import UnifiedContext
from deeptutor.core.tool_protocol import BaseTool, ToolDefinition, ToolParameter, ToolResult
from deeptutor.learning.coordinator.models import CapabilityLearningResult
from deeptutor.learning.evidence import ValidatedAssessment
from deeptutor.learning.models import (
    LearningThread,
    LearningThreadStatus,
    TopicSource,
    TopicSourceKind,
)
from deeptutor.learning.storage import LearningStore
from deeptutor.learning.topic_generation import generate_topic_draft

_MAX_TOPIC_SOURCES = 12


def _language(value: Any) -> str:
    return "zh" if str(value or "en").lower().startswith("zh") else "en"


def _proposal_path_id(thread_id: str) -> str:
    return "draft-" + hashlib.sha256(thread_id.encode("utf-8")).hexdigest()[:24]


def _topic_sources(value: Any) -> list[TopicSource]:
    if not isinstance(value, list):
        return []
    sources: list[TopicSource] = []
    for item in value[:_MAX_TOPIC_SOURCES]:
        try:
            source = item if isinstance(item, TopicSource) else TopicSource.model_validate(item)
        except ValidationError:
            continue
        sources.append(source)
    return sources


def bounded_topic_sources(context: UnifiedContext) -> list[TopicSource]:
    """Project stable source identifiers only; never inject source bodies."""

    sources: list[TopicSource] = []
    for index, name in enumerate(context.knowledge_bases[:_MAX_TOPIC_SOURCES]):
        normalized = str(name or "").strip()
        if not normalized:
            continue
        sources.append(
            TopicSource(
                id=f"kb-{len(sources)}",
                kind=TopicSourceKind.KNOWLEDGE_BASE,
                source_id=normalized,
                label=normalized,
                position=len(sources),
            )
        )
    for attachment in context.attachments:
        if len(sources) >= _MAX_TOPIC_SOURCES:
            break
        source_id = str(attachment.id or attachment.filename or "").strip()
        if not source_id:
            continue
        sources.append(
            TopicSource(
                id=f"attachment-{len(sources)}",
                kind=TopicSourceKind.FILE,
                source_id=source_id,
                label=str(attachment.filename or source_id).strip(),
                position=len(sources),
            )
        )
    return sources


class _DefinitionTool(BaseTool):
    """Compatibility accessor used by existing capability tool tests."""

    @property
    def definition(self) -> ToolDefinition:
        return self.get_definition()


class LearningPathDraftTool(_DefinitionTool):
    """Generate a reversible path proposal without creating a mastery path."""

    name = "learning_path_draft"

    def __init__(self, *, store: LearningStore | None = None) -> None:
        self._store = store

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=(
                "Generate an editable learning-path proposal from the server-approved goal and "
                "attached source identifiers. Call only for a path proposal; it never creates "
                "a mastery path before learner approval."
            ),
            parameters=[],
            raw_parameters={
                "type": "object",
                "additionalProperties": False,
                "properties": {},
            },
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        scope = str(kwargs.get("_scope") or "path").strip()
        if scope != "path":
            return ToolResult(
                content="Learning-path drafts are available only for a path decision.",
                success=False,
            )
        thread_id = str(kwargs.get("_thread_id") or "").strip()
        goal = str(kwargs.get("_goal") or "").strip()
        if not thread_id or not goal:
            return ToolResult(
                content="A server-owned learning thread and goal are required for a path draft.",
                success=False,
            )
        sources = _topic_sources(kwargs.get("_sources"))
        proposal = dict(
            await generate_topic_draft(
                name=goal[:120],
                goal=goal[:2_000],
                sources=sources,
                language=_language(kwargs.get("_language")),
            )
        )
        proposal["path_id"] = _proposal_path_id(thread_id)

        store = self._store or LearningStore()
        thread = store.get_learning_thread(thread_id)
        if thread is None:
            store.create_learning_thread(
                LearningThread(
                    thread_id=thread_id,
                    session_id="",
                    scope="path",
                    goal=goal[:2_000],
                    status=LearningThreadStatus.DRAFT,
                    source_refs=[source.id for source in sources],
                )
            )
        elif thread.scope != "path":
            return ToolResult(
                content="The server-owned learning thread is not a path draft.",
                success=False,
            )
        store.set_learning_thread_next_activity(thread_id, proposal)
        return ToolResult(
            content=json.dumps(proposal, ensure_ascii=False),
            metadata={"proposal": proposal},
        )


class LearningReportAssessmentTool(_DefinitionTool):
    """Pass a validated open assessment to the server-owned turn finalizer."""

    name = "learning_report_assessment"

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=(
                "Report an assessment of learner work only after the learner supplied assessable "
                "work. Cite exact learner evidence and state uncertainty; never report mastery."
            ),
            parameters=[
                ToolParameter(
                    name="outcome",
                    type="string",
                    description="Assessment outcome for this learner attempt.",
                    enum=["correct", "partial", "incorrect"],
                ),
                ToolParameter(
                    name="rubric",
                    type="array",
                    description="Criterion results as objects with id and passed.",
                    items={
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "passed": {"type": "boolean"},
                        },
                        "required": ["id", "passed"],
                        "additionalProperties": False,
                    },
                ),
                ToolParameter(
                    name="cited_evidence",
                    type="array",
                    description="Exact short excerpts from the learner's submitted work.",
                    items={"type": "string"},
                ),
                ToolParameter(
                    name="uncertainty",
                    type="number",
                    description="Assessment uncertainty from 0.0 to 1.0.",
                ),
            ],
            raw_parameters={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "outcome": {
                        "type": "string",
                        "description": "Assessment outcome for this learner attempt.",
                        "enum": ["correct", "partial", "incorrect"],
                    },
                    "rubric": {
                        "type": "array",
                        "description": "Criterion results as objects with id and passed.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "passed": {"type": "boolean"},
                            },
                            "required": ["id", "passed"],
                            "additionalProperties": False,
                        },
                    },
                    "cited_evidence": {
                        "type": "array",
                        "description": "Exact short excerpts from the learner's submitted work.",
                        "items": {"type": "string"},
                    },
                    "uncertainty": {
                        "type": "number",
                        "description": "Assessment uncertainty from 0.0 to 1.0.",
                    },
                },
                "required": ["outcome", "rubric", "cited_evidence", "uncertainty"],
            },
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        if str(kwargs.get("_scope") or "").strip() != "lesson":
            return ToolResult(
                content="Learning assessments are available only for a lesson attempt.",
                success=False,
            )
        callback = kwargs.get("_record_result")
        if not callable(callback):
            return ToolResult(
                content="The server-owned learning assessment callback is unavailable.",
                success=False,
            )
        try:
            assessment = ValidatedAssessment.model_validate(
                {
                    "outcome": kwargs.get("outcome"),
                    "rubric": kwargs.get("rubric"),
                    "cited_evidence": kwargs.get("cited_evidence"),
                    "uncertainty": kwargs.get("uncertainty"),
                }
            )
        except ValidationError:
            return ToolResult(
                content="The assessment must include a valid outcome, rubric, cited evidence, and uncertainty.",
                success=False,
            )
        result = CapabilityLearningResult(assessment=assessment.model_dump(mode="json")).model_dump(
            mode="json"
        )
        record_result = callback
        record_result(result)
        return ToolResult(
            content=json.dumps(result, ensure_ascii=False),
            metadata={"result": result},
        )


LEARNING_COORDINATOR_TOOL_TYPES: tuple[type[BaseTool], ...] = (
    LearningPathDraftTool,
    LearningReportAssessmentTool,
)

__all__ = [
    "LEARNING_COORDINATOR_TOOL_TYPES",
    "LearningPathDraftTool",
    "LearningReportAssessmentTool",
    "bounded_topic_sources",
]
