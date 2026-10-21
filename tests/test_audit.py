from app.audit import AuditStore
from app.llm import LLMCallResult, PROMPT_VERSION
from app.schemas import EligibilityLabel, ExtractedFacts, ModelTriageDraft, TriageResult


def test_audit_persists_summary_without_raw_message_or_extracted_identifiers(tmp_path):
    store = AuditStore(tmp_path / "audit.sqlite3")
    result = TriageResult(
        inquiry_id="synthetic-01",
        eligibility=EligibilityLabel.NEEDS_REVIEW,
        rationale="Missing region.",
        extracted_facts=ExtractedFacts(company_name="Synthetic Works", tax_id="SYNTHETIC-TAX-ID"),
        escalate=True,
        escalation_reasons=["no_knowledge_match"],
        prompt_version=PROMPT_VERSION,
    )
    call = LLMCallResult(
        draft=ModelTriageDraft(
            eligibility=EligibilityLabel.NEEDS_REVIEW,
            rationale="Missing region.",
            extracted_facts=ExtractedFacts(),
        ),
        model="fake-model",
        prompt_version=PROMPT_VERSION,
        latency_ms=25,
        input_tokens=100,
        output_tokens=20,
        cost_usd=0.001,
        attempts=2,
    )
    store.record_success(result, call)
    row = store.list_records()[0]
    assert row["inquiry_id"] == "synthetic-01"
    assert row["outcome"] == "success"
    assert row["attempts"] == 2
    assert row["cost_usd"] == 0.001
    assert "tax_id" not in row
    assert "Synthetic Works" not in str(row)
    assert "raw_message" not in row


def test_audit_records_failure_as_escalated(tmp_path):
    store = AuditStore(tmp_path / "audit.sqlite3")
    store.record_failure("synthetic-02", "llm_provider_error", attempts=2)
    row = store.list_records()[0]
    assert row["outcome"] == "failure"
    assert row["escalate"] == 1
    assert row["error_code"] == "llm_provider_error"
    assert row["attempts"] == 2
