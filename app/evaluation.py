"""Offline metrics for a frozen, synthetic evaluation set."""

from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from statistics import fmean

from app.schemas import (
    ClassMetrics,
    EligibilityLabel,
    EvaluationCase,
    EvaluationMetrics,
    EvaluationPrediction,
)


LABELS = tuple(EligibilityLabel)


def load_cases(path: str | Path) -> list[EvaluationCase]:
    """Load JSONL gold labels; the file is static and never model-generated."""

    cases = []
    with Path(path).open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if line.strip():
                try:
                    cases.append(EvaluationCase.model_validate(json.loads(line)))
                except (json.JSONDecodeError, ValueError) as exc:
                    raise ValueError(f"Invalid evaluation case at line {line_number}") from exc
    _assert_unique((case.case_id for case in cases), "gold case")
    return cases


def evaluate(
    cases: list[EvaluationCase], predictions: list[EvaluationPrediction]
) -> EvaluationMetrics:
    """Calculate quality, safety, citation, cost, and latency metrics."""

    _assert_unique((case.case_id for case in cases), "gold case")
    _assert_unique((prediction.case_id for prediction in predictions), "prediction")
    expected_ids = {case.case_id for case in cases}
    actual_ids = {prediction.case_id for prediction in predictions}
    if expected_ids != actual_ids:
        raise ValueError(
            f"Prediction IDs differ from gold set; missing={sorted(expected_ids - actual_ids)}, "
            f"extra={sorted(actual_ids - expected_ids)}"
        )
    if not cases:
        raise ValueError("Evaluation set must not be empty")

    gold_by_id = {case.case_id: case for case in cases}
    pred_by_id = {prediction.case_id: prediction for prediction in predictions}
    confusion = {actual.value: {predicted.value: 0 for predicted in LABELS} for actual in LABELS}
    per_class: dict[str, ClassMetrics] = {}

    for case_id, case in gold_by_id.items():
        prediction = pred_by_id[case_id]
        confusion[case.expected_eligibility.value][prediction.eligibility.value] += 1

    for label in LABELS:
        tp = confusion[label.value][label.value]
        fp = sum(confusion[other.value][label.value] for other in LABELS if other != label)
        fn = sum(confusion[label.value][other.value] for other in LABELS if other != label)
        support = tp + fn
        precision = _ratio(tp, tp + fp, empty_value=1.0 if support == 0 else 0.0)
        recall = _ratio(tp, support, empty_value=1.0)
        f1 = _ratio(2 * precision * recall, precision + recall, empty_value=0.0)
        per_class[label.value] = ClassMetrics(
            precision=round(precision, 4), recall=round(recall, 4), f1=round(f1, 4), support=support
        )

    citation_true_positive = citation_predicted = citation_expected = 0
    escalation_tp = escalation_fp = escalation_fn = 0
    correct_labels = 0
    for case_id, case in gold_by_id.items():
        prediction = pred_by_id[case_id]
        gold_citations = set(case.expected_citation_ids)
        predicted_citations = set(prediction.citation_ids)
        citation_true_positive += len(gold_citations & predicted_citations)
        citation_predicted += len(predicted_citations)
        citation_expected += len(gold_citations)
        correct_labels += prediction.eligibility == case.expected_eligibility
        escalation_tp += prediction.escalate and case.expected_escalation
        escalation_fp += prediction.escalate and not case.expected_escalation
        escalation_fn += not prediction.escalate and case.expected_escalation

    citation_precision = _ratio(
        citation_true_positive, citation_predicted,
        empty_value=1.0 if citation_expected == 0 else 0.0,
    )
    citation_recall = _ratio(citation_true_positive, citation_expected, empty_value=1.0)
    citation_f1 = _ratio(
        2 * citation_precision * citation_recall,
        citation_precision + citation_recall,
        empty_value=0.0,
    )
    escalation_precision = _ratio(escalation_tp, escalation_tp + escalation_fp, empty_value=1.0 if escalation_fn == 0 else 0.0)
    escalation_recall = _ratio(escalation_tp, escalation_tp + escalation_fn, empty_value=1.0)
    escalation_f1 = _ratio(2 * escalation_precision * escalation_recall, escalation_precision + escalation_recall, empty_value=0.0)

    costs = [prediction.cost_usd for prediction in predictions if prediction.cost_usd is not None]
    latencies = sorted(prediction.latency_ms for prediction in predictions if prediction.latency_ms is not None)
    p95 = latencies[math.ceil(0.95 * len(latencies)) - 1] if latencies else None

    return EvaluationMetrics(
        case_count=len(cases),
        accuracy=round(correct_labels / len(cases), 4),
        per_class=per_class,
        confusion_matrix=confusion,
        macro_f1=round(fmean(metric.f1 for metric in per_class.values()), 4),
        schema_valid_rate=round(sum(pred.schema_valid for pred in predictions) / len(cases), 4),
        citation_precision=round(citation_precision, 4),
        citation_recall=round(citation_recall, 4),
        citation_f1=round(citation_f1, 4),
        escalation_precision=round(escalation_precision, 4),
        escalation_recall=round(escalation_recall, 4),
        escalation_f1=round(escalation_f1, 4),
        total_cost_usd=round(math.fsum(costs), 8) if costs else None,
        average_cost_usd=round(fmean(costs), 8) if costs else None,
        cost_observation_count=len(costs),
        average_latency_ms=round(fmean(latencies), 2) if latencies else None,
        p95_latency_ms=p95,
        latency_observation_count=len(latencies),
    )


def _ratio(numerator: float, denominator: float, *, empty_value: float) -> float:
    return numerator / denominator if denominator else empty_value


def _assert_unique(values, label: str) -> None:
    items = list(values)
    duplicates = sorted(value for value, count in Counter(items).items() if count > 1)
    if duplicates:
        raise ValueError(f"Duplicate {label} IDs: {duplicates}")
