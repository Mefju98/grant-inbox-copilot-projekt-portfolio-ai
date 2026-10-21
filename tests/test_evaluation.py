from pathlib import Path

import pytest

from app.evaluation import evaluate, load_cases
from app.schemas import EligibilityLabel, EvaluationCase, EvaluationPrediction


DATASET = Path(__file__).parents[1] / "data" / "evaluation_cases.jsonl"


def test_frozen_gold_dataset_has_50_balanced_labeled_cases():
    cases = load_cases(DATASET)
    assert len(cases) == 50
    assert sum(case.expected_eligibility == EligibilityLabel.ELIGIBLE for case in cases) == 17
    assert sum(case.expected_eligibility == EligibilityLabel.INELIGIBLE for case in cases) == 17
    assert sum(case.expected_eligibility == EligibilityLabel.NEEDS_REVIEW for case in cases) == 16


def test_perfect_predictions_produce_perfect_quality_metrics_and_aggregate_cost_time():
    cases = load_cases(DATASET)[:2]
    predictions = [
        EvaluationPrediction(
            case_id=case.case_id,
            eligibility=case.expected_eligibility,
            escalate=case.expected_escalation,
            citation_ids=case.expected_citation_ids,
            schema_valid=True,
            latency_ms=latency,
            cost_usd=cost,
        )
        for case, latency, cost in zip(cases, [100, 300], [0.01, 0.03])
    ]
    metrics = evaluate(cases, predictions)
    assert metrics.accuracy == 1
    assert metrics.macro_f1 == 1
    assert metrics.schema_valid_rate == 1
    assert metrics.citation_f1 == 1
    assert metrics.escalation_f1 == 1
    assert metrics.total_cost_usd == 0.04
    assert metrics.average_latency_ms == 200
    assert metrics.p95_latency_ms == 300


def test_metrics_capture_classification_citation_schema_and_escalation_errors():
    cases = load_cases(DATASET)[:2]
    predictions = [
        EvaluationPrediction(
            case_id=cases[0].case_id,
            eligibility=EligibilityLabel.INELIGIBLE,
            escalate=True,
            citation_ids=["wrong-source"],
            schema_valid=False,
            latency_ms=200,
        ),
        EvaluationPrediction(
            case_id=cases[1].case_id,
            eligibility=cases[1].expected_eligibility,
            escalate=cases[1].expected_escalation,
            citation_ids=cases[1].expected_citation_ids,
            schema_valid=True,
            latency_ms=100,
        ),
    ]
    metrics = evaluate(cases, predictions)
    assert metrics.accuracy == 0.5
    assert metrics.schema_valid_rate == 0.5
    assert metrics.citation_recall == 0.5
    assert metrics.citation_precision < 1
    assert metrics.escalation_recall == 1
    assert metrics.escalation_precision < 1


def test_metrics_reject_predictions_for_wrong_case_set():
    cases = load_cases(DATASET)[:1]
    prediction = EvaluationPrediction(
        case_id="unknown-case",
        eligibility=EligibilityLabel.NEEDS_REVIEW,
        escalate=True,
        schema_valid=False,
    )
    with pytest.raises(ValueError, match="Prediction IDs differ"):
        evaluate(cases, [prediction])
