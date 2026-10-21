"""Conservative extraction helpers for facts stated explicitly in an inquiry."""

from __future__ import annotations

import re

from app.schemas import ExtractedFacts


_SIZE_TO_CANONICAL = {
    "micro": "micro",
    "micro-sized": "micro",
    "small": "small",
    "medium": "medium",
    "medium-sized": "medium",
    "large": "large",
}
_BUSINESS_NOUN = r"(?:company|business|enterprise|firm|corporation|manufacturer|startup|sme)"
_SIZE_BEFORE_BUSINESS = re.compile(
    rf"\b(?P<size>micro(?:-sized)?|small|medium(?:-sized)?|large)\b"
    rf"(?:[\s-]+[\w]+){{0,3}}[\s-]+\b{_BUSINESS_NOUN}\b",
    re.IGNORECASE,
)
_REGION_IN_CONTEXT = re.compile(
    r"\b(?:operate|operating|based|located|work|works|business)\s+(?:in|from)\s+"
    r"(?:the\s+)?(?P<region>North|South|East|West)\b"
    r"|\b(?P<region_after>North|South|East|West)[- ]region\b",
    re.IGNORECASE,
)
_CONFLICTING_REGION = re.compile(
    r"\b(?:either|between)\b.{0,100}\b(?:North|South|East|West)\b"
    r".{0,80}\b(?:or|and)\b.{0,30}\b(?:North|South|East|West)\b",
    re.IGNORECASE,
)
_UNCLEAR_REGION = re.compile(
    r"\b(?:exact\s+)?(?:operating\s+)?(?:location|region)\b.{0,60}"
    r"\b(?:unclear|uncertain|unknown|not stated|not specified)\b"
    r"|\b(?:unclear|uncertain|unknown)\s+(?:exact\s+)?(?:location|region)\b",
    re.IGNORECASE,
)
_PROJECT_ACTIVITY = re.compile(
    r"\b(?:applied\s+research|experimental\s+development|research|"
    r"development|develop\w*|innovation|innovative|prototype|build\w*|"
    r"validat\w*|test\w*)\b",
    re.IGNORECASE,
)
_CLAUSE_SPLIT = re.compile(r"[.;:\n]+")
_EXPLICIT_UNCERTAINTY = re.compile(
    r"\b(?:unclear|uncertain|ambiguous|ambiguity|contradict\w*|conflict\w*)\b"
    r"|\bneeds?\s+clarification\b",
    # Explicit alternatives, ranges, and unresolved budgets/sizes must not be
    # reduced to whichever single value the model happened to extract.
    re.IGNORECASE,
)
_EXPLICIT_ALTERNATIVE = re.compile(
    r"\beither\b.{0,120}\bor\b"
    r"|\bbetween\s+(?:PLN\s*)?\d[\d,]*(?:\.\d+)?\s*(?:PLN)?\s+and\s+\d"
    r"|\b(?:amount|budget)\b.{0,60}\b(?:still being decided|not yet known|undecided)\b"
    r"|\bboth\b.{0,60}\b(?:micro|small|medium(?:-sized)?|large)\b"
    r".{0,30}\band\b.{0,30}\b(?:micro|small|medium(?:-sized)?|large)\b",
    re.IGNORECASE,
)


_REQUIRED_FIELDS = ("region", "company_size", "requested_amount_pln", "project_summary")
_NULL_TEXT_VALUES = {"", "null", "none", "unknown", "n/a", "not provided"}


def reconcile_policy_facts(message: str, facts: ExtractedFacts) -> ExtractedFacts:
    """Fill explicit company size and reconcile the policy-missing list.

    This does not infer size from industry or funding amount. If multiple sizes
    are explicitly associated with businesses, the field remains unresolved so
    the ordinary human-review guardrail can handle the conflict. ``facts_missing``
    is then rebuilt from actual policy field values, removing contradictions and
    non-policy identity fields reported by the model.
    """

    # Some local models serialize JSON null as the literal string "null".
    # Treat common placeholder strings as missing before checking the policy,
    # otherwise (for example) the company size "null" is mistaken for an
    # explicit disqualifying size.
    updates = {}
    for field in ("company_name", "tax_id", "region", "industry", "project_summary", "company_size"):
        value = getattr(facts, field)
        if isinstance(value, str) and value.strip().casefold() in _NULL_TEXT_VALUES:
            updates[field] = None

    normalized = facts.model_copy(update=updates) if updates else facts
    if _CONFLICTING_REGION.search(message) or _UNCLEAR_REGION.search(message):
        updates["region"] = None
    elif not (normalized.region and normalized.region.strip()):
        region_matches = {
            (match.group("region") or match.group("region_after")).casefold()
            for match in _REGION_IN_CONTEXT.finditer(message)
        }
        if len(region_matches) == 1:
            updates["region"] = region_matches.pop().title()

    if not (normalized.project_summary and normalized.project_summary.strip()):
        explicit_summary = _explicit_project_summary(message)
        if explicit_summary:
            updates["project_summary"] = explicit_summary

    if not (normalized.company_size and normalized.company_size.strip()):
        candidates: set[str] = set()
        for clause in _CLAUSE_SPLIT.split(message):
            for match in _SIZE_BEFORE_BUSINESS.finditer(clause):
                candidates.add(_SIZE_TO_CANONICAL[match.group("size").casefold()])
        if len(candidates) == 1:
            updates["company_size"] = candidates.pop()

    reconciled = facts.model_copy(update=updates) if updates else normalized
    missing = [field for field in _REQUIRED_FIELDS if getattr(reconciled, field) is None]
    return reconciled.model_copy(update={"facts_missing": missing})


def has_explicit_uncertainty(message: str) -> bool:
    """Detect direct wording that says the inquiry contains unresolved facts.

    This narrow lexical guardrail supplements model extraction; it is not a
    general natural-language ambiguity classifier.
    """

    return (
        _EXPLICIT_UNCERTAINTY.search(message) is not None
        or _EXPLICIT_ALTERNATIVE.search(message) is not None
    )


def _explicit_project_summary(message: str) -> str | None:
    """Return a literal project-activity phrase when the model omitted it."""

    match = _PROJECT_ACTIVITY.search(message)
    if match is None:
        return None
    end = re.search(r"[.;:\n]", message[match.start():])
    phrase_end = match.start() + end.start() if end else len(message)
    summary = message[match.start():phrase_end].strip(" ,:-")
    return summary or None
