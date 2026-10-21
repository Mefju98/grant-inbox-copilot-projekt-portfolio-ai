# Portfolio demo script

This walkthrough takes about five minutes. Use only the fictional examples in this repository. The app demonstrates preliminary triage and human review; it does not make funding decisions.

## Before the demo

- Start the API and optional Streamlit UI as described in [README.md](README.md).
- Open the Streamlit page at `http://127.0.0.1:8501/`.
- Keep the API terminal visible if you want to show request handling and provider diagnostics.
- Do not use real applicant messages, personal data, or API keys in the UI or screenshots.

## Walkthrough

### 1. Explain the design (about 45 seconds)

Say: “The API validates a request, retrieves relevant rules from a small fictional knowledge base, and asks an LLM for a structured proposal only when evidence exists. Python then applies deterministic eligibility checks, validates citations, records a minimal audit summary, and escalates uncertain cases. The model proposes; it does not decide.”

Point out that the fixed pipeline is intentional: its steps are visible and testable, and a missing knowledge match fails closed without calling the model.

### 2. Show the no-match safety path (about 60 seconds)

1. Select **No knowledge match (human review)**.
2. Confirm the synthetic-data checkbox and run triage.
3. Show `needs_review`, `Human review: Required`, `no_knowledge_match`, no policy citations, and `Attempts: 0`.
4. In **Local human review queue**, show the new pending item and its reason. Mark it reviewed and show the reviewed count.

Say: “Because the policy index had no relevant evidence, the system made no recommendation and skipped the LLM call. The local queue stores this synthetic message so a reviewer can inspect and track it.”

### 3. Show the evidence-backed path (about 90 seconds)

1. Select **Evidence match (fictional prototype)**.
2. Confirm the synthetic-data checkbox and run triage.
3. Expand **Extracted facts** and one or two **Policy evidence** items.
4. Point out the eligibility label, rationale, escalation status, model/prompt, latency, and attempts.

Say: “The response includes the extracted eligibility facts and the policy snippets used as evidence. The eligibility label is a preliminary result under fictional rules, not an award decision. If required facts are missing, evidence is unclear, or the rules disagree, the result goes to human review.”

### 4. Report evaluation honestly (about 60 seconds)

The latest recorded full local run is `outputs/evaluation-ollama-policy-citation-fix-recheck-full.json`: 50 frozen synthetic cases, zero API failures, 100% accuracy, schema validity, citation F1, and escalation F1. It used local Ollama model `qwen2.5:3b` with prompt `triage-v5`; mean latency was about 5.56 seconds and cost was unknown (`null`). The full local test suite passed with 98 tests on 2026-10-21. Pytest reported one Starlette deprecation warning about the legacy `httpx` test client integration.

Say: “These are results on a small synthetic set for this specific local model and prompt. They do not establish real-world performance. The intermittent case-020 502 seen in an earlier run was not reproduced in the later 50-case run or six isolated requests, so its original cause remains unconfirmed.”

### 5. Close with limitations (about 45 seconds)

- Retrieval is lexical BM25 over a small corpus; it can miss paraphrases.
- Citation validation checks that IDs came from retrieved documents, but does not prove that each citation entails every generated claim.
- The review queue is a local SQLite demo with no access controls or retention automation; it is only for synthetic data.
- A production handoff would need an approved destination, authentication, access controls, and retention policy.

## Optional n8n note

The included n8n workflow demonstrates a webhook and an escalation branch. It returns the escalation outcome but does not create a ticket or send email. The human-review Test URL was verified once while its one-shot listener was active.
