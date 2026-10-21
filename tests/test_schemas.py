import pytest
from pydantic import ValidationError

from app.schemas import (
    EligibilityLabel,
    EscalationReason,
    ExtractedFacts,
    InquiryRequest,
    KnowledgeCitation,
    TriageResult,
)


def test_inquiry_requires_nonempty_message_and_forbids_unknown_fields():
    with pytest.raises(ValidationError):
        InquiryRequest(inquiry_id="demo-1", message=" ")
    with pytest.raises(ValidationError):
        InquiryRequest(inquiry_id="demo-1", message="Hello", secret="unexpected")


def test_triage_result_accepts_citation_and_human_review():
    result = TriageResult(
        inquiry_id="demo-1",
        eligibility=EligibilityLabel.NEEDS_REVIEW,
        rationale="The project location is unclear.",
        extracted_facts=ExtractedFacts(facts_missing=["region"]),
        citations=[KnowledgeCitation(
            document_id="fictional-program-v1",
            title="Fictional Regional Innovation Fund",
            excerpt="Applicants must operate in the North region.",
            source_version="1.0",
        )],
        escalate=True,
        escalation_reasons=[EscalationReason.MISSING_INFORMATION],
        confidence_score=0.5,
        prompt_version="triage-v1",
    )
    assert result.escalate is True
    assert result.citations[0].document_id == "fictional-program-v1"


def test_result_rejects_out_of_range_confidence_and_invalid_amount():
    with pytest.raises(ValidationError):
        TriageResult(
            inquiry_id="demo-1", eligibility="eligible", rationale="OK",
            extracted_facts={}, confidence_score=1.1,
        )
    with pytest.raises(ValidationError):
        ExtractedFacts(requested_amount_pln=-1)


def test_inquiry_accepts_synthetic_message():
    inquiry = InquiryRequest(inquiry_id="synthetic-01", message="We plan a fictional project.")
    assert inquiry.inquiry_id == "synthetic-01"
