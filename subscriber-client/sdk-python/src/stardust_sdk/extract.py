"""Read token usage from provider API responses (spec §12.2–§12.4, Measured-tier tokens).

Works on the vendor SDK objects and on plain dicts (e.g. raw JSON from the HTTP APIs),
without importing any vendor SDK. Returns ``None`` when a response carries no usage.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class Usage:
    provider: str
    model: str
    tokens_in: int
    tokens_out: int
    tokens_cached_in: Optional[int] = None


def _get(obj: Any, *names: str) -> Any:
    """First present attribute or key among ``names`` (handles SDK objects and dicts)."""
    if obj is None:
        return None
    for name in names:
        value = obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)
        if value is not None:
            return value
    return None


def _int(v: Any) -> int:
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0


def from_anthropic(response: Any, model: Optional[str] = None) -> Optional[Usage]:
    """Anthropic Messages API. ``input_tokens`` excludes cache reads/writes, so they are added back."""
    usage = _get(response, "usage")
    if usage is None:
        return None
    cache_read = _int(_get(usage, "cache_read_input_tokens"))
    cache_write = _int(_get(usage, "cache_creation_input_tokens"))
    return Usage(
        provider="anthropic",
        model=_get(response, "model") or model or "unknown",
        tokens_in=_int(_get(usage, "input_tokens")) + cache_read + cache_write,
        tokens_out=_int(_get(usage, "output_tokens")),
        tokens_cached_in=cache_read or None,
    )


def from_openai(response: Any, model: Optional[str] = None) -> Optional[Usage]:
    """OpenAI Chat Completions (``prompt_tokens``) or Responses API (``input_tokens``).

    Cached and reasoning tokens are already included in the input/output totals.
    """
    usage = _get(response, "usage")
    if usage is None:
        return None
    if _get(usage, "prompt_tokens") is not None:
        tokens_in, tokens_out = _get(usage, "prompt_tokens"), _get(usage, "completion_tokens")
        details = _get(usage, "prompt_tokens_details")
    else:
        tokens_in, tokens_out = _get(usage, "input_tokens"), _get(usage, "output_tokens")
        details = _get(usage, "input_tokens_details")
    cached = _int(_get(details, "cached_tokens"))
    return Usage(
        provider="openai",
        model=_get(response, "model") or model or "unknown",
        tokens_in=_int(tokens_in),
        tokens_out=_int(tokens_out),
        tokens_cached_in=cached or None,
    )


def from_gemini(response: Any, model: Optional[str] = None) -> Optional[Usage]:
    """Gemini API / Vertex AI ``usage_metadata`` (SDK) or ``usageMetadata`` (REST JSON).

    Thinking tokens are billed as output, so they are added to the candidates count.
    """
    meta = _get(response, "usage_metadata", "usageMetadata")
    if meta is None:
        return None
    prompt = _int(_get(meta, "prompt_token_count", "promptTokenCount"))
    tool_prompt = _int(_get(meta, "tool_use_prompt_token_count", "toolUsePromptTokenCount"))
    candidates = _int(_get(meta, "candidates_token_count", "candidatesTokenCount"))
    thoughts = _int(_get(meta, "thoughts_token_count", "thoughtsTokenCount"))
    cached = _int(_get(meta, "cached_content_token_count", "cachedContentTokenCount"))
    return Usage(
        provider="google",
        model=_get(response, "model_version", "modelVersion") or model or "unknown",
        tokens_in=prompt + tool_prompt,
        tokens_out=candidates + thoughts,
        tokens_cached_in=cached or None,
    )


def detect_provider(response: Any) -> Optional[str]:
    module = type(response).__module__ or ""
    if module.startswith("anthropic"):
        return "anthropic"
    if module.startswith("openai"):
        return "openai"
    if module.startswith("google"):
        return "google"
    if isinstance(response, dict):
        if "usageMetadata" in response or "usage_metadata" in response:
            return "google"
        usage = response.get("usage") or {}
        if "prompt_tokens" in usage or response.get("object") in ("chat.completion", "response"):
            return "openai"
        if response.get("type") == "message" or "cache_read_input_tokens" in usage:
            return "anthropic"
    return None


EXTRACTORS = {"anthropic": from_anthropic, "openai": from_openai, "google": from_gemini}


def extract(response: Any, provider: Optional[str] = None, model: Optional[str] = None) -> Optional[Usage]:
    provider = provider or detect_provider(response)
    fn = EXTRACTORS.get(provider or "")
    return fn(response, model) if fn else None
