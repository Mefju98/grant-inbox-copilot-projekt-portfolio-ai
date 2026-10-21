"""Transparent deterministic baseline for one fictional grant program."""

import re

from app.schemas import (
    EligibilityLabel,
    EscalationReason,
    ExtractedFacts,
    TriageResult,
)


PROGRAM_ID = "fictional-north-innovation-v1"
ELIGIBLE_REGIONS = {"north"}
ELIGIBLE_COMPANY_SIZES = {"micro", "small"}
MIN_AMOUNT_PLN = 10_000
MAX_AMOUNT_PLN = 200_000
PROJECT_SIGNAL_WORDS = (
    "research", "experimental development", "development", "develop",
    "innovation", "innovative", "prototype", "build", "validat", "test",
)
ROUTINE_PROJECT_PATTERN = re.compile(
    r"\b(?:routine|standard|ordinary)\b.{0,80}\b(?:renovation|refurbishment|"
    r"purchases?|furniture|configuration|adaptation)\b",
    re.IGNORECASE,
)
NO_RD_PATTERN = re.compile(
    r"\bno\s+(?:research|development|innovation|prototype)\b",
    re.IGNORECASE,
)
REQUIRED_FACT_FIELDS = ("region", "company_size", "requested_amount_pln", "project_summary")


def classify_facts(
    inquiry_id: str,
    facts: ExtractedFacts,
    inquiry_text: str | None = None,
) -> TriageResult:
    """Classify extracted facts against a declared toy policy.

    This is a transparent baseline, not legal or funding advice. Because this
    stage has no retrieved knowledge documents, every result is escalated and
    no recommendation is issued, even when the rule outcome is clear.
    """

    # Derive missing eligibility facts from validated values. The LLM-provided
    # facts_missing list is untrusted and can contradict populated fields or
    # include identity details that are not part of this toy program's policy.
    missing = [name for name in REQUIRED_FACT_FIELDS if getattr(facts, name) is None]
    reasons: list[EscalationReason] = [EscalationReason.NO_KNOWLEDGE_MATCH]
    region = (facts.region or "").strip().casefold()
    company_size = (facts.company_size or "").strip().casefold()
    amount = facts.requested_amount_pln
    project_text = " ".join(value or "" for value in (facts.project_summary, inquiry_text)).casefold()
    known_failures = []
    if region and region not in ELIGIBLE_REGIONS:
        known_failures.append("operating region is outside North")
    if company_size and company_size not in ELIGIBLE_COMPANY_SIZES:
        known_failures.append("company size is not micro or small")
    if amount is not None and not MIN_AMOUNT_PLN <= amount <= MAX_AMOUNT_PLN:
        known_failures.append(f"requested amount is outside {MIN_AMOUNT_PLN}-{MAX_AMOUNT_PLN} PLN")
    if ROUTINE_PROJECT_PATTERN.search(project_text) or NO_RD_PATTERN.search(project_text):
        known_failures.append("project scope is explicitly routine or outside R&D")

    if known_failures:
        # A known failed criterion decides eligibility even when another fact
        # is missing; the policy table says review only when no clear failure
        # already determines the outcome.
        eligibility = EligibilityLabel.INELIGIBLE
        rationale = (
            f"The inquiry fails a declared rule for {PROGRAM_ID}: "
            + "; ".join(known_failures)
            + "."
        )
    elif missing:
        reasons.append(EscalationReason.MISSING_INFORMATION)
        eligibility = EligibilityLabel.NEEDS_REVIEW
        rationale = "Required eligibility facts are missing: " + ", ".join(missing) + "."
    else:
        has_project_signal = any(word in project_text for word in PROJECT_SIGNAL_WORDS)
        if not has_project_signal:
            eligibility = EligibilityLabel.NEEDS_REVIEW
            reasons.append(EscalationReason.AMBIGUOUS_INPUT)
            rationale = "The project summary does not contain a recognized R&D or innovation signal; human review is required."
        else:
            eligibility = EligibilityLabel.ELIGIBLE
            rationale = f"The supplied facts match the declared baseline rules for {PROGRAM_ID}."

    return TriageResult(
        inquiry_id=inquiry_id,
        eligibility=eligibility,
        recommendation=None,
        rationale=rationale + " No knowledge-base citation is available yet, so this result is escalated for human review.",
        extracted_facts=facts,
        citations=[],
        escalate=True,
        escalation_reasons=reasons,
    )
