"""Select the smallest rule citation set for a deterministic triage outcome."""

from __future__ import annotations

import re

from app.schemas import EligibilityLabel, ExtractedFacts


OVERVIEW = "fictional-program-overview-v1"
REGION = "eligible-regions-v1"
COMPANY_SIZE = "eligible-company-sizes-v1"
FUNDING_RANGE = "funding-range-v1"
PROJECT_TYPES = "eligible-project-types-v1"
REQUIRED_INFORMATION = "required-information-v1"
ROUTINE_SCOPE = re.compile(
    r"\b(?:routine|standard|ordinary)\b.{0,60}\b(?:renovation|refurbishment|"
    r"furniture|purchase|configuration|adaptation)\b",
    re.IGNORECASE,
)
UNCERTAIN_AMOUNT = re.compile(
    r"\beither\b.{0,60}\b(?:PLN\s*)?\d[\d,]*(?:\.\d+)?\s*(?:PLN)?\b"
    r".{0,60}\bor\b.{0,30}\b(?:PLN\s*)?\d"
    r"|\bbetween\s+(?:PLN\s*)?\d[\d,]*(?:\.\d+)?\s*(?:PLN)?\s+and\s+\d"
    r"|\b(?:amount|budget)\b.{0,60}\b(?:still being decided|not yet known|undecided)\b",
    # The amount can be numerically clear while its meaning is not: policy
    # requires distinguishing the grant request from total project cost.
    re.IGNORECASE,
)
UNCERTAIN_AMOUNT_MEANING = re.compile(
    r"\b(?:does not|doesn't|cannot|can't)\s+clarif\w*.{0,100}"
    r"\b(?:project\s+total|total\s+project\s+cost)\b.{0,100}\bgrant\s+amount\b"
    r"|\b(?:project\s+total|total\s+project\s+cost)\b.{0,100}\b(?:requested\s+)?grant\s+amount\b",
    re.IGNORECASE,
)
CONFLICTING_COMPANY_SIZE = re.compile(
    r"\bboth\b.{0,60}\b(?:micro|small|medium(?:-sized)?|large)\b"
    r".{0,30}\band\b.{0,30}\b(?:micro|small|medium(?:-sized)?|large)\b",
    # Also cover prose that states the applicant is described differently
    # across the message rather than using the word "both".
    re.IGNORECASE,
)
ALTERNATING_COMPANY_SIZE = re.compile(
    r"\b(?:alternately|variously)\s+describ\w*.{0,60}\b(?:as\s+)?"
    r"(?:micro|small|medium(?:-sized)?|large)\b.{0,40}\b(?:and|or)\s+"
    r"(?:micro|small|medium(?:-sized)?|large)\b",
    re.IGNORECASE,
)
UNCERTAIN_REGION = re.compile(
    r"\b(?:somewhere|exact|operating|work)\s+(?:location|region)\b.{0,60}"
    r"\b(?:unclear|uncertain|unknown|not stated|not specified)\b"
    r"|\b(?:location|region)\b.{0,60}\b(?:unclear|uncertain|unknown|not stated|not specified)\b"
    r"|\b(?:either|between)\b.{0,100}\b(?:North|South|East|West)\b.{0,80}\b(?:or|and)\b.{0,30}\b(?:North|South|East|West)\b",
    re.IGNORECASE,
)
UNCERTAIN_PROJECT_SCOPE = re.compile(
    r"\b(?:R\s*&\s*D|project|activity|research|development|scope)\b.{0,100}"
    r"\b(?:unclear|uncertain|ambiguous|conflicting|contradictory|not described|does not describe)\b"
    r"|\b(?:unclear|uncertain|ambiguous|conflicting|contradictory)\b.{0,100}"
    r"\b(?:R\s*&\s*D|project|activity|research|development|scope)\b"
    r"|\b(?:whether|if)\b.{0,120}\b(?:research|experimental development|routine|prototype|project scope)\b",
    re.IGNORECASE,
)


def select_policy_citation_ids(
    eligibility: EligibilityLabel,
    facts: ExtractedFacts,
    inquiry_text: str,
    available_document_ids: set[str],
) -> list[str]:
    """Choose relevant retrieved rules without relying on model citation recall.

    Citation selection follows the same fictional policy criteria as the
    deterministic eligibility decision. IDs are always filtered to the
    documents that retrieval actually supplied.
    """

    candidates: list[str]
    if eligibility == EligibilityLabel.ELIGIBLE:
        candidates = [OVERVIEW, REGION, COMPANY_SIZE, FUNDING_RANGE, PROJECT_TYPES]
    elif eligibility == EligibilityLabel.INELIGIBLE:
        candidates = []
        if (facts.region or "").strip().casefold() != "north":
            candidates.append(REGION)
        if (facts.company_size or "").strip().casefold() not in {"micro", "small"}:
            candidates.append(COMPANY_SIZE)
        if facts.requested_amount_pln is not None and not 10_000 <= facts.requested_amount_pln <= 200_000:
            candidates.append(FUNDING_RANGE)
        if ROUTINE_SCOPE.search(inquiry_text):
            candidates.append(PROJECT_TYPES)
        # An ineligible result should have a concrete failed rule. If one is
        # not represented in extracted facts, retain a general program source.
        if not candidates:
            candidates = [OVERVIEW]
    else:
        # Prefer fields that the message itself marks as unresolved over
        # incidental omissions from model extraction. This keeps the citations
        # tied to the reason for review (e.g. an ambiguous amount) rather than
        # to a field the small model happened to leave blank.
        candidates = _explicitly_uncertain_policy_documents(inquiry_text)
        if not candidates:
            field_documents = {
                "region": REGION,
                "company_size": COMPANY_SIZE,
                "requested_amount_pln": FUNDING_RANGE,
                "project_summary": PROJECT_TYPES,
            }
            candidates = [
                field_documents[field]
                for field in field_documents
                if getattr(facts, field) is None
            ]
            if not candidates:
                candidates = [PROJECT_TYPES]
        candidates.append(REQUIRED_INFORMATION)

    return list(dict.fromkeys(document_id for document_id in candidates if document_id in available_document_ids))


def _explicitly_uncertain_policy_documents(inquiry_text: str) -> list[str]:
    documents: list[str] = []
    if UNCERTAIN_REGION.search(inquiry_text):
        documents.append(REGION)
    if CONFLICTING_COMPANY_SIZE.search(inquiry_text) or ALTERNATING_COMPANY_SIZE.search(inquiry_text):
        documents.append(COMPANY_SIZE)
    if UNCERTAIN_AMOUNT.search(inquiry_text) or UNCERTAIN_AMOUNT_MEANING.search(inquiry_text):
        documents.append(FUNDING_RANGE)
    if UNCERTAIN_PROJECT_SCOPE.search(inquiry_text):
        documents.append(PROJECT_TYPES)
    return documents
