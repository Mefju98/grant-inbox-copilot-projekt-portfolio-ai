# Benchmark history

This page keeps the dated Ollama benchmark notes out of the main README. All cases are synthetic and use the fictional program. Metrics are useful for comparing code changes on this fixed set; they are not evidence of real-world funding-decision performance. Cost is `null` when the local provider does not return enough usage or pricing information to estimate it.

## 2026-10-20: earlier full runs

An earlier 50-case run against `qwen2.5:3b` with `triage-v5` completed without API failures. Accuracy, schema validity, and escalation F1 were 1.000; citation F1 was 0.982. One case took 52.46 seconds and used two attempts. The report is [`evaluation-ollama-policy-after-timeout-fix-full.json`](../outputs/evaluation-ollama-policy-after-timeout-fix-full.json).

A later full run had one API failure: `case-020` returned HTTP 502 `llm_provider_error` after 94.39 seconds. Across all cases, accuracy was 0.980, schema validity 98%, citation F1 0.993, and escalation F1 0.970. The 49 successful responses had accuracy, macro F1, and escalation F1 of 1.000, with citation F1 0.996. The remaining citation mismatch was an extra funding-range citation in `case-045`; the amount-alternative pattern was subsequently narrowed to numeric alternatives. Mean latency was 6.35 seconds. The report is [`evaluation-ollama-policy-citation-fix-full.json`](../outputs/evaluation-ollama-policy-citation-fix-full.json).

The citation selector, explicit region fallback, and BM25 query expansion for common policy synonyms were adjusted after earlier mismatches. A targeted recheck of cases 039, 043, 047, and 045 then had zero failures and 1.000 classification, schema, citation, and escalation metrics. Mean latency was 5.89 seconds. The report is [`evaluation-citation-recheck-v2.json`](../outputs/evaluation-citation-recheck-v2.json).

`case-020` also passed isolated retries, including a 10.45-second single-attempt run, but another full run failed on that case. Per-attempt diagnostics were added to correlate benchmark failures with Uvicorn logs.

## 2026-10-20: full recheck with diagnostics

The subsequent full 50-case run completed with zero API failures. Accuracy, schema validity, citation F1, and escalation F1 were all 1.000; mean latency was 5.56 seconds. Its report is [`evaluation-ollama-policy-citation-fix-recheck-full.json`](../outputs/evaluation-ollama-policy-citation-fix-recheck-full.json).

The intermittent `case-020` failure was not reproduced, so its root cause remains unconfirmed. Diagnostics expose attempt categories and response metadata without logging inquiry text, prompts, retrieved text, response bodies, or API keys.

## 2026-10-21: automated tests

The full local test suite passed with 98 tests. GitHub Actions now runs the same suite on Python 3.11 and 3.12. Automated tests use mocked model calls; they do not repeat the live Ollama benchmark.

## 2026-10-21: isolated `case-020` follow-up

After the full recheck, `case-020` was run by itself six more times against the local API and Ollama. All six returned HTTP 200 in one attempt. Latencies ranged from 4.57 to 9.59 seconds. The reports are [`evaluation-case-020-followup.json`](../outputs/evaluation-case-020-followup.json) and [`evaluation-case-020-repeat-1.json`](../outputs/evaluation-case-020-repeat-1.json) through [`evaluation-case-020-repeat-5.json`](../outputs/evaluation-case-020-repeat-5.json).

These isolated successes confirm that the current path is healthy, but they do not reproduce or explain the earlier 502 seen during a full benchmark. The root cause remains unconfirmed; the per-attempt diagnostics are still needed if the intermittent failure returns.
