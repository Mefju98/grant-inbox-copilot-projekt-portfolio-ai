"""FastAPI entry point for the draft-triage LLM workflow."""

import sqlite3
import uuid
from time import perf_counter

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.audit import AuditStore
from app.citations import select_policy_citation_ids
from app.extraction import has_explicit_uncertainty, reconcile_policy_facts
from app.llm import LLMSettings, OpenAICompatibleClient
from app.baseline import classify_facts
from app.retrieval import KnowledgeRetriever
from app.retry import RetryExhausted, RetryPolicy, call_with_retry, configure_attempt_logging
from app.schemas import (
    EligibilityLabel,
    ErrorResponse,
    EscalationReason,
    InquiryRequest,
    LLMCallMetrics,
    TriageAPIResponse,
    TriageResult,
)


def create_app(
    client_factory=None,
    audit_store=None,
    retriever=None,
    retry_policy: RetryPolicy = RetryPolicy(),
) -> FastAPI:
    configure_attempt_logging()
    app = FastAPI(
        title="Grant Inbox Copilot",
        version="0.1.0",
        description="Synthetic inquiry triage with cited retrieval and human review guardrails.",
    )
    audit = audit_store or AuditStore.from_env()
    knowledge = retriever or KnowledgeRetriever.from_default_corpus()

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/triage", response_model=TriageAPIResponse)
    def triage(request: InquiryRequest):
        request_id = uuid.uuid4().hex[:12]
        retrieval_started = perf_counter()
        try:
            matches = knowledge.search(request.message)
        except (OSError, ValueError):
            try:
                audit.record_failure(request.inquiry_id, "knowledge_base_unavailable", attempts=0)
            except (OSError, sqlite3.Error):
                pass
            return JSONResponse(
                status_code=503,
                content=ErrorResponse(
                    code="knowledge_base_unavailable",
                    message="The knowledge base is unavailable; no recommendation was returned.",
                    inquiry_id=request.inquiry_id,
                ).model_dump(mode="json"),
            )
        retrieval_latency_ms = round((perf_counter() - retrieval_started) * 1000)
        if not matches:
            result = TriageResult(
                inquiry_id=request.inquiry_id,
                eligibility="needs_review",
                recommendation=None,
                rationale="No relevant knowledge document matched this inquiry. No recommendation was generated; human review is required.",
                extracted_facts={"facts_missing": ["program_evidence"]},
                citations=[],
                escalate=True,
                escalation_reasons=[EscalationReason.NO_KNOWLEDGE_MATCH],
                prompt_version=None,
            )
            try:
                audit.record_no_match(result, retrieval_latency_ms)
            except (OSError, sqlite3.Error):
                return JSONResponse(
                    status_code=503,
                    content=ErrorResponse(
                        code="audit_unavailable",
                        message="The audit store is unavailable; no triage result was returned.",
                        inquiry_id=request.inquiry_id,
                    ).model_dump(mode="json"),
                )
            return TriageAPIResponse(
                result=result,
                llm=LLMCallMetrics(latency_ms=retrieval_latency_ms),
            )

        retrieved_documents = [match.document for match in matches]
        try:
            client = client_factory() if client_factory else OpenAICompatibleClient(LLMSettings.from_env())
        except (ValueError, OSError):
            try:
                audit.record_failure(request.inquiry_id, "llm_not_configured", attempts=0)
            except (OSError, sqlite3.Error):
                return JSONResponse(
                    status_code=503,
                    content=ErrorResponse(
                        code="audit_unavailable",
                        message="The audit store is unavailable; no triage result was returned.",
                        inquiry_id=request.inquiry_id,
                    ).model_dump(mode="json"),
                )
            return JSONResponse(
                status_code=503,
                content=ErrorResponse(
                    code="llm_not_configured",
                    message="Configure LLM_MODEL and a compatible LLM endpoint before submitting inquiries.",
                    inquiry_id=request.inquiry_id,
                ).model_dump(mode="json"),
            )

        try:
            call = call_with_retry(
                client,
                request.message,
                retry_policy,
                evidence=retrieved_documents,
                request_id=request_id,
            )
        except RetryExhausted as exc:
            try:
                audit.record_failure(request.inquiry_id, "llm_provider_error", attempts=exc.attempt_count)
            except (OSError, sqlite3.Error):
                return JSONResponse(
                    status_code=503,
                    content=ErrorResponse(
                        code="audit_unavailable",
                        message="The audit store is unavailable; no triage result was returned.",
                        inquiry_id=request.inquiry_id,
                    ).model_dump(mode="json"),
                )
            # Status and ``code`` are unchanged; the extra keys are content-free diagnostics.
            return JSONResponse(
                status_code=502,
                content={
                    **ErrorResponse(
                        code="llm_provider_error",
                        message="The LLM provider returned an error or an invalid response.",
                        inquiry_id=request.inquiry_id,
                    ).model_dump(mode="json"),
                    "request_id": request_id,
                    "attempts": exc.attempt_count,
                    "failure_kind": exc.failure_kind,
                    "attempt_summaries": exc.attempt_summaries,
                },
                headers={"X-Request-ID": request_id},
            )
        finally:
            client.close()

        result = _ground_result(request.inquiry_id, call, retrieved_documents, request.message)
        response = TriageAPIResponse(
            result=result,
            llm=LLMCallMetrics(
                model=call.model,
                prompt_version=call.prompt_version,
                model_proposal_eligibility=call.draft.eligibility,
                policy_eligibility=classify_facts(
                    request.inquiry_id, result.extracted_facts, request.message
                ).eligibility,
                latency_ms=call.latency_ms,
                input_tokens=call.input_tokens,
                output_tokens=call.output_tokens,
                cost_usd=call.cost_usd,
                attempts=call.attempts,
            ),
        )
        try:
            audit.record_success(result, call)
        except (OSError, sqlite3.Error):
            return JSONResponse(
                status_code=503,
                content=ErrorResponse(
                    code="audit_unavailable",
                    message="The audit store is unavailable; no triage result was returned.",
                    inquiry_id=request.inquiry_id,
                ).model_dump(mode="json"),
            )
        return response

    return app


def _ground_result(inquiry_id, call, retrieved_documents, inquiry_text) -> TriageResult:
    """Ground citations and let deterministic policy rules own the eligibility label."""

    documents = {document.document_id: document for document in retrieved_documents}
    cited_ids = list(dict.fromkeys(call.draft.citation_ids))
    unsupported_ids = [document_id for document_id in cited_ids if document_id not in documents]
    facts = reconcile_policy_facts(inquiry_text, call.draft.extracted_facts)
    rule_result = classify_facts(inquiry_id, facts, inquiry_text)
    explicit_uncertainty = has_explicit_uncertainty(inquiry_text)
    citation_eligibility = (
        EligibilityLabel.NEEDS_REVIEW if explicit_uncertainty else rule_result.eligibility
    )
    citation_ids = select_policy_citation_ids(
        citation_eligibility,
        facts,
        inquiry_text,
        set(documents),
    )
    citations = [
        {
            "document_id": document_id,
            "title": documents[document_id].title,
            "excerpt": documents[document_id].content,
            "source_version": documents[document_id].source_version,
        }
        for document_id in citation_ids
    ]
    rule_reasons = [
        reason for reason in rule_result.escalation_reasons
        if reason != EscalationReason.NO_KNOWLEDGE_MATCH
    ]

    if unsupported_ids or not citations:
        return TriageResult(
            inquiry_id=inquiry_id,
            eligibility="needs_review",
            recommendation=None,
            rationale="The model assessment could not be grounded in a valid retrieved citation. Human review is required.",
            extracted_facts=facts,
            citations=citations,
            escalate=True,
            escalation_reasons=[EscalationReason.UNSUPPORTED_MODEL_CLAIM],
            confidence_score=call.draft.confidence_score,
            prompt_version=call.prompt_version,
        )

    if explicit_uncertainty:
        return TriageResult(
            inquiry_id=inquiry_id,
            eligibility=EligibilityLabel.NEEDS_REVIEW,
            recommendation=None,
            rationale=(
                "The inquiry explicitly describes unresolved or conflicting project information. "
                "Human review is required before making a preliminary recommendation."
            ),
            extracted_facts=facts,
            citations=citations,
            escalate=True,
            escalation_reasons=[EscalationReason.AMBIGUOUS_INPUT],
            confidence_score=call.draft.confidence_score,
            prompt_version=call.prompt_version,
        )

    if rule_result.eligibility == EligibilityLabel.NEEDS_REVIEW:
        reasons = rule_reasons or [EscalationReason.MISSING_INFORMATION]
        return TriageResult(
            inquiry_id=inquiry_id,
            eligibility=EligibilityLabel.NEEDS_REVIEW,
            recommendation=None,
            rationale=rule_result.rationale + " Human review is required.",
            extracted_facts=facts,
            citations=citations,
            escalate=True,
            escalation_reasons=reasons,
            confidence_score=call.draft.confidence_score,
            prompt_version=call.prompt_version,
        )

    # ``needs_review`` is an abstention, not an opposite policy decision.
    # Small local models may abstain by default even when the extracted facts
    # are complete, so only a contradictory definitive label is a conflict.
    model_makes_definitive_claim = call.draft.eligibility != EligibilityLabel.NEEDS_REVIEW
    if model_makes_definitive_claim and call.draft.eligibility != rule_result.eligibility:
        return TriageResult(
            inquiry_id=inquiry_id,
            eligibility=rule_result.eligibility,
            recommendation=None,
            rationale=(
                f"The deterministic fictional-program rules indicate {rule_result.eligibility.value}, "
                f"but the model proposed {call.draft.eligibility.value}. The case is escalated for "
                "human review, and no recommendation was generated."
            ),
            extracted_facts=facts,
            citations=citations,
            escalate=True,
            escalation_reasons=[EscalationReason.CONFLICTING_EVIDENCE],
            confidence_score=call.draft.confidence_score,
            prompt_version=call.prompt_version,
        )

    if rule_result.eligibility == EligibilityLabel.INELIGIBLE:
        return TriageResult(
            inquiry_id=inquiry_id,
            eligibility=EligibilityLabel.INELIGIBLE,
            recommendation=None,
            rationale=rule_result.rationale,
            extracted_facts=facts,
            citations=citations,
            escalate=False,
            escalation_reasons=[],
            confidence_score=call.draft.confidence_score,
            prompt_version=call.prompt_version,
        )

    return TriageResult(
        inquiry_id=inquiry_id,
        eligibility=rule_result.eligibility,
        recommendation=(
            "Proceed to the next screening step; this preliminary result is not a funding decision."
        ),
        rationale=(
            "The supplied region, company size, requested amount, and project activity match "
            "the retrieved fictional program criteria."
        ),
        extracted_facts=facts,
        citations=citations,
        escalate=False,
        escalation_reasons=[],
        confidence_score=call.draft.confidence_score,
        prompt_version=call.prompt_version,
    )


app = create_app()
