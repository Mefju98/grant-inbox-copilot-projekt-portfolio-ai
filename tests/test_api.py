from app.audit import AuditStore
from app.llm import LLMCallResult, LLMProviderError, PROMPT_VERSION
from dataclasses import replace
from app.main import create_app
from app.retrieval import KnowledgeRetriever, RetrievedDocument
from app.retry import RetryPolicy
from app.schemas import EligibilityLabel, ExtractedFacts, InquiryRequest, KnowledgeDocument, ModelTriageDraft


OVERVIEW = KnowledgeDocument(
    document_id="fictional-program-overview-v1",
    title="Fictional North Innovation Fund — Program Overview",
    source_version="1.0",
    content="Supports micro and small enterprises in North for research, development, or prototype work.",
)


class FixedRetriever:
    def search(self, query):
        documents = KnowledgeRetriever.from_default_corpus().documents
        return [RetrievedDocument(document=document, score=1.0) for document in documents]


class EmptyRetriever:
    def search(self, query):
        return []


class FakeLLMClient:
    def triage(self, message, evidence=None):
        assert message == "Synthetic message"
        assert evidence[0].document_id == OVERVIEW.document_id
        return LLMCallResult(
            draft=ModelTriageDraft(
                eligibility=EligibilityLabel.ELIGIBLE,
                rationale="The supplied facts match the fictional program criteria.",
                extracted_facts=ExtractedFacts(
                    region="North",
                    company_size="small",
                    requested_amount_pln=50_000,
                    project_summary="Development of a prototype",
                ),
                confidence_score=0.8,
                recommendation="The inquiry appears to meet the fictional program criteria.",
                citation_ids=[OVERVIEW.document_id],
            ),
            model="fake-model",
            prompt_version=PROMPT_VERSION,
            latency_ms=17,
            input_tokens=20,
            output_tokens=10,
        )

    def close(self):
        pass


def _endpoint(app, path):
    return next(route.endpoint for route in app.routes if getattr(route, "path", None) == path)


def test_triage_api_returns_only_retrieved_citations_and_audits_result(tmp_path):
    audit = AuditStore(tmp_path / "audit.sqlite3")
    app = create_app(client_factory=FakeLLMClient, audit_store=audit, retriever=FixedRetriever())
    request = InquiryRequest(inquiry_id="synthetic-01", message="Synthetic message")
    response = _endpoint(app, "/triage")(request)
    assert response.result.eligibility == EligibilityLabel.ELIGIBLE
    assert response.result.escalate is False
    assert response.result.recommendation is not None
    assert response.result.citations[0].document_id == OVERVIEW.document_id
    assert response.llm.model == "fake-model"
    assert response.llm.model_proposal_eligibility == EligibilityLabel.ELIGIBLE
    assert response.llm.policy_eligibility == EligibilityLabel.ELIGIBLE
    assert response.llm.latency_ms >= 0
    assert audit.list_records()[0]["outcome"] == "success"


def test_explicit_company_size_fallback_can_resolve_a_model_extraction_omission(tmp_path):
    class OmitsCompanySizeClient:
        def triage(self, message, evidence=None):
            return LLMCallResult(
                draft=ModelTriageDraft(
                    eligibility=EligibilityLabel.ELIGIBLE,
                    rationale="The stated project appears to match the fictional program.",
                    extracted_facts=ExtractedFacts(
                        region="North",
                        requested_amount_pln=50_000,
                        project_summary="Development of a prototype",
                        facts_missing=["company_size", "tax_id"],
                    ),
                    recommendation="The inquiry appears to meet the fictional program criteria.",
                    citation_ids=[OVERVIEW.document_id],
                ),
                model="fake-model",
                prompt_version=PROMPT_VERSION,
                latency_ms=10,
            )

        def close(self):
            pass

    app = create_app(
        client_factory=OmitsCompanySizeClient,
        audit_store=AuditStore(tmp_path / "audit.sqlite3"),
        retriever=FixedRetriever(),
    )
    request = InquiryRequest(
        inquiry_id="synthetic-explicit-size",
        message="A small company in North requests 50000 PLN for development of a prototype.",
    )

    result = _endpoint(app, "/triage")(request).result

    assert result.eligibility == EligibilityLabel.ELIGIBLE
    assert result.extracted_facts.company_size == "small"
    assert result.extracted_facts.facts_missing == []


def test_no_knowledge_match_skips_llm_and_escalates(tmp_path):
    class MustNotCallClient:
        def __init__(self):
            raise AssertionError("LLM should not be called when retrieval has no match")

    audit = AuditStore(tmp_path / "audit.sqlite3")
    app = create_app(client_factory=MustNotCallClient, audit_store=audit, retriever=EmptyRetriever())
    request = InquiryRequest(inquiry_id="synthetic-no-match", message="Synthetic message")
    response = _endpoint(app, "/triage")(request)
    assert response.result.eligibility == EligibilityLabel.NEEDS_REVIEW
    assert response.result.recommendation is None
    assert response.result.citations == []
    assert response.result.escalate is True
    assert response.llm.attempts == 0
    assert audit.list_records()[0]["attempts"] == 0


def test_model_cannot_cite_an_unretrieved_document_or_return_a_recommendation(tmp_path):
    class HallucinatedCitationClient(FakeLLMClient):
        def triage(self, message, evidence=None):
            call = super().triage(message, evidence=evidence)
            draft = call.draft.model_copy(update={"citation_ids": ["invented-source-v99"]})
            return replace(call, draft=draft)

    app = create_app(
        client_factory=HallucinatedCitationClient,
        audit_store=AuditStore(tmp_path / "audit.sqlite3"),
        retriever=FixedRetriever(),
    )
    request = InquiryRequest(inquiry_id="synthetic-hallucinated-citation", message="Synthetic message")
    result = _endpoint(app, "/triage")(request).result
    assert result.eligibility == EligibilityLabel.NEEDS_REVIEW
    assert result.recommendation is None
    assert result.escalate is True
    assert result.escalation_reasons[0].value == "unsupported_model_claim"


def test_model_abstention_does_not_override_complete_deterministic_policy(tmp_path):
    class DisagreesWithPolicyClient(FakeLLMClient):
        def triage(self, message, evidence=None):
            call = super().triage(message, evidence=evidence)
            draft = call.draft.model_copy(update={"eligibility": EligibilityLabel.NEEDS_REVIEW})
            return replace(call, draft=draft)

    app = create_app(
        client_factory=DisagreesWithPolicyClient,
        audit_store=AuditStore(tmp_path / "audit.sqlite3"),
        retriever=FixedRetriever(),
    )
    request = InquiryRequest(inquiry_id="synthetic-label-conflict", message="Synthetic message")

    result = _endpoint(app, "/triage")(request).result

    assert result.eligibility == EligibilityLabel.ELIGIBLE
    assert result.recommendation is not None
    assert result.escalate is False
    assert result.escalation_reasons == []


def test_definitive_model_policy_contradiction_still_escalates(tmp_path):
    class ContradictsPolicyClient(FakeLLMClient):
        def triage(self, message, evidence=None):
            call = super().triage(message, evidence=evidence)
            draft = call.draft.model_copy(update={"eligibility": EligibilityLabel.INELIGIBLE})
            return replace(call, draft=draft)

    app = create_app(
        client_factory=ContradictsPolicyClient,
        audit_store=AuditStore(tmp_path / "audit.sqlite3"),
        retriever=FixedRetriever(),
    )
    request = InquiryRequest(inquiry_id="synthetic-definitive-conflict", message="Synthetic message")

    result = _endpoint(app, "/triage")(request).result

    assert result.eligibility == EligibilityLabel.ELIGIBLE
    assert result.recommendation is None
    assert result.escalate is True
    assert result.escalation_reasons == ["conflicting_evidence"]
    assert "model proposed ineligible" in result.rationale


def test_explicit_project_ambiguity_escalates_even_when_required_fields_are_present(tmp_path):
    class AmbiguityOmittingClient(FakeLLMClient):
        def triage(self, message, evidence=None):
            return LLMCallResult(
                draft=ModelTriageDraft(
                    eligibility=EligibilityLabel.NEEDS_REVIEW,
                    rationale="The project may match the fictional program.",
                    extracted_facts=ExtractedFacts(
                        region="North",
                        company_size="small",
                        requested_amount_pln=65_000,
                        project_summary="Experimental development and refurbishment",
                    ),
                    recommendation="The inquiry may match the fictional program.",
                    citation_ids=[OVERVIEW.document_id],
                ),
                model="fake-model",
                prompt_version=PROMPT_VERSION,
                latency_ms=12,
            )

    app = create_app(
        client_factory=AmbiguityOmittingClient,
        audit_store=AuditStore(tmp_path / "audit.sqlite3"),
        retriever=FixedRetriever(),
    )
    request = InquiryRequest(
        inquiry_id="synthetic-project-ambiguity",
        message=(
            "A small company in North requests 65000 PLN for experimental development, "
            "but the project cost split is unclear."
        ),
    )

    result = _endpoint(app, "/triage")(request).result

    assert result.eligibility == EligibilityLabel.NEEDS_REVIEW
    assert result.recommendation is None
    assert result.escalate is True
    assert result.escalation_reasons == ["ambiguous_input"]


def test_known_ineligibility_does_not_require_an_llm_recommendation(tmp_path):
    class AbstainingClient(FakeLLMClient):
        def triage(self, message, evidence=None):
            return LLMCallResult(
                draft=ModelTriageDraft(
                    eligibility=EligibilityLabel.NEEDS_REVIEW,
                    rationale="The model omitted the project description.",
                    extracted_facts=ExtractedFacts(
                        region="North",
                        company_size="micro",
                        requested_amount_pln=9_000,
                        facts_missing=["project_summary"],
                    ),
                    recommendation=None,
                    citation_ids=["funding-range-v1"],
                ),
                model="fake-model",
                prompt_version=PROMPT_VERSION,
                latency_ms=12,
            )

    app = create_app(
        client_factory=AbstainingClient,
        audit_store=AuditStore(tmp_path / "audit.sqlite3"),
        retriever=FixedRetriever(),
    )
    request = InquiryRequest(
        inquiry_id="synthetic-known-ineligibility",
        message="A micro company in North requests 9000 PLN for a sensor prototype.",
    )

    result = _endpoint(app, "/triage")(request).result

    assert result.eligibility == EligibilityLabel.INELIGIBLE
    assert result.recommendation is None
    assert result.escalate is False
    assert result.escalation_reasons == []
    assert [citation.document_id for citation in result.citations] == ["funding-range-v1"]


def test_health_endpoint_does_not_require_provider_configuration():
    response = _endpoint(create_app(), "/health")()
    assert response == {"status": "ok"}


def test_provider_failure_is_retried_once_and_audited_as_escalated(tmp_path):
    class UnavailableClient:
        calls = 0

        def triage(self, message, evidence=None):
            self.calls += 1
            raise LLMProviderError("temporary outage", retryable=True)

        def close(self):
            pass

    made = []

    def factory():
        client = UnavailableClient()
        made.append(client)
        return client

    audit = AuditStore(tmp_path / "audit.sqlite3")
    app = create_app(
        client_factory=factory,
        audit_store=audit,
        retriever=FixedRetriever(),
        retry_policy=RetryPolicy(max_attempts=2, base_delay_seconds=0, max_delay_seconds=0),
    )
    request = InquiryRequest(inquiry_id="synthetic-failure", message="Synthetic message")
    response = _endpoint(app, "/triage")(request)
    assert response.status_code == 502
    assert made[0].calls == 2
    row = audit.list_records()[0]
    assert row["outcome"] == "failure"
    assert row["attempts"] == 2
    assert row["escalate"] == 1
