"""Learning Coordinator teaching-loop contracts."""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest

from deeptutor.capabilities.learning_coordinator.capability import (
    LearningCoordinatorLoopCapability,
)
from deeptutor.capabilities.learning_coordinator.tools import (
    LearningPathDraftTool,
    LearningReportAssessmentTool,
)
from deeptutor.capabilities.partner_authoring.capability import PartnerAuthoringCapability
from deeptutor.capabilities.partner_authoring.tools import ProposePartnerTool
from deeptutor.capabilities.partner_group.capability import PartnerGroupCapability
from deeptutor.capabilities.partner_group.tools import InvokeOtherTool
from deeptutor.core.context import UnifiedContext
from deeptutor.core.stream import StreamEventType
from deeptutor.core.tool_protocol import BaseTool, ToolResult
from deeptutor.learning.coordinator.models import (
    ActivityKind,
    ActivityPlan,
    CapabilityLearningResult,
    LearningDecision,
    LearningScope,
)
from deeptutor.learning.evidence import ValidatedAssessment
from deeptutor.learning.storage import LearningStore
from deeptutor.runtime.agentic.tool_dispatch import dispatch_tool_calls
from deeptutor.runtime.registry.tool_registry import ToolRegistry
from deeptutor.runtime.stream_bus import StreamBus


class _DefinitionCapturingRegistry:
    """Execute a real tool schema without unrelated durable side effects."""

    def __init__(self, tool: BaseTool) -> None:
        self.tool = tool
        self.executed: dict[str, Any] | None = None

    def get(self, name: str) -> BaseTool | None:
        return self.tool if name == self.tool.name else None

    async def execute(self, tool_name: str, **kwargs: Any) -> ToolResult:
        assert tool_name == self.tool.name
        self.executed = kwargs
        return ToolResult(content="executed")


def _decision(
    *, scope: LearningScope = LearningScope.LESSON, help_level: int = 0
) -> LearningDecision:
    return LearningDecision(
        scope=scope,
        route="chat",
        goal="Understand entropy",
        thread_id="thread-1",
        activity=ActivityPlan(
            kind=ActivityKind.TEACH_BACK,
            objective="Explain entropy in your own words.",
            learner_action="Explain why entropy changes in the isolated system.",
            help_level=help_level,
            assessment_method="open_rubric",
            next_action="Compare the explanation with a new example.",
        ),
        reason="A short guided attempt is appropriate.",
        confidence=0.9,
        requires_approval=scope is LearningScope.PATH,
    )


def _context_with_decision(
    *, scope: LearningScope = LearningScope.LESSON, help_level: int = 0
) -> UnifiedContext:
    context = UnifiedContext(active_capability="chat", language="en")
    context.extension("learning_coordinator")["decision"] = _decision(
        scope=scope,
        help_level=help_level,
    ).model_dump(mode="json")
    return context


def test_extension_is_inactive_without_decision() -> None:
    assert LearningCoordinatorLoopCapability().is_active(UnifiedContext()) is False


def test_invalid_decision_warning_is_sanitized(caplog: pytest.LogCaptureFixture) -> None:
    secret_marker = "secret-learning-decision-marker"
    context = UnifiedContext()
    context.extension("learning_coordinator")["decision"] = {
        "scope": "lesson",
        "route": secret_marker,
    }

    with caplog.at_level(
        logging.WARNING,
        logger="deeptutor.capabilities.learning_coordinator.capability",
    ):
        assert LearningCoordinatorLoopCapability().is_active(context) is False

    assert caplog.messages == ["Ignoring invalid Learning Coordinator decision"]
    assert secret_marker not in caplog.text
    assert caplog.records[0].exc_info is None


def test_extension_activates_for_valid_decision() -> None:
    context = _context_with_decision()
    extension = LearningCoordinatorLoopCapability()

    assert extension.is_active(context) is True
    assert set(extension.owned_tools) == {
        "learning_path_draft",
        "learning_report_assessment",
    }


def test_prompt_requires_direct_answer_at_help_four() -> None:
    context = _context_with_decision(help_level=4)

    block = LearningCoordinatorLoopCapability().system_block(context, language="en", prompts={})

    assert block is not None
    assert "give the complete answer in this turn" in block.content.lower()


def test_assessment_tool_exposes_no_mastery_parameter() -> None:
    names = {parameter.name for parameter in LearningReportAssessmentTool().definition.parameters}

    assert "mastery" not in names
    assert names == {"outcome", "rubric", "cited_evidence", "uncertainty"}


def test_path_draft_schema_is_an_empty_closed_object() -> None:
    parameters = LearningPathDraftTool().definition.to_openai_schema()["function"]["parameters"]

    assert parameters == {
        "type": "object",
        "additionalProperties": False,
        "properties": {},
    }


def test_assessment_schema_is_closed_to_assessment_fields() -> None:
    parameters = LearningReportAssessmentTool().definition.to_openai_schema()["function"][
        "parameters"
    ]

    assert parameters["additionalProperties"] is False
    assert set(parameters["properties"]) == {
        "outcome",
        "rubric",
        "cited_evidence",
        "uncertainty",
    }


async def _draft(**_kwargs: Any) -> dict[str, Any]:
    return {
        "description": "A first thermodynamics route.",
        "modules": [
            {
                "id": "module-1",
                "name": "State functions",
                "order": 0,
                "knowledge_points": [],
            }
        ],
        "sources": [],
    }


@pytest.mark.asyncio
async def test_path_draft_persists_draft_without_path(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = LearningStore(root=tmp_path)
    monkeypatch.setattr(
        "deeptutor.capabilities.learning_coordinator.tools.generate_topic_draft", _draft
    )

    result = await LearningPathDraftTool(store=store).execute(
        _thread_id="thread-1",
        _goal="Learn thermodynamics",
        _language="en",
        _sources=[],
    )

    assert result.success is True
    proposal = result.metadata["proposal"]
    assert proposal["modules"]
    assert store.load(proposal["path_id"]) is None
    thread = store.get_learning_thread("thread-1")
    assert thread is not None
    assert thread.next_activity == proposal


@pytest.mark.asyncio
async def test_path_draft_rejects_non_path_decision(tmp_path) -> None:
    result = await LearningPathDraftTool(store=LearningStore(root=tmp_path)).execute(
        _scope="lesson",
        _thread_id="thread-1",
        _goal="Understand entropy",
        _language="en",
        _sources=[],
    )

    assert result.success is False


@pytest.mark.asyncio
async def test_assessment_tool_records_validated_raw_result_in_extension() -> None:
    context = _context_with_decision()
    extension = LearningCoordinatorLoopCapability()
    kwargs = extension.augment_kwargs(
        "learning_report_assessment",
        {
            "outcome": "partial",
            "rubric": [{"id": "mechanism", "passed": True}],
            "cited_evidence": ["entropy changes"],
            "uncertainty": 0.2,
        },
        context,
    )

    result = await LearningReportAssessmentTool().execute(**kwargs)

    assert result.success is True
    raw_result = context.extension("learning_coordinator")["result"]
    assert raw_result == result.metadata["result"]
    validated = CapabilityLearningResult.model_validate(raw_result)
    assessment = ValidatedAssessment.model_validate(validated.assessment)
    assert assessment.outcome == "partial"


def test_private_runtime_values_are_not_model_parameters() -> None:
    context = _context_with_decision(scope=LearningScope.PATH)
    extension = LearningCoordinatorLoopCapability()
    model_parameters = {
        parameter.name for parameter in LearningPathDraftTool().definition.parameters
    }
    injected = extension.augment_kwargs("learning_path_draft", {}, context)

    assert model_parameters == set()
    assert set(injected) == {"_scope", "_thread_id", "_goal", "_language", "_sources"}
    assert "mastery" not in injected


def test_owned_tools_are_resolvable_by_the_chat_tool_registry() -> None:
    registry = ToolRegistry()
    registry.load_builtins()

    assert isinstance(registry.get("learning_path_draft"), LearningPathDraftTool)
    assert isinstance(registry.get("learning_report_assessment"), LearningReportAssessmentTool)


@pytest.mark.asyncio
async def test_dispatcher_rejects_unknown_assessment_arguments_before_execution() -> None:
    context = _context_with_decision()
    capability = LearningCoordinatorLoopCapability()
    registry = ToolRegistry()
    registry.load_builtins()

    outcome = await dispatch_tool_calls(
        tool_calls=[
            {
                "id": "call-1",
                "name": "learning_report_assessment",
                "arguments": json.dumps(
                    {
                        "outcome": "partial",
                        "rubric": [{"id": "mechanism", "passed": True}],
                        "cited_evidence": ["entropy changes"],
                        "uncertainty": 0.2,
                        "mastery": 1,
                    }
                ),
            }
        ],
        context=context,
        stream=StreamBus(),
        source="chat",
        stage="responding",
        iteration_index=0,
        registry=registry,
        kwarg_augmenter=capability.augment_kwargs,
    )

    assert outcome.tool_messages[0]["content"] == (
        "`learning_report_assessment` could not be dispatched because it contains "
        "unsupported model arguments. Re-emit using only the declared schema fields."
    )
    assert "result" not in context.extension("learning_coordinator")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("capability", "context", "tool", "arguments", "private_key"),
    [
        (
            PartnerAuthoringCapability(),
            UnifiedContext(active_capability="partner_authoring", user_message="Create a tutor"),
            ProposePartnerTool(),
            {
                "name": "Ada",
                "description": "A precise tutor.",
                "soul": "Teach with examples.",
                "language": "en",
                "emoji": "📐",
                "color": "#112233",
            },
            "_partner_authoring_context",
        ),
        (
            PartnerGroupCapability(),
            UnifiedContext(
                metadata={
                    "partner_group": {
                        "allow_invoke_other": True,
                        "self_id": "ada",
                        "members": [{"partner_id": "ada"}, {"partner_id": "bob"}],
                    }
                }
            ),
            InvokeOtherTool(),
            {"target_partner_id": "bob", "question": "What assumption needs checking?"},
            "_partner_group_context",
        ),
    ],
    ids=["partner-authoring", "partner-group"],
)
async def test_dispatcher_accepts_closed_partner_tools_after_private_augmentation(
    capability: Any,
    context: UnifiedContext,
    tool: BaseTool,
    arguments: dict[str, Any],
    private_key: str,
) -> None:
    assert capability.is_active(context)
    registry = _DefinitionCapturingRegistry(tool)

    outcome = await dispatch_tool_calls(
        tool_calls=[{"id": "call-1", "name": tool.name, "arguments": json.dumps(arguments)}],
        context=context,
        stream=StreamBus(),
        source="chat",
        stage="responding",
        iteration_index=0,
        registry=registry,
        kwarg_augmenter=capability.augment_kwargs,
    )

    assert outcome.tool_messages[0]["content"] == "executed"
    assert registry.executed is not None
    assert registry.executed[private_key] is context


@pytest.mark.asyncio
async def test_dispatcher_sanitizes_closed_schema_rejection_everywhere(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret_key = "UNTRUSTED_KEY_SECRET"
    secret_value = "UNTRUSTED_VALUE_SECRET"
    context = _context_with_decision()
    stream = StreamBus()
    registry = ToolRegistry()
    registry.load_builtins()

    with caplog.at_level(logging.WARNING, logger="deeptutor.runtime.agentic.tool_dispatch"):
        outcome = await dispatch_tool_calls(
            tool_calls=[
                {
                    "id": "call-1",
                    "name": "learning_report_assessment",
                    "arguments": json.dumps(
                        {
                            "outcome": "partial",
                            "rubric": [{"id": "mechanism", "passed": True}],
                            "cited_evidence": ["entropy changes"],
                            "uncertainty": 0.2,
                            secret_key: secret_value,
                        }
                    ),
                }
            ],
            context=context,
            stream=stream,
            source="chat",
            stage="responding",
            iteration_index=0,
            registry=registry,
            kwarg_augmenter=LearningCoordinatorLoopCapability().augment_kwargs,
        )

    assert outcome.tool_messages[0]["content"] == (
        "`learning_report_assessment` could not be dispatched because it contains "
        "unsupported model arguments. Re-emit using only the declared schema fields."
    )
    assert outcome.tool_metadata_by_id["call-1"] == {"error": "unexpected_arguments"}
    assert secret_key not in caplog.text
    assert secret_value not in caplog.text
    emitted = [event.to_dict() for event in stream._history]
    rendered = json.dumps(emitted, default=str)
    assert secret_key not in rendered
    assert secret_value not in rendered
    call_event = next(event for event in stream._history if event.type is StreamEventType.TOOL_CALL)
    assert set((call_event.metadata or {}).get("args") or {}) == {
        "outcome",
        "rubric",
        "cited_evidence",
        "uncertainty",
    }
