"""Tests for per-attempt LLM diagnostics: classification, logging, redaction, 502 body.

Run with: pytest tests/test_attempt_diagnostics.py
Assumes the repo's real ``app.schemas`` (``ModelTriageDraft`` is replaced by a fake
here, so the tests do not depend on its fields).
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import httpx
import pytest
from pydantic import BaseModel

from app import llm
from app.llm import LLMProviderError, LLMSettings, OpenAICompatibleClient
from app.retry import RetryExhausted, RetryPolicy, call_with_retry

ATTEMPT_LOGGER = "app.llm.attempts"
CANARIES = ("CANARY-MSG", "CANARY-EVID", "CANARY-TITLE", "CANARY-KEY", "CANARY-ERR", "CANARY-OUT")

EVIDENCE = [
    SimpleNamespace(
        document_id="doc-1",
        title="CANARY-TITLE",
        source_version="v1",
        content="CANARY-EVID",
    )
]


class _FakeDraft:
    @staticmethod
    def model_validate(data):
        return SimpleNamespace(**data)


class _StrictModel(BaseModel):
    x: int


class _SchemaFailDraft:
    @staticmethod
    def model_validate(data):
        # The resulting ValidationError text contains the offending input value.
        return _StrictModel.model_validate({"x": "not-an-int CANARY-OUT"})


@pytest.fixture(autouse=True)
def fake_draft(monkeypatch):
    monkeypatch.setattr(llm, "ModelTriageDraft", _FakeDraft)


def make_client(handler, *, read_timeout: float = 30.0) -> OpenAICompatibleClient:
    settings = LLMSettings(
        base_url="http://ollama.test/v1",
        model="qwen2.5:3b",
        api_key="CANARY-KEY",
        timeout_seconds=30.0,
    )
    http_client = httpx.Client(
        transport=httpx.MockTransport(handler), timeout=httpx.Timeout(read_timeout)
    )
    return OpenAICompatibleClient(settings, http_client=http_client)


def ok_body(content='{"a": 1}', finish_reason="stop"):
    return {
        "model": "qwen2.5:3b",
        "choices": [{"message": {"content": content}, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20},
    }


def raising(exc_cls):
    def handler(request):
        raise exc_cls("CANARY-ERR boom", request=request)

    return handler


def attempt_records(caplog):
    return [json.loads(r.getMessage()) for r in caplog.records if r.name == ATTEMPT_LOGGER]


NO_DELAY = RetryPolicy(max_attempts=2, base_delay_seconds=0.2, max_delay_seconds=1.0)


@pytest.mark.parametrize(
    ("exc_cls", "kind"),
    [
        (httpx.ConnectTimeout, "timeout_connect"),
        (httpx.ReadTimeout, "timeout_read"),
        (httpx.WriteTimeout, "timeout_write"),
        (httpx.PoolTimeout, "timeout_pool"),
        (httpx.ConnectError, "transport_error"),
        (httpx.RemoteProtocolError, "transport_error"),
    ],
)
def test_transport_and_timeout_kinds(exc_cls, kind):
    client = make_client(raising(exc_cls))
    with pytest.raises(LLMProviderError) as info:
        client.triage("CANARY-MSG")
    assert info.value.kind == kind
    assert info.value.retryable is True
    assert client.last_diagnostics["exc_type"] == exc_cls.__name__
    assert "duration_ms" in client.last_diagnostics


@pytest.mark.parametrize(
    ("status", "kind", "retryable"),
    [
        (500, "http_5xx", True),
        (502, "http_5xx", True),
        (503, "http_5xx", True),
        (504, "http_5xx", True),
        (429, "http_4xx", True),
        (408, "http_4xx", True),
        (400, "http_4xx", False),
        (404, "http_4xx", False),
    ],
)
def test_http_status_kinds(status, kind, retryable):
    client = make_client(lambda request: httpx.Response(status, text="CANARY-ERR upstream"))
    with pytest.raises(LLMProviderError) as info:
        client.triage("CANARY-MSG")
    assert info.value.kind == kind
    assert info.value.retryable is retryable
    assert info.value.http_status == status
    assert client.last_diagnostics["http_status"] == status


def test_truncated_output_is_distinguished_from_invalid_json():
    truncated = make_client(
        lambda r: httpx.Response(200, json=ok_body('{"eligibility": "elig', "length"))
    )
    with pytest.raises(LLMProviderError) as info:
        truncated.triage("CANARY-MSG")
    assert info.value.kind == "output_truncated"
    assert info.value.retryable is False
    assert truncated.last_diagnostics["finish_reason"] == "length"
    assert truncated.last_diagnostics["completion_tokens"] == 20

    malformed = make_client(lambda r: httpx.Response(200, json=ok_body("not json", "stop")))
    with pytest.raises(LLMProviderError) as info:
        malformed.triage("CANARY-MSG")
    assert info.value.kind == "invalid_json"


def test_schema_and_empty_content_kinds(monkeypatch):
    monkeypatch.setattr(llm, "ModelTriageDraft", _SchemaFailDraft)
    client = make_client(lambda r: httpx.Response(200, json=ok_body('{"a": 1}')))
    with pytest.raises(LLMProviderError) as info:
        client.triage("CANARY-MSG")
    assert info.value.kind == "schema_invalid"
    assert "CANARY-OUT" not in str(info.value)

    empty = make_client(lambda r: httpx.Response(200, json=ok_body("   ")))
    with pytest.raises(LLMProviderError) as info:
        empty.triage("CANARY-MSG")
    assert info.value.kind == "empty_content"


def test_retry_then_success_logs_two_attempts(caplog):
    caplog.set_level(logging.INFO, logger=ATTEMPT_LOGGER)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, text="busy")
        return httpx.Response(200, json=ok_body())

    sleeps: list[float] = []
    result = call_with_retry(
        make_client(handler), "CANARY-MSG", NO_DELAY, sleep_fn=sleeps.append,
        request_id="req-1",
    )
    first, second = attempt_records(caplog)
    assert result.attempts == 2
    assert sleeps == [0.2]
    assert (first["attempt"], first["outcome"], first["will_retry"]) == (1, "http_5xx", True)
    assert first["http_status"] == 503 and first["backoff_ms_after"] == 200
    assert (second["attempt"], second["outcome"], second["will_retry"]) == (2, "ok", False)
    assert second["finish_reason"] == "stop" and second["completion_tokens"] == 20
    assert first["request_id"] == second["request_id"] == "req-1"


def test_two_timeouts_exhaust_retry_without_masking(caplog):
    caplog.set_level(logging.INFO, logger=ATTEMPT_LOGGER)
    with pytest.raises(RetryExhausted) as info:
        call_with_retry(
            make_client(raising(httpx.ReadTimeout)), "CANARY-MSG", NO_DELAY,
            sleep_fn=lambda s: None, request_id="req-2",
        )
    exc = info.value
    assert exc.attempt_count == 2
    assert exc.failure_kind == "timeout_read"
    assert [s["outcome"] for s in exc.attempt_summaries] == ["timeout_read", "timeout_read"]
    assert [s["will_retry"] for s in exc.attempt_summaries] == [True, False]
    assert len(attempt_records(caplog)) == 2


@pytest.mark.parametrize(
    "handler",
    [
        lambda r: httpx.Response(400, text="bad"),
        lambda r: httpx.Response(200, json=ok_body("not json")),
    ],
)
def test_non_retryable_failures_make_one_attempt(handler):
    sleeps: list[float] = []
    with pytest.raises(RetryExhausted) as info:
        call_with_retry(make_client(handler), "CANARY-MSG", NO_DELAY, sleep_fn=sleeps.append)
    assert info.value.attempt_count == 1
    assert sleeps == []
    assert info.value.attempt_summaries[0]["will_retry"] is False


def test_record_has_required_fields_and_effective_timeouts(caplog):
    caplog.set_level(logging.INFO, logger=ATTEMPT_LOGGER)
    # Injected client uses a 90 s read timeout while settings say 30 s:
    # the log must show what the HTTP client really enforces.
    client = make_client(raising(httpx.ReadTimeout), read_timeout=90.0)
    with pytest.raises(RetryExhausted):
        call_with_retry(
            client, "CANARY-MSG", RetryPolicy(max_attempts=1), request_id="req-3",
            evidence=EVIDENCE,
        )
    (record,) = attempt_records(caplog)
    required = {
        "event", "ts", "request_id", "attempt", "max_attempts", "outcome",
        "retryable", "will_retry", "backoff_ms_after", "wall_ms", "elapsed_ms", "model",
        "prompt_version", "prompt_chars", "evidence_docs", "timeouts", "duration_ms", "exc_type",
    }
    assert required <= record.keys()
    assert record["timeouts"]["read"] == 90.0
    assert record["prompt_chars"] > 0 and record["evidence_docs"] == 1
    assert record["event"] == "llm_attempt"


def test_response_diagnostics_are_whitelisted(caplog):
    caplog.set_level(logging.INFO, logger=ATTEMPT_LOGGER)
    body = ok_body(finish_reason="stop CANARY-OUT")  # not a plain token: must be dropped
    body["usage"]["prompt_tokens"] = "CANARY-OUT"  # not an int: must become None
    call_with_retry(
        make_client(lambda r: httpx.Response(200, json=body)), "CANARY-MSG", NO_DELAY,
        sleep_fn=lambda s: None,
    )
    (record,) = attempt_records(caplog)
    assert "finish_reason" not in record
    assert record["prompt_tokens"] is None
    assert "CANARY-OUT" not in caplog.text


def test_no_prompt_content_secrets_or_error_bodies_leak(caplog):
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.INFO, logger=ATTEMPT_LOGGER)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            leaked = request.headers.get("authorization", "")
            return httpx.Response(500, text=f"CANARY-ERR upstream echoed {leaked}")
        return httpx.Response(200, json=ok_body("CANARY-OUT is not json"))

    client = make_client(handler)
    with pytest.raises(RetryExhausted) as info:
        call_with_retry(
            client, "CANARY-MSG inquiry text", NO_DELAY, sleep_fn=lambda s: None,
            evidence=EVIDENCE, request_id="req-4",
        )
    exc = info.value
    haystacks = [
        caplog.text,
        json.dumps(exc.attempt_summaries),
        str(exc),
        repr(client.last_diagnostics),
    ]
    for haystack in haystacks:
        for canary in CANARIES:
            assert canary not in haystack, f"{canary} leaked"


def test_every_log_line_is_single_line_json(caplog):
    caplog.set_level(logging.INFO, logger=ATTEMPT_LOGGER)
    with pytest.raises(RetryExhausted):
        call_with_retry(
            make_client(raising(httpx.ConnectError)), "CANARY-MSG", NO_DELAY,
            sleep_fn=lambda s: None, request_id="req-5",
        )
    for record in caplog.records:
        if record.name == ATTEMPT_LOGGER:
            message = record.getMessage()
            assert "\n" not in message
            json.loads(message)


def test_502_keeps_status_and_code_and_adds_diagnostics(caplog):
    # Requires fastapi's TestClient; adjust InquiryRequest fields if the schema requires more.
    from fastapi.testclient import TestClient

    from app.main import create_app

    caplog.set_level(logging.INFO, logger=ATTEMPT_LOGGER)

    class FakeAudit:
        def __init__(self):
            self.failures = []

        def record_failure(self, inquiry_id, code, attempts):
            self.failures.append((inquiry_id, code, attempts))

    class FakeRetriever:
        def search(self, message):
            return [SimpleNamespace(document=EVIDENCE[0])]

    audit = FakeAudit()
    app = create_app(
        client_factory=lambda: make_client(raising(httpx.ReadTimeout)),
        audit_store=audit,
        retriever=FakeRetriever(),
        retry_policy=RetryPolicy(max_attempts=2, base_delay_seconds=0.0, max_delay_seconds=0.0),
    )
    response = TestClient(app).post(
        "/triage", json={"inquiry_id": "case-020", "message": "CANARY-MSG synthetic inquiry text"}
    )
    body = response.json()
    assert response.status_code == 502
    assert body["code"] == "llm_provider_error"
    assert body["attempts"] == 2
    assert body["failure_kind"] == "timeout_read"
    assert [s["outcome"] for s in body["attempt_summaries"]] == ["timeout_read", "timeout_read"]
    assert response.headers["x-request-id"] == body["request_id"]
    assert audit.failures == [("case-020", "llm_provider_error", 2)]
    assert "case-020" not in caplog.text
    for canary in CANARIES:
        assert canary not in response.text
