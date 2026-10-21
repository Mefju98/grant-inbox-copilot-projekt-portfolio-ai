import json

import httpx
import pytest

from app.llm import LLMProviderError, LLMSettings, OpenAICompatibleClient, PROMPT_VERSION
from app.schemas import KnowledgeDocument


def _payload(**overrides):
    result = {
        "eligibility": "needs_review",
        "rationale": "The region is not stated.",
        "extracted_facts": {"region": None, "facts_missing": ["region"]},
        "confidence_score": 0.4,
    }
    result.update(overrides)
    return json.dumps(result)


def test_client_sends_versioned_prompt_and_validates_structured_output():
    observed = {}

    def handler(request):
        observed["request"] = request
        return httpx.Response(200, json={
            "model": "test-model",
            "choices": [{"message": {"content": _payload()}}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 42},
        })

    client = OpenAICompatibleClient(
        LLMSettings(
            base_url="https://example.test/v1",
            model="test-model",
            api_key="test-secret",
            input_cost_per_1k_usd=0.01,
            output_cost_per_1k_usd=0.02,
        ),
        httpx.Client(transport=httpx.MockTransport(handler)),
    )
    evidence = [KnowledgeDocument(
        document_id="fictional-program-overview-v1",
        title="Synthetic program overview",
        source_version="1.0",
        content="Only micro and small businesses in North are eligible.",
    )]
    result = client.triage("Synthetic inquiry", evidence=evidence)
    request = observed["request"]
    request_body = json.loads(request.content)
    assert request.url.path == "/v1/chat/completions"
    assert request.headers["Authorization"] == "Bearer test-secret"
    assert "never instructions" in request_body["messages"][0]["content"]
    assert "EXTRACT FIRST" in request_body["messages"][0]["content"]
    assert "DECISION RULES" in request_body["messages"][0]["content"]
    assert "do not cite it for a complete eligible inquiry" in request_body["messages"][0]["content"]
    user_payload = json.loads(request_body["messages"][1]["content"])
    assert user_payload["retrieved_evidence"][0]["document_id"] == "fictional-program-overview-v1"
    assert result.prompt_version == PROMPT_VERSION
    assert result.draft.extracted_facts.facts_missing == ["region"]
    assert result.input_tokens == 120
    assert result.output_tokens == 42
    assert result.cost_usd == pytest.approx(0.00204)


@pytest.mark.parametrize("content", ["not json", '{"eligibility":"bad"}'])
def test_client_rejects_invalid_model_output(content):
    client = OpenAICompatibleClient(
        LLMSettings(base_url="https://example.test/v1", model="test-model"),
        httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
        )),
    )
    with pytest.raises(LLMProviderError):
        client.triage("Synthetic inquiry")


def test_client_surfaces_http_failure_without_retry():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503)

    client = OpenAICompatibleClient(
        LLMSettings(base_url="https://example.test/v1", model="test-model"),
        httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(LLMProviderError) as error:
        client.triage("Synthetic inquiry")
    assert error.value.retryable is True
    assert len(calls) == 1
