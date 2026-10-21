import pytest

from app.citations import (
    COMPANY_SIZE,
    FUNDING_RANGE,
    OVERVIEW,
    PROJECT_TYPES,
    REGION,
    REQUIRED_INFORMATION,
    select_policy_citation_ids,
)
from app.schemas import EligibilityLabel, ExtractedFacts


ALL_DOCUMENTS = {
    OVERVIEW,
    REGION,
    COMPANY_SIZE,
    FUNDING_RANGE,
    PROJECT_TYPES,
    REQUIRED_INFORMATION,
}


@pytest.mark.parametrize(
    ("eligibility", "facts", "message", "expected"),
    [
        (
            EligibilityLabel.ELIGIBLE,
            ExtractedFacts(
                region="North",
                company_size="small",
                requested_amount_pln=50_000,
                project_summary="Development of a prototype",
            ),
            "Clear synthetic project",
            [OVERVIEW, REGION, COMPANY_SIZE, FUNDING_RANGE, PROJECT_TYPES],
        ),
        (
            EligibilityLabel.INELIGIBLE,
            ExtractedFacts(
                region="North",
                company_size="micro",
                requested_amount_pln=9_000,
                project_summary="Sensor prototype",
            ),
            "A sensor prototype",
            [FUNDING_RANGE],
        ),
        (
            EligibilityLabel.INELIGIBLE,
            ExtractedFacts(
                region="South",
                company_size="medium",
                requested_amount_pln=300_000,
                project_summary="Innovation project",
            ),
            "A project",
            [REGION, COMPANY_SIZE, FUNDING_RANGE],
        ),
        (
            EligibilityLabel.NEEDS_REVIEW,
            ExtractedFacts(region="North", company_size="small", requested_amount_pln=50_000),
            "Project description missing",
            [PROJECT_TYPES, REQUIRED_INFORMATION],
        ),
        (
            EligibilityLabel.NEEDS_REVIEW,
            ExtractedFacts(
                region="North",
                company_size="small",
                requested_amount_pln=50_000,
                project_summary="Experimental development",
            ),
            "The project scope is unclear",
            [PROJECT_TYPES, REQUIRED_INFORMATION],
        ),
    ],
)
def test_selects_minimal_policy_citations(eligibility, facts, message, expected):
    assert select_policy_citation_ids(eligibility, facts, message, ALL_DOCUMENTS) == expected


def test_never_selects_a_document_not_supplied_by_retrieval():
    selected = select_policy_citation_ids(
        EligibilityLabel.ELIGIBLE,
        ExtractedFacts(
            region="North",
            company_size="small",
            requested_amount_pln=50_000,
            project_summary="Prototype development",
        ),
        "Clear inquiry",
        {OVERVIEW, FUNDING_RANGE},
    )
    assert selected == [OVERVIEW, FUNDING_RANGE]


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            "We are somewhere in the northern area, a small business, and might need about 50000 PLN for a new product. Exact location and R&D scope are unclear.",
            [REGION, PROJECT_TYPES, REQUIRED_INFORMATION],
        ),
        (
            "We operate in North and request 90000 PLN for research; the message alternately describes us as small and large.",
            [COMPANY_SIZE, REQUIRED_INFORMATION],
        ),
        (
            "We are a small business in North, but the message does not clarify whether the requested 50000 PLN is the project total or the grant amount.",
            [FUNDING_RANGE, REQUIRED_INFORMATION],
        ),
        (
            "Small North business asks for 60000 PLN for an innovative prototype, but says the work may take place in either North or South.",
            [REGION, REQUIRED_INFORMATION],
        ),
    ],
)
def test_cites_policy_fields_explicitly_marked_uncertain_even_if_model_omits_other_facts(
    message, expected
):
    # Model omissions in unrelated fields must not drown out the explicit
    # policy ambiguity stated by the applicant.
    assert select_policy_citation_ids(
        EligibilityLabel.NEEDS_REVIEW,
        ExtractedFacts(),
        message,
        ALL_DOCUMENTS,
    ) == expected
