import pytest

from app.llm import LLMCallResult, LLMProviderError, PROMPT_VERSION
from app.retry import RetryExhausted, RetryPolicy, call_with_retry
from app.schemas import EligibilityLabel, ExtractedFacts, ModelTriageDraft


def _result():
    return LLMCallResult(
        draft=ModelTriageDraft(
            eligibility=EligibilityLabel.NEEDS_REVIEW,
            rationale="Need clarification.",
            extracted_facts=ExtractedFacts(),
        ),
        model="fake-model",
        prompt_version=PROMPT_VERSION,
        latency_ms=1,
    )


def test_transient_failure_is_retried_once_and_attempts_are_reported():
    class FlakyClient:
        calls = 0

        def triage(self, message, evidence=None):
            self.calls += 1
            if self.calls == 1:
                raise LLMProviderError("temporary outage", retryable=True)
            return _result()

    client = FlakyClient()
    delays = []
    result = call_with_retry(client, "synthetic", sleep_fn=delays.append)
    assert client.calls == 2
    assert result.attempts == 2
    assert delays == [0.2]


def test_invalid_output_is_not_retried():
    class InvalidClient:
        calls = 0

        def triage(self, message, evidence=None):
            self.calls += 1
            raise LLMProviderError("invalid schema", retryable=False)

    client = InvalidClient()
    with pytest.raises(RetryExhausted) as error:
        call_with_retry(client, "synthetic", sleep_fn=lambda _: None)
    assert error.value.attempt_count == 1
    assert client.calls == 1


def test_retry_policy_has_a_small_hard_limit():
    with pytest.raises(ValueError, match="between 1 and 3"):
        RetryPolicy(max_attempts=4)
