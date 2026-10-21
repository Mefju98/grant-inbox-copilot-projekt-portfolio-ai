# Design decisions

## 1. Synthetic fictional domain

All inquiry messages, program rules, and knowledge documents are fabricated. This makes the project safe to share as a portfolio sample and avoids implying that it evaluates real grant eligibility.

## 2. Fixed pipeline instead of an agent framework

The task has a known sequence: validate, retrieve, ask for a structured draft, check policy, audit, return. A direct Python pipeline is easier to inspect, test, and explain than an agent that chooses its own tools or steps.

## 3. LLM is a proposal generator

Pydantic validates the model's shape. Retrieved-ID checks, deterministic eligibility rules, citation selection, and escalation policy remain in Python. The deterministic policy owns the final eligibility label when required facts are complete and unambiguous. A model's `needs_review` is an abstention, not a contradictory decision; it does not alone force escalation when policy facts are complete and the model supplied a grounded recommendation. A contradictory definitive model label (`eligible` versus `ineligible`) still escalates and suppresses the recommendation. Direct uncertainty phrases in the inquiry (for example, "unclear" or "conflicting statements") are separately caught by a small lexical guardrail and escalated even if all four required fields were extracted. This guardrail is intentionally narrow and does not replace semantic ambiguity detection. Citation IDs are selected from retrieved policy documents based on the final rule outcome and the specific failed or missing criteria, rather than trusting the model's tendency to cite every retrieved document. A schema-valid answer is not automatically a correct answer.

When one policy fact clearly fails a rule (for example, a known amount below the minimum), that fact decides `ineligible` even if another field is missing. This follows the decision table: missing information leads to `needs_review` only when no known rule already decides the case. A definitive ineligible outcome does not need a model-generated recommendation and is returned without escalation unless the inquiry contains explicit ambiguity or the model makes a contradictory definitive claim.

## 4. Evidence-first and fail closed

The API searches the fictional knowledge base before calling the LLM. With no matches, it skips the provider, returns no recommendation, and escalates. This bounds unsupported claims and avoids spending a model call when the corpus has no relevant evidence.

## 5. BM25 for the small first corpus

The corpus is small and text-based, so a standard-library lexical ranker keeps installation and runtime simple. It is a baseline; it will miss paraphrases and should be compared with semantic retrieval before any production use.

## 6. Do not treat model confidence as probability

The output keeps the model-supplied score only as an uncalibrated diagnostic. No threshold uses it to make an eligibility decision. A calibrated probability would require a separate calibration dataset and methodology.

## 7. Retry only transient failures

Network failures and selected transient HTTP statuses can be retried, with a hard cap. Invalid JSON or schema output is not retried, avoiding repeated calls that may return the same malformed response and consume more budget.

## 8. Minimal SQLite audit data

The audit records identifiers, outcome, model/prompt metadata, timing, tokens, cost, citations, and error codes. It intentionally excludes the raw message and extracted identifiers. This reduces stored content but does not replace a retention policy or access controls.

## 9. Gold labels are frozen before model runs

The 50 cases and their expected labels/citations live in a static JSONL file. They are not generated or edited from observed model output. This helps prevent evaluation leakage, although the small synthetic set does not establish real-world performance.

## 10. Provider compatibility is not provider equivalence

One Chat Completions-shaped client reduces integration code and supports compatible endpoints, including intended Ollama usage. JSON-mode support, latency, and output quality vary by endpoint/model and must be tested and reported separately.

## 11. Automation artifacts have honest portability claims

The n8n workflow is a portable JSON starting point with a local-network URL that may need adjustment. Power Automate is a manual setup guide because connector, tenant, endpoint, and authentication details vary by environment. Neither artifact is described as a configured human queue integration.

## 12. Use deterministic extraction for narrow, explicit fields

The local 3B model often omitted an explicitly stated company size. A small Python fallback fills that field only when a single size label is directly attached to a business noun (for example, "small company"). It does not infer size from funding or project details, and conflicting size phrases remain unresolved for human review. The policy `facts_missing` list is rebuilt from the four actual eligibility fields, so identity details such as tax ID cannot create a false eligibility escalation.

## 13. Keep the demo review queue local and synthetic

The optional Streamlit demo can store escalated synthetic inquiries in a local SQLite review queue so a person can inspect the message, rationale, and policy citations and mark the case reviewed. The UI requires explicit confirmation that input is synthetic before sending it to the API or storing it. The queue has no access controls or retention automation and is not suitable for real applicant data. A production handoff needs an organization-approved destination and its access and retention policies.
