"""Deterministic validation and gate decisions for coordinator evidence."""

from __future__ import annotations

from typing import Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from deeptutor.learning.models import EvidenceOutcome, EvidenceRecord, KnowledgeType


class RubricCriterionResult(BaseModel):
    """One assessor rubric result accepted from an open assessment payload."""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1)
    passed: bool


class ValidatedAssessment(BaseModel):
    """The narrow, evidence-grounded subset of an open assessment result."""

    model_config = ConfigDict(extra="ignore")

    outcome: Literal["correct", "partial", "incorrect"]
    rubric: list[RubricCriterionResult]
    cited_evidence: list[str]
    uncertainty: float = Field(ge=0.0, le=1.0)


def validate_open_assessment(
    payload: object, learner_response: str
) -> ValidatedAssessment | None:
    """Return an assessment only when its claimed evidence is learner-grounded."""

    try:
        result = ValidatedAssessment.model_validate(payload)
    except ValidationError:
        return None

    normalized_response = " ".join(str(learner_response).casefold().split())
    citations = [" ".join(item.casefold().split()) for item in result.cited_evidence]
    if (
        result.uncertainty > 0.5
        or not result.rubric
        or not citations
        or any(not citation or citation not in normalized_response for citation in citations)
    ):
        return None
    return result


def _has_two_retrievals_separated_by(records: Sequence[EvidenceRecord], *, seconds: float) -> bool:
    retrieval_times = sorted(
        record.created_at
        for record in records
        if record.activity_kind in {"retrieval", "review"}
    )
    return any(
        later - earlier >= seconds
        for index, earlier in enumerate(retrieval_times)
        for later in retrieval_times[index + 1 :]
    )


def _has_independent_solution(records: Sequence[EvidenceRecord]) -> bool:
    return any(
        record.activity_kind in {"guided_attempt", "solution", "independent_solution"}
        for record in records
    )


def _has_project_artifact(records: Sequence[EvidenceRecord]) -> bool:
    return any(
        record.activity_kind in {"project", "project_step", "project_artifact"}
        and bool(record.artifact_ref or record.response_ref)
        for record in records
    )


def _has_independent_critique(records: Sequence[EvidenceRecord]) -> bool:
    return any(
        record.activity_kind in {"critique", "independent_critique", "evidence_comparison"}
        for record in records
    )


def evidence_gate(kind: KnowledgeType, records: Sequence[EvidenceRecord]) -> bool:
    """Determine whether authoritative coordinator evidence clears a mastery gate."""

    valid = [
        record
        for record in records
        if record.removed_at is None and record.outcome is EvidenceOutcome.CORRECT
    ]
    independent = [record for record in valid if record.independent and record.help_level <= 2]
    if kind is KnowledgeType.MEMORY:
        return _has_two_retrievals_separated_by(independent, seconds=20 * 60 * 60)
    if kind is KnowledgeType.CONCEPT:
        return any(
            record.activity_kind == "teach_back" or record.transfer for record in independent
        )
    if kind is KnowledgeType.PROCEDURE:
        return _has_independent_solution(independent) and any(
            record.transfer for record in independent
        )
    return _has_project_artifact(valid) and _has_independent_critique(independent)


__all__ = [
    "RubricCriterionResult",
    "ValidatedAssessment",
    "evidence_gate",
    "validate_open_assessment",
]
