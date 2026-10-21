from app.baseline import classify_facts
from app.schemas import EligibilityLabel, EscalationReason, ExtractedFacts


def test_matching_facts_are_preliminarily_eligible_but_escalated_without_citation():
    result = classify_facts(
        "synthetic-eligible",
        ExtractedFacts(
            region="North",
            company_size="small",
            requested_amount_pln=50_000,
            project_summary="Development of an innovative prototype",
        ),
    )
    assert result.eligibility == EligibilityLabel.ELIGIBLE
    assert result.escalate is True
    assert result.recommendation is None
    assert result.citations == []
    assert EscalationReason.NO_KNOWLEDGE_MATCH in result.escalation_reasons


def test_explicitly_disqualifying_fact_is_classified_ineligible():
    result = classify_facts(
        "synthetic-ineligible",
        ExtractedFacts(
            region="South",
            company_size="small",
            requested_amount_pln=50_000,
            project_summary="Research prototype",
        ),
    )
    assert result.eligibility == EligibilityLabel.INELIGIBLE
    assert result.escalate is True
    assert result.recommendation is None


def test_missing_facts_require_review_and_are_named():
    result = classify_facts("synthetic-missing", ExtractedFacts(region="North"))
    assert result.eligibility == EligibilityLabel.NEEDS_REVIEW
    assert EscalationReason.MISSING_INFORMATION in result.escalation_reasons
    assert "company_size" in result.rationale
    assert result.escalate is True


def test_model_missing_list_cannot_override_populated_policy_facts():
    result = classify_facts(
        "synthetic-contradictory-missing-list",
        ExtractedFacts(
            region="North",
            company_size="small",
            requested_amount_pln=50_000,
            project_summary="Development of an energy-efficiency prototype",
            facts_missing=["company_name", "tax_id", "industry", "company_size", "project_summary"],
        ),
    )
    assert result.eligibility == EligibilityLabel.ELIGIBLE
    assert EscalationReason.MISSING_INFORMATION not in result.escalation_reasons


def test_truly_missing_policy_value_still_requires_review_even_if_model_omits_it():
    result = classify_facts(
        "synthetic-omitted-missing-list",
        ExtractedFacts(
            region="North",
            requested_amount_pln=50_000,
            project_summary="Development of a prototype",
        ),
    )
    assert result.eligibility == EligibilityLabel.NEEDS_REVIEW
    assert EscalationReason.MISSING_INFORMATION in result.escalation_reasons
    assert "company_size" in result.rationale


def test_unrecognized_project_summary_requires_review():
    result = classify_facts(
        "synthetic-unclear",
        ExtractedFacts(
            region="North",
            company_size="micro",
            requested_amount_pln=200_000,
            project_summary="Office renovation",
        ),
    )
    assert result.eligibility == EligibilityLabel.NEEDS_REVIEW
    assert EscalationReason.AMBIGUOUS_INPUT in result.escalation_reasons


def test_known_out_of_range_amount_decides_ineligibility_despite_missing_project_summary():
    result = classify_facts(
        "synthetic-low-amount-incomplete",
        ExtractedFacts(region="North", company_size="micro", requested_amount_pln=9_000),
        "A micro company in North requests 9000 PLN for a sensor prototype.",
    )
    assert result.eligibility == EligibilityLabel.INELIGIBLE
    assert EscalationReason.MISSING_INFORMATION not in result.escalation_reasons


def test_explicit_routine_project_is_ineligible_even_when_other_facts_are_missing():
    result = classify_facts(
        "synthetic-routine-project-incomplete",
        ExtractedFacts(region="North", company_size="small", requested_amount_pln=50_000),
        "A small company requests funding for routine office renovation with no research.",
    )
    assert result.eligibility == EligibilityLabel.INELIGIBLE


def test_explicit_develop_and_innovative_project_terms_count_as_rd_signals():
    result = classify_facts(
        "synthetic-develop-innovative",
        ExtractedFacts(
            region="North",
            company_size="small",
            requested_amount_pln=98_000,
            project_summary="Develop an innovative inventory robot",
        ),
    )
    assert result.eligibility == EligibilityLabel.ELIGIBLE
