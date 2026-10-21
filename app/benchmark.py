"""Run the frozen synthetic evaluation set against a live Grant Inbox API."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

import httpx
from pydantic import ValidationError

from app.evaluation import evaluate, load_cases
from app.schemas import (
    EligibilityLabel,
    EvaluationCase,
    EvaluationPrediction,
    TriageAPIResponse,
)


DEFAULT_CASES = Path(__file__).resolve().parent.parent / "data" / "evaluation_cases.jsonl"
DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "outputs" / "evaluation-report.json"


def run_api_evaluation(
    api_url: str,
    cases: list[EvaluationCase],
    *,
    http_client: httpx.Client | None = None,
    timeout_seconds: float = 120.0,
) -> dict:
    """Call the API once per case and return a privacy-minimized report.

    Failed or invalid responses are counted as invalid-schema, escalated
    ``needs_review`` predictions so the report retains all gold case IDs. The
    failure list makes those fallback rows visible and prevents silent hiding.
    """

    owns_client = http_client is None
    client = http_client or httpx.Client(timeout=timeout_seconds)
    predictions: list[EvaluationPrediction] = []
    rows: list[dict] = []

    try:
        for case in cases:
            started = perf_counter()
            status_code = None
            response = None
            llm_metrics = None
            result = None
            error_code = None
            error_diagnostics: dict = {}
            try:
                response = client.post(
                    api_url,
                    json={"inquiry_id": case.case_id, "message": case.message},
                )
                status_code = response.status_code
                response.raise_for_status()
                payload = TriageAPIResponse.model_validate(response.json())
                result = payload.result
                llm_metrics = payload.llm
                prediction = EvaluationPrediction(
                    case_id=case.case_id,
                    eligibility=result.eligibility,
                    escalate=result.escalate,
                    citation_ids=[citation.document_id for citation in result.citations],
                    schema_valid=True,
                    latency_ms=round((perf_counter() - started) * 1000),
                    cost_usd=llm_metrics.cost_usd,
                )
            except (httpx.HTTPError, ValueError, ValidationError) as exc:
                if isinstance(exc, ValidationError):
                    error_code = "invalid_response_schema"
                elif response is not None and status_code is not None and status_code >= 400:
                    try:
                        error_payload = response.json()
                    except ValueError:
                        error_payload = None
                    error_code = (
                        error_payload.get("code")
                        if isinstance(error_payload, dict) and isinstance(error_payload.get("code"), str)
                        else f"http_{status_code}"
                    )
                    if isinstance(error_payload, dict):
                        error_diagnostics = _safe_error_diagnostics(error_payload)
                elif isinstance(exc, httpx.HTTPError):
                    error_code = "request_error"
                else:
                    error_code = "invalid_json_response"
                prediction = EvaluationPrediction(
                    case_id=case.case_id,
                    eligibility=EligibilityLabel.NEEDS_REVIEW,
                    escalate=True,
                    citation_ids=[],
                    schema_valid=False,
                    latency_ms=round((perf_counter() - started) * 1000),
                )

            predictions.append(prediction)
            rows.append(
                {
                    "case_id": case.case_id,
                    "http_status": status_code,
                    "schema_valid": prediction.schema_valid,
                    "eligibility": prediction.eligibility.value,
                    "escalate": prediction.escalate,
                    "citation_ids": prediction.citation_ids,
                    "latency_ms": prediction.latency_ms,
                    "cost_usd": prediction.cost_usd,
                    "model": llm_metrics.model if llm_metrics else None,
                    "prompt_version": llm_metrics.prompt_version if llm_metrics else None,
                    "model_proposal_eligibility": (
                        llm_metrics.model_proposal_eligibility.value
                        if llm_metrics and llm_metrics.model_proposal_eligibility
                        else None
                    ),
                    "policy_eligibility": (
                        llm_metrics.policy_eligibility.value
                        if llm_metrics and llm_metrics.policy_eligibility
                        else None
                    ),
                    "attempts": (
                        llm_metrics.attempts
                        if llm_metrics
                        else error_diagnostics.get("attempts")
                    ),
                    "request_id": error_diagnostics.get("request_id"),
                    "failure_kind": error_diagnostics.get("failure_kind"),
                    "attempt_summaries": error_diagnostics.get("attempt_summaries", []),
                    "escalation_reasons": (
                        [reason.value for reason in result.escalation_reasons] if result else []
                    ),
                    "extracted_policy_fields": (
                        [
                            field
                            for field in ("region", "company_size", "requested_amount_pln", "project_summary")
                            if getattr(result.extracted_facts, field) is not None
                        ]
                        if result
                        else []
                    ),
                    "required_facts_missing": (
                        [
                            field
                            for field in (
                                "region",
                                "company_size",
                                "requested_amount_pln",
                                "project_summary",
                            )
                            if getattr(result.extracted_facts, field) is None
                        ]
                        if result
                        else []
                    ),
                    "model_reported_facts_missing": (
                        list(result.extracted_facts.facts_missing) if result else []
                    ),
                    "error_code": error_code,
                }
            )
    finally:
        if owns_client:
            client.close()

    metrics = evaluate(cases, predictions)
    successful_ids = {
        row["case_id"] for row in rows if row["error_code"] is None and row["schema_valid"]
    }
    successful_cases = [case for case in cases if case.case_id in successful_ids]
    successful_predictions = [
        prediction for prediction in predictions if prediction.case_id in successful_ids
    ]
    successful_metrics = (
        evaluate(successful_cases, successful_predictions).model_dump(mode="json")
        if successful_cases
        else None
    )
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "api_url": api_url,
        "case_count": len(cases),
        "failure_count": sum(row["error_code"] is not None for row in rows),
        "metrics": metrics.model_dump(mode="json"),
        "successful_response_count": len(successful_cases),
        "successful_response_metrics": successful_metrics,
        "cases": rows,
    }


def _safe_error_diagnostics(payload: dict) -> dict:
    """Copy only bounded, typed diagnostics from our API's error response."""

    safe: dict = {}
    request_id = payload.get("request_id")
    if isinstance(request_id, str) and re.fullmatch(r"[0-9a-f]{12}", request_id):
        safe["request_id"] = request_id
    attempts = payload.get("attempts")
    if type(attempts) is int and 1 <= attempts <= 3:
        safe["attempts"] = attempts
    failure_kind = payload.get("failure_kind")
    if isinstance(failure_kind, str) and re.fullmatch(r"[a-z0-9_]{1,40}", failure_kind):
        safe["failure_kind"] = failure_kind

    raw_summaries = payload.get("attempt_summaries")
    if isinstance(raw_summaries, list):
        summaries = []
        for raw in raw_summaries[:3]:
            if not isinstance(raw, dict):
                continue
            summary: dict = {}
            attempt = raw.get("attempt")
            if type(attempt) is int and 1 <= attempt <= 3:
                summary["attempt"] = attempt
            outcome = raw.get("outcome")
            if isinstance(outcome, str) and re.fullmatch(r"[a-z0-9_]{1,40}", outcome):
                summary["outcome"] = outcome
            for key in ("duration_ms", "completion_tokens"):
                value = raw.get(key)
                if type(value) is int and value >= 0:
                    summary[key] = value
            status = raw.get("http_status")
            if type(status) is int and 100 <= status <= 599:
                summary["http_status"] = status
            finish_reason = raw.get("finish_reason")
            if isinstance(finish_reason, str) and re.fullmatch(r"[A-Za-z_]{1,32}", finish_reason):
                summary["finish_reason"] = finish_reason
            will_retry = raw.get("will_retry")
            if type(will_retry) is bool:
                summary["will_retry"] = will_retry
            if summary:
                summaries.append(summary)
        safe["attempt_summaries"] = summaries
    return safe


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000/triage")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--timeout", type=float, default=120.0, help="Per-case request timeout in seconds")
    parser.add_argument("--limit", type=int, help="Evaluate only the first N cases (useful for a cost-conscious smoke test)")
    args = parser.parse_args()

    cases = load_cases(args.cases)
    if args.limit is not None:
        if args.limit < 1:
            parser.error("--limit must be at least 1")
        cases = cases[:args.limit]
    report = run_api_evaluation(args.url, cases, timeout_seconds=args.timeout)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Evaluated {report['case_count']} synthetic cases; failures: {report['failure_count']}")
    print(f"Accuracy: {report['metrics']['accuracy']:.3f}; macro F1: {report['metrics']['macro_f1']:.3f}")
    print(f"Schema valid: {report['metrics']['schema_valid_rate']:.1%}; citation F1: {report['metrics']['citation_f1']:.3f}")
    print(f"Escalation F1: {report['metrics']['escalation_f1']:.3f}; average latency: {report['metrics']['average_latency_ms']} ms")
    successful_metrics = report["successful_response_metrics"]
    if successful_metrics is not None:
        print(
            f"Successful responses only ({report['successful_response_count']}): "
            f"accuracy {successful_metrics['accuracy']:.3f}; "
            f"macro F1 {successful_metrics['macro_f1']:.3f}; "
            f"escalation F1 {successful_metrics['escalation_f1']:.3f}"
        )
    else:
        print("Successful responses only: no valid responses to score")
    print(f"Observed total cost: {report['metrics']['total_cost_usd']}; report: {args.output}")
    return 1 if report["failure_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
