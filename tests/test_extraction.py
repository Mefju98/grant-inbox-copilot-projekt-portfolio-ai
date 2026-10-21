import pytest

from app.extraction import has_explicit_uncertainty, reconcile_policy_facts
from app.schemas import ExtractedFacts


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("A small company operates in North.", "small"),
        ("Our micro business is based in North.", "micro"),
        ("A medium-sized enterprise requests funding.", "medium"),
        ("Large North-region manufacturer seeks support.", "large"),
        ("North micro-enterprise seeks support.", "micro"),
    ],
)
def test_fills_explicit_company_size_from_business_phrase(message, expected):
    facts = reconcile_policy_facts(message, ExtractedFacts())
    assert facts.company_size == expected


def test_does_not_infer_company_size_from_project_description():
    facts = reconcile_policy_facts(
        "A company plans to build a small prototype in North.", ExtractedFacts()
    )
    assert facts.company_size is None


def test_fills_unambiguous_region_from_explicit_operating_location():
    facts = reconcile_policy_facts(
        "We operate in North and request 90000 PLN for research.", ExtractedFacts()
    )
    assert facts.region == "North"


def test_does_not_infer_region_from_vague_or_uncertain_location():
    facts = reconcile_policy_facts(
        "We are somewhere in the northern area. Exact location is unclear.",
        ExtractedFacts(region="North"),
    )
    assert facts.region is None


def test_leaves_conflicting_company_sizes_unresolved():
    facts = reconcile_policy_facts(
        "A small company and a large enterprise are applying together.", ExtractedFacts()
    )
    assert facts.company_size is None


def test_does_not_replace_a_model_extraction():
    facts = reconcile_policy_facts(
        "A small company operates in North.", ExtractedFacts(company_size="micro")
    )
    assert facts.company_size == "micro"


def test_rebuilds_missing_list_from_policy_fields_only():
    facts = reconcile_policy_facts(
        "A small company operates in North.",
        ExtractedFacts(
            region="North",
            company_size=None,
            requested_amount_pln=50_000,
            project_summary="Development of a prototype",
            facts_missing=["company_size", "company_name", "tax_id", "industry"],
        ),
    )
    assert facts.company_size == "small"
    assert facts.facts_missing == []


def test_reports_only_truly_missing_policy_fields():
    facts = reconcile_policy_facts(
        "Company size is not provided.",
        ExtractedFacts(
            region="North",
            requested_amount_pln=50_000,
            project_summary="Development of a prototype",
            facts_missing=["tax_id", "industry"],
        ),
    )
    assert facts.company_size is None
    assert facts.facts_missing == ["company_size"]


@pytest.mark.parametrize(
    "message",
    [
        "The cost split is unclear.",
        "The project has contradictory goals and needs clarification.",
        "It is unclear whether the work is experimental development.",
        "There are conflicting statements about whether the prototype exists.",
    ],
)
def test_detects_explicit_uncertainty_phrases(message):
    assert has_explicit_uncertainty(message) is True


def test_does_not_mark_a_clear_project_description_as_uncertain():
    assert has_explicit_uncertainty(
        "A small company in North requests 50000 PLN to develop a prototype."
    ) is False
