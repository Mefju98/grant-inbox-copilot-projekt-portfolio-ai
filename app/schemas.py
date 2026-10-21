"""Validated API and pipeline contracts for synthetic grant inquiries."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class StrictModel(BaseModel):
    """Reject unknown fields so upstream model output cannot silently drift."""

    model_config = ConfigDict(extra="forbid")


class EligibilityLabel(StrEnum):
    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"
    NEEDS_REVIEW = "needs_review"


class EscalationReason(StrEnum):
    AMBIGUOUS_INPUT = "ambiguous_input"
    MISSING_INFORMATION = "missing_information"
    NO_KNOWLEDGE_MATCH = "no_knowledge_match"
    CONFLICTING_EVIDENCE = "conflicting_evidence"
    LOW_RULE_CONFIDENCE = "low_rule_confidence"
    MODEL_OR_VALIDATION_ERROR = "model_or_validation_error"
    UNSUPPORTED_MODEL_CLAIM = "unsupported_model_claim"


class InquiryRequest(StrictModel):
    """A synthetic prospect message submitted for triage."""

    inquiry_id: NonEmptyText
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=10_000)]
    received_at: str | None = None


class ExtractedFacts(StrictModel):
    company_name: str | None = None
    tax_id: str | None = None
    region: str | None = None
    industry: str | None = None
    project_summary: str | None = None
    requested_amount_pln: int | None = Field(default=None, ge=0)
    company_size: str | None = None
    facts_missing: list[NonEmptyText] = Field(default_factory=list)


class KnowledgeCitation(StrictModel):
    document_id: NonEmptyText
    title: NonEmptyText
    excerpt: NonEmptyText
    source_version: NonEmptyText


class KnowledgeDocument(StrictModel):
    document_id: NonEmptyText
    title: NonEmptyText
    source_version: NonEmptyText
    content: NonEmptyText


class TriageResult(StrictModel):
    """Machine-readable result; confidence is an uncalibrated score, not a probability."""

    inquiry_id: NonEmptyText
    eligibility: EligibilityLabel
    recommendation: str | None = None
    rationale: NonEmptyText
    extracted_facts: ExtractedFacts
    citations: list[KnowledgeCitation] = Field(default_factory=list)
    escalate: bool = False
    escalation_reasons: list[EscalationReason] = Field(default_factory=list)
    confidence_score: float | None = Field(default=None, ge=0, le=1)
    prompt_version: NonEmptyText | None = None


class ModelTriageDraft(StrictModel):
    """Untrusted model proposal; retrieval and policy checks happen downstream."""

    eligibility: EligibilityLabel
    rationale: NonEmptyText
    extracted_facts: ExtractedFacts
    confidence_score: float | None = Field(default=None, ge=0, le=1)
    recommendation: str | None = None
    citation_ids: list[NonEmptyText] = Field(default_factory=list)


class LLMCallMetrics(StrictModel):
    model: str | None = None
    prompt_version: str | None = None
    model_proposal_eligibility: EligibilityLabel | None = None
    policy_eligibility: EligibilityLabel | None = None
    latency_ms: int = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cost_usd: float | None = Field(default=None, ge=0)
    attempts: int = Field(default=0, ge=0)


class TriageAPIResponse(StrictModel):
    result: TriageResult
    llm: LLMCallMetrics


class ErrorResponse(StrictModel):
    code: NonEmptyText
    message: NonEmptyText
    inquiry_id: str | None = None
    escalate: bool = True
    escalation_reasons: list[EscalationReason] = Field(
        default_factory=lambda: [EscalationReason.MODEL_OR_VALIDATION_ERROR]
    )


class EvaluationCase(StrictModel):
    """Gold labels are authored independently before any model evaluation."""

    case_id: NonEmptyText
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    expected_eligibility: EligibilityLabel
    expected_escalation: bool
    expected_citation_ids: list[NonEmptyText] = Field(default_factory=list)


class EvaluationPrediction(StrictModel):
    """Normalized output from one evaluated system run."""

    case_id: NonEmptyText
    eligibility: EligibilityLabel
    escalate: bool
    citation_ids: list[NonEmptyText] = Field(default_factory=list)
    schema_valid: bool
    latency_ms: int | None = Field(default=None, ge=0)
    cost_usd: float | None = Field(default=None, ge=0)


class ClassMetrics(StrictModel):
    precision: float
    recall: float
    f1: float
    support: int


class EvaluationMetrics(StrictModel):
    case_count: int
    accuracy: float
    per_class: dict[str, ClassMetrics]
    confusion_matrix: dict[str, dict[str, int]]
    macro_f1: float
    schema_valid_rate: float
    citation_precision: float
    citation_recall: float
    citation_f1: float
    escalation_precision: float
    escalation_recall: float
    escalation_f1: float
    total_cost_usd: float | None = None
    average_cost_usd: float | None = None
    cost_observation_count: int
    average_latency_ms: float | None = None
    p95_latency_ms: int | None = None
    latency_observation_count: int
