"""Single-call client for OpenAI-compatible chat-completions APIs."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from importlib.resources import files
from time import perf_counter
from typing import Any

import httpx
from pydantic import ValidationError

from app.schemas import KnowledgeDocument, ModelTriageDraft


PROMPT_VERSION = "triage-v5"


class LLMProviderError(RuntimeError):
    """Raised for transport, provider, or response-contract failures.

    ``kind`` is a content-free failure class used for diagnostics only. It never
    changes retry behaviour; ``retryable`` alone decides that.
    """

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        kind: str = "unknown",
        http_status: int | None = None,
    ):
        super().__init__(message)
        self.retryable = retryable
        self.kind = kind
        self.http_status = http_status


@dataclass(frozen=True)
class LLMSettings:
    base_url: str
    model: str
    api_key: str | None = None
    timeout_seconds: float = 30.0
    input_cost_per_1k_usd: float | None = None
    output_cost_per_1k_usd: float | None = None

    @classmethod
    def from_env(cls) -> "LLMSettings":
        base_url = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        model = os.getenv("LLM_MODEL", "")
        api_key = os.getenv("LLM_API_KEY")
        if not model:
            raise ValueError("LLM_MODEL must be set")
        return cls(
            base_url=base_url,
            model=model,
            api_key=api_key,
            timeout_seconds=float(os.getenv("LLM_TIMEOUT_SECONDS", "30")),
            input_cost_per_1k_usd=_optional_rate(os.getenv("LLM_INPUT_COST_PER_1K_USD")),
            output_cost_per_1k_usd=_optional_rate(os.getenv("LLM_OUTPUT_COST_PER_1K_USD")),
        )


@dataclass(frozen=True)
class LLMCallResult:
    draft: ModelTriageDraft
    model: str
    prompt_version: str
    latency_ms: int
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    attempts: int = 1


def load_system_prompt(version: str = PROMPT_VERSION) -> str:
    prompt_folder = version.removeprefix("triage-")
    return files("app").joinpath("prompts", prompt_folder, "triage_system.txt").read_text(encoding="utf-8")


class OpenAICompatibleClient:
    """Client for OpenAI Chat Completions and compatible endpoints, including Ollama."""

    def __init__(self, settings: LLMSettings, http_client: httpx.Client | None = None):
        self.settings = settings
        self._http_client = http_client or httpx.Client(timeout=settings.timeout_seconds)
        self._owns_client = http_client is None
        # Content-free metadata about the most recent triage() call, success or failure.
        # The client is created per request, so this is not shared between requests.
        self.last_diagnostics: dict[str, Any] = {}

    def close(self) -> None:
        if self._owns_client:
            self._http_client.close()

    def triage(
        self,
        message: str,
        evidence: list[KnowledgeDocument] | None = None,
    ) -> LLMCallResult:
        headers = {"Content-Type": "application/json"}
        if self.settings.api_key:
            headers["Authorization"] = f"Bearer {self.settings.api_key}"
        system_prompt = load_system_prompt()
        user_content = json.dumps(
            {
                "inquiry": message,
                "retrieved_evidence": [
                    {
                        "document_id": document.document_id,
                        "title": document.title,
                        "source_version": document.source_version,
                        "content": document.content,
                    }
                    for document in (evidence or [])
                ],
            },
            ensure_ascii=False,
        )
        payload = {
            "model": self.settings.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
        }
        started = perf_counter()
        diag: dict[str, Any] = {
            "model": self.settings.model,
            "prompt_version": PROMPT_VERSION,
            "prompt_chars": len(system_prompt) + len(user_content),
            "evidence_docs": len(evidence or []),
            "timeouts": _effective_timeouts(self._http_client),
        }
        self.last_diagnostics = diag
        try:
            response = self._http_client.post(
                f"{self.settings.base_url}/chat/completions",
                headers=headers,
                json=payload,
            )
            diag["http_status"] = response.status_code
            diag["response_bytes"] = len(response.content)
            response.raise_for_status()
            body = response.json()
            diag.update(_response_diagnostics(body))
            content = body["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise LLMProviderError("Provider returned empty or non-text content", kind="empty_content")
            diag["content_chars"] = len(content)
            draft = ModelTriageDraft.model_validate(json.loads(content))
        except LLMProviderError:
            _close_diagnostics(diag, started)
            raise
        except httpx.TimeoutException as exc:
            _close_diagnostics(diag, started, exc)
            raise LLMProviderError(
                "LLM request timed out", retryable=True, kind=_timeout_kind(exc)
            ) from exc
        except httpx.HTTPStatusError as exc:
            _close_diagnostics(diag, started, exc)
            status = exc.response.status_code
            retryable = status in {408, 429, 500, 502, 503, 504}
            raise LLMProviderError(
                f"LLM provider returned HTTP {status}",
                retryable=retryable,
                kind="http_5xx" if status >= 500 else "http_4xx",
                http_status=status,
            ) from exc
        except httpx.TransportError as exc:
            _close_diagnostics(diag, started, exc)
            raise LLMProviderError("LLM transport failed", retryable=True, kind="transport_error") from exc
        except (httpx.HTTPError, KeyError, IndexError, TypeError, json.JSONDecodeError, ValidationError) as exc:
            _close_diagnostics(diag, started, exc)
            raise LLMProviderError(
                f"LLM request or response validation failed: {type(exc).__name__}",
                kind=_validation_kind(exc, diag),
            ) from exc

        _close_diagnostics(diag, started)
        usage: Any = body.get("usage") or {}
        input_tokens = _optional_int(usage.get("prompt_tokens"))
        output_tokens = _optional_int(usage.get("completion_tokens"))
        cost = None
        if (
            input_tokens is not None
            and output_tokens is not None
            and self.settings.input_cost_per_1k_usd is not None
            and self.settings.output_cost_per_1k_usd is not None
        ):
            cost = (
                input_tokens * self.settings.input_cost_per_1k_usd
                + output_tokens * self.settings.output_cost_per_1k_usd
            ) / 1000
        return LLMCallResult(
            draft=draft,
            model=str(body.get("model", self.settings.model)),
            prompt_version=PROMPT_VERSION,
            latency_ms=round((perf_counter() - started) * 1000),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
        )


def _timeout_kind(exc: httpx.TimeoutException) -> str:
    if isinstance(exc, httpx.ConnectTimeout):
        return "timeout_connect"
    if isinstance(exc, httpx.ReadTimeout):
        return "timeout_read"
    if isinstance(exc, httpx.WriteTimeout):
        return "timeout_write"
    if isinstance(exc, httpx.PoolTimeout):
        return "timeout_pool"
    return "timeout"


def _validation_kind(exc: BaseException, diag: dict[str, Any]) -> str:
    if isinstance(exc, json.JSONDecodeError):
        # finish_reason == "length" means generation stopped at the token/context limit,
        # so the JSON is cut off rather than malformed by the model.
        return "output_truncated" if diag.get("finish_reason") == "length" else "invalid_json"
    if isinstance(exc, ValidationError):
        return "schema_invalid"
    return "bad_response"


def _effective_timeouts(client: Any) -> dict[str, float | None] | None:
    timeout = getattr(client, "timeout", None)
    if timeout is None:
        return None
    return {name: getattr(timeout, name, None) for name in ("connect", "read", "write", "pool")}


def _response_diagnostics(body: Any) -> dict[str, Any]:
    """Whitelisted, content-free fields from an OpenAI-compatible response body."""

    out: dict[str, Any] = {}
    if not isinstance(body, dict):
        return out
    usage = body.get("usage")
    if isinstance(usage, dict):
        out["prompt_tokens"] = _optional_int(usage.get("prompt_tokens"))
        out["completion_tokens"] = _optional_int(usage.get("completion_tokens"))
    choices = body.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        reason = choices[0].get("finish_reason")
        if isinstance(reason, str) and len(reason) <= 32 and reason.isidentifier():
            out["finish_reason"] = reason
    return out


def _close_diagnostics(diag: dict[str, Any], started: float, exc: BaseException | None = None) -> None:
    diag["duration_ms"] = round((perf_counter() - started) * 1000)
    if exc is not None:
        diag["exc_type"] = type(exc).__name__


def _optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) and value >= 0 else None


def _optional_rate(value: str | None) -> float | None:
    if value is None or value.strip() == "":
        return None
    rate = float(value)
    if rate < 0:
        raise ValueError("LLM token rates must not be negative")
    return rate
