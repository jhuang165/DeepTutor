"""Validated, serializable contracts for Learning Coordinator decisions."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from deeptutor.learning.models import KnowledgeType


class LearningScope(str, Enum):
    ANSWER = "answer"
    LESSON = "lesson"
    PATH = "path"


class LearningQueueReason(str, Enum):
    UNFINISHED_ATTEMPT = "unfinished_attempt"
    RESUME_LESSON = "resume_lesson"
    DUE_REVIEW = "due_review"
    NEEDS_TRANSFER = "needs_transfer"
    CONTINUE_PATH = "continue_path"


class LearningQueueReasonData(BaseModel):
    """Locale-neutral values interpolated by the authenticated client."""

    objective: str = ""
    goal: str = ""
    path_name: str = ""
    answer_state: Literal["", "pending_answer", "pending_grading"] = ""


class LearningQueueItem(BaseModel):
    thread_id: str = ""
    path_id: str = ""
    objective_id: str = ""
    activity: dict[str, Any] = Field(default_factory=dict)
    reason: LearningQueueReason
    reason_data: LearningQueueReasonData = Field(default_factory=LearningQueueReasonData)
    priority: int
    due_at: float | None = None


class ActivityKind(str, Enum):
    EXPLANATION = "explanation"
    PREDICTION = "prediction"
    WORKED_EXAMPLE = "worked_example"
    GUIDED_ATTEMPT = "guided_attempt"
    RETRIEVAL = "retrieval"
    TEACH_BACK = "teach_back"
    EVIDENCE_COMPARISON = "evidence_comparison"
    PROJECT_STEP = "project_step"
    REVIEW = "review"
    VISUAL_EXPLORATION = "visual_exploration"


class SourcePolicy(str, Enum):
    ATTACHED_ONLY = "attached_only"
    ATTACHED_PREFERRED = "attached_preferred"
    OPEN = "open"


class ActivityPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ActivityKind
    objective: str = Field(max_length=2_000)
    learner_action: str = Field(max_length=4_000)
    knowledge_type: KnowledgeType = KnowledgeType.CONCEPT
    recipe_id: str = "concept-transfer"
    recipe_version: int = Field(default=1, ge=1)
    recipe_step: int = Field(default=0, ge=0)
    help_level: int = Field(default=0, ge=0, le=4)
    source_refs: list[str] = Field(default_factory=list)
    assessment_method: str = "none"
    independent_required: bool = False
    transfer_required: bool = False
    next_action: str = ""


class LearningRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(max_length=100_000)
    requested_capability: str = Field(default="chat", max_length=64)
    language: str = "en"
    has_sources: bool = False
    attached_only_requested: bool = False
    course_id: str = ""
    mastery_path_id: str = ""
    workspace_mode: str = ""
    direct_answer_requested: bool = False
    stuck_signal: bool = False
    repeated_request: bool = False
    previous_help_level: int = Field(default=0, ge=0, le=4)
    last_outcome: Literal["", "correct", "partial", "incorrect", "unassessed"] = ""
    server_next_activity: ActivityPlan | None = None


class ScopeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: LearningScope
    goal: str = Field(max_length=2_000)
    knowledge_type: KnowledgeType = KnowledgeType.CONCEPT
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(max_length=500)
    direct_answer_requested: bool = False


class LearningDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: LearningScope
    route: str = Field(max_length=64)
    goal: str = Field(max_length=2_000)
    language: Literal["en", "zh"] = "en"
    thread_id: str = Field(default="", max_length=128)
    objective_id: str = ""
    activity: ActivityPlan
    reason: str = Field(max_length=500)
    confidence: float = Field(ge=0.0, le=1.0)
    requires_approval: bool = False
    source_policy: SourcePolicy = SourcePolicy.OPEN

    @model_validator(mode="after")
    def validate_approval(self) -> LearningDecision:
        if self.requires_approval != (self.scope is LearningScope.PATH):
            raise ValueError("Only path scope requires approval")
        return self


class CapabilityLearningResult(BaseModel):
    """Untrusted capability output accepted by learning finalization."""

    model_config = ConfigDict(extra="forbid")

    artifact_ref: str = ""
    assessment: dict[str, Any] | None = None
    source_refs: list[str] = Field(default_factory=list)


class RecipeActivity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ActivityKind
    assessment_method: str
    independent_required: bool
    transfer_required: bool
    learner_action: str


class TeachingRecipe(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    version: int = Field(ge=1)
    knowledge_type: KnowledgeType
    activity_sequence: list[RecipeActivity]
    evidence_requirements: list[str]
    default_route: str
    instruction: str
