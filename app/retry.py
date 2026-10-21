"""Small bounded retry policy for transient provider failures only."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from time import perf_counter, sleep
from typing import Any, Callable

from app.llm import LLMCallResult, LLMProviderError

logger = logging.getLogger("app.llm.attempts")

# Only these keys are ever copied from client diagnostics into logs or API errors.
_RECORD_DIAGNOSTIC_KEYS = (
    "model",
    "prompt_version",
    "prompt_chars",
    "evidence_docs",
    "timeouts",
    "http_status",
    "response_bytes",
    "content_chars",
    "prompt_tokens",
    "completion_tokens",
    "finish_reason",
    "duration_ms",
    "exc_type",
)
_SUMMARY_KEYS = (
    "attempt",
    "outcome",
    "duration_ms",
    "http_status",
    "finish_reason",
    "completion_tokens",
    "will_retry",
)


class RetryExhausted(LLMProviderError):
    def __init__(self, cause: LLMProviderError, attempts: int, summaries: list[dict[str, Any]] | None = None):
        super().__init__(
            str(cause),
            retryable=False,
            kind=getattr(cause, "kind", "unknown"),
            http_status=getattr(cause, "http_status", None),
        )
        self.attempt_count = attempts
        self.original_error = cause
        self.attempt_summaries = list(summaries or [])

    @property
    def failure_kind(self) -> str:
        return self.kind


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 2
    base_delay_seconds: float = 0.2
    max_delay_seconds: float = 1.0

    def __post_init__(self) -> None:
        if not 1 <= self.max_attempts <= 3:
            raise ValueError("max_attempts must be between 1 and 3")
        if self.base_delay_seconds < 0 or self.max_delay_seconds < self.base_delay_seconds:
            raise ValueError("Retry delays must be non-negative and max_delay >= base_delay")


def configure_attempt_logging(level: int = logging.INFO) -> None:
    """Make per-attempt records visible under uvicorn, which leaves app loggers unconfigured."""

    logger.setLevel(level)
    if not logger.handlers and not logging.getLogger().handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)


def call_with_retry(
    client,
    message: str,
    policy: RetryPolicy = RetryPolicy(),
    sleep_fn: Callable[[float], None] = sleep,
    evidence=None,
    request_id: str | None = None,
) -> LLMCallResult:
    """Retry transient transport/provider errors, never invalid model output."""

    started = perf_counter()
    summaries: list[dict[str, Any]] = []
    for attempt in range(1, policy.max_attempts + 1):
        attempt_started = perf_counter()
        try:
            result = client.triage(message, evidence=evidence)
        except LLMProviderError as exc:
            will_retry = exc.retryable and attempt < policy.max_attempts
            delay = (
                min(policy.base_delay_seconds * (2 ** (attempt - 1)), policy.max_delay_seconds)
                if will_retry
                else 0.0
            )
            summaries.append(
                _emit_attempt(
                    client,
                    request_id=request_id,
                    attempt=attempt,
                    max_attempts=policy.max_attempts,
                    outcome=exc.kind,
                    retryable=exc.retryable,
                    will_retry=will_retry,
                    backoff_ms_after=round(delay * 1000),
                    attempt_started=attempt_started,
                    request_started=started,
                    http_status=exc.http_status,
                )
            )
            if not will_retry:
                raise RetryExhausted(exc, attempts=attempt, summaries=summaries) from exc
            sleep_fn(delay)
            continue
        _emit_attempt(
            client,
            request_id=request_id,
            attempt=attempt,
            max_attempts=policy.max_attempts,
            outcome="ok",
            retryable=False,
            will_retry=False,
            backoff_ms_after=0,
            attempt_started=attempt_started,
            request_started=started,
            http_status=None,
        )
        return replace(
            result,
            attempts=attempt,
            latency_ms=round((perf_counter() - started) * 1000),
        )
    raise AssertionError("Retry loop exited unexpectedly")


def _emit_attempt(
    client,
    *,
    request_id,
    attempt,
    max_attempts,
    outcome,
    retryable,
    will_retry,
    backoff_ms_after,
    attempt_started,
    request_started,
    http_status,
) -> dict[str, Any]:
    now = perf_counter()
    record: dict[str, Any] = {
        "event": "llm_attempt",
        "ts": datetime.now(timezone.utc).isoformat(),
        "request_id": request_id,
        "attempt": attempt,
        "max_attempts": max_attempts,
        "outcome": outcome,
        "retryable": retryable,
        "will_retry": will_retry,
        "backoff_ms_after": backoff_ms_after,
        "wall_ms": round((now - attempt_started) * 1000),
        "elapsed_ms": round((now - request_started) * 1000),
    }
    diagnostics = getattr(client, "last_diagnostics", None)
    if isinstance(diagnostics, dict):
        for key in _RECORD_DIAGNOSTIC_KEYS:
            if key in diagnostics:
                record[key] = diagnostics[key]
    if record.get("http_status") is None and http_status is not None:
        record["http_status"] = http_status
    level = logging.INFO if outcome == "ok" else logging.WARNING
    logger.log(level, json.dumps(record, sort_keys=True, default=str))
    summary_source = {**record, "duration_ms": record.get("duration_ms", record["wall_ms"])}
    return {key: summary_source.get(key) for key in _SUMMARY_KEYS}
