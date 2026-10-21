import json
from pathlib import Path

import httpx

from app.benchmark import run_api_evaluation
from app.evaluation import load_cases
from app.schemas import TriageAPIResponse


DATASET = Path(__file__).parents[1] / "data" / "evaluation_cases.jsonl"


def _success_payload(case_id: str) -> dict:
    return TriageAPIResponse(
        result={
            "inquiry_id": case_id,
            "eligibility": "eligible",
            "recommendation": "Synthetic recommendation supported by the retrieved docs.",
            "rationale": "The synthetic case matched the toy policy.",
            "extracted_facts": {
                "region": "North",
                "company_size": "small",
                "requested_amount_pln": 50000,
                "project_summary": "R&D prototype",
            },
            "citations": [
                {
                    "document_id": "fictional-program-overview-v1",
                    "title": "Synthetic overview",
                    "excerpt": "Synthetic evidence.",
                    "source_version": "1.0",
                }
            ],
            "escalate": False,
            "escalation_reasons": [],
        },
        llm={
            "model": "fake-model",
            "prompt_version": "triage-v2",
            "model_proposal_eligibility": "eligible",
            "policy_eligibility": "eligible",
            "latency_ms": 30,
            "input_tokens": 10,
            "output_tokens": 10,
            "cost_usd": 0.001,
            "attempts": 1,
        },
    ).model_dump(mode="json")


def test_live_benchmark_normalizes_responses_and_records_metrics_without_messages():
    cases = load_cases(DATASET)[:2]

    def handler(request):
        inquiry_id = json.loads(request.content)["inquiry_id"]
        return httpx.Response(200, json=_success_payload(inquiry_id))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    report = run_api_evaluation("http://fake/triage", cases, http_client=client)

    assert report["case_count"] == 2
    assert report["failure_count"] == 0
    assert report["metrics"]["schema_valid_rate"] == 1
    assert report["successful_response_count"] == 2
    assert report["successful_response_metrics"]["case_count"] == 2
    assert report["metrics"]["cost_observation_count"] == 2
    assert report["cases"][0]["model"] == "fake-model"
    assert report["cases"][0]["model_proposal_eligibility"] == "eligible"
    assert report["cases"][0]["policy_eligibility"] == "eligible"
    assert report["cases"][0]["extracted_policy_fields"] == [
        "region",
        "company_size",
        "requested_amount_pln",
        "project_summary",
    ]
    assert report["cases"][0]["required_facts_missing"] == []
    assert report["cases"][0]["model_reported_facts_missing"] == []
    assert "message" not in report["cases"][0]


def test_live_benchmark_counts_api_errors_as_visible_escalated_schema_failures():
    cases = load_cases(DATASET)[:1]
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(502, json={"code": "provider_error"}))
    )

    report = run_api_evaluation("http://fake/triage", cases, http_client=client)

    assert report["failure_count"] == 1
    assert report["metrics"]["schema_valid_rate"] == 0
    assert report["cases"][0]["eligibility"] == "needs_review"
    assert report["cases"][0]["escalate"] is True
    assert report["cases"][0]["error_code"] == "provider_error"
    assert report["successful_response_count"] == 0
    assert report["successful_response_metrics"] is None


def test_benchmark_preserves_only_sanitized_retry_diagnostics_from_502():
    cases = load_cases(DATASET)[:1]
    payload = {
        "code": "llm_provider_error",
        "request_id": "012345abcdef",
        "attempts": 2,
        "failure_kind": "timeout_read",
        "attempt_summaries": [
            {
                "attempt": 1,
                "outcome": "timeout_read",
                "duration_ms": 30_000,
                "will_retry": True,
                "prompt": "private text must be dropped",
            },
            {
                "attempt": 2,
                "outcome": "timeout_read",
                "duration_ms": 30_000,
                "will_retry": False,
            },
        ],
        "message": "private provider response must be dropped",
    }
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(502, json=payload))
    )

    report = run_api_evaluation("http://fake/triage", cases, http_client=client)

    row = report["cases"][0]
    assert row["request_id"] == "012345abcdef"
    assert row["attempts"] == 2
    assert row["failure_kind"] == "timeout_read"
    assert row["attempt_summaries"] == [
        {"attempt": 1, "outcome": "timeout_read", "duration_ms": 30_000, "will_retry": True},
        {"attempt": 2, "outcome": "timeout_read", "duration_ms": 30_000, "will_retry": False},
    ]
    assert "private" not in json.dumps(row)


def test_success_metrics_exclude_failed_fallback_rows():
    cases = load_cases(DATASET)[:2]

    def handler(request):
        inquiry_id = json.loads(request.content)["inquiry_id"]
        if inquiry_id == cases[1].case_id:
            return httpx.Response(502, json={"code": "provider_error"})
        return httpx.Response(200, json=_success_payload(inquiry_id))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    report = run_api_evaluation("http://fake/triage", cases, http_client=client)

    assert report["case_count"] == 2
    assert report["failure_count"] == 1
    assert report["metrics"]["case_count"] == 2
    assert report["successful_response_count"] == 1
    assert report["successful_response_metrics"]["case_count"] == 1
