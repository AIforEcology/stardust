"""Auto-instrument Anthropic, OpenAI and Google Gen AI clients.

``instrument(client, stardust)`` wraps the client's generation methods in place so
every call is metered, including streaming calls. Wrappers return the vendor's own
response objects unchanged, and a metering failure never breaks the API call.

Covered:

- Anthropic / AsyncAnthropic: ``messages.create`` (with or without ``stream=True``)
- OpenAI / AsyncOpenAI: ``chat.completions.create`` and ``responses.create``.
  Streaming chat completions only report usage if you pass
  ``stream_options={"include_usage": True}``; the SDK doesn't add it for you.
- google-genai ``Client``: ``models.generate_content`` / ``generate_content_stream``
  and the ``client.aio`` equivalents
"""

from __future__ import annotations

import functools
import inspect
import logging
from typing import Any, Callable, Dict, Optional

from .client import Stardust
from .extract import Usage, _get, _int, from_anthropic, from_gemini, from_openai

log = logging.getLogger("stardust_sdk")

_INSTRUMENTED = "__stardust_instrumented__"


# --- stream accumulators ------------------------------------------------------------


class _Accumulator:
    """Collects usage from stream events; ``usage()`` is called once the stream ends."""

    def __init__(self, provider: str, model: Optional[str]):
        self.provider, self.model = provider, model
        self.final: Optional[Usage] = None

    def feed(self, event: Any) -> None:
        raise NotImplementedError

    def usage(self) -> Optional[Usage]:
        return self.final


class _AnthropicAcc(_Accumulator):
    # message_start carries input usage; message_delta carries the running output count.
    def __init__(self, provider: str, model: Optional[str]):
        super().__init__(provider, model)
        self.usage_fields: Dict[str, int] = {}

    def feed(self, event: Any) -> None:
        kind = _get(event, "type")
        if kind == "message_start":
            message = _get(event, "message")
            self.model = _get(message, "model") or self.model
            self._merge(_get(message, "usage"))
        elif kind == "message_delta":
            self._merge(_get(event, "usage"))

    def _merge(self, usage: Any) -> None:
        for name in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
            value = _get(usage, name)
            if value is not None:
                self.usage_fields[name] = _int(value)

    def usage(self) -> Optional[Usage]:
        if not self.usage_fields:
            return None
        return from_anthropic({"model": self.model, "usage": self.usage_fields}, self.model)


class _OpenAIAcc(_Accumulator):
    # Chat: the last chunk carries usage (with include_usage). Responses: response.completed.
    def feed(self, event: Any) -> None:
        if _get(event, "type") in ("response.completed", "response.incomplete"):
            event = _get(event, "response")
        if _get(event, "usage") is not None:
            self.final = from_openai(event, self.model)


class _GeminiAcc(_Accumulator):
    # Each chunk repeats usage_metadata; the last one holds the totals.
    def feed(self, event: Any) -> None:
        if _get(event, "usage_metadata", "usageMetadata") is not None:
            self.final = from_gemini(event, self.model)


# --- stream proxies -----------------------------------------------------------------


class _StreamProxy:
    """Pass-through wrapper around a vendor stream that records usage when it ends."""

    def __init__(self, stream: Any, acc: _Accumulator, done: Callable[[Optional[Usage]], None]):
        self._stream, self._acc, self._done = stream, acc, done
        self._finished = False

    def _finish(self) -> None:
        if not self._finished:
            self._finished = True
            self._done(self._acc.usage())

    def _feed(self, event: Any) -> None:
        try:
            self._acc.feed(event)
        except Exception:  # noqa: BLE001
            log.exception("Stardust could not read a stream event")

    def __iter__(self):
        try:
            for event in self._stream:
                self._feed(event)
                yield event
        finally:
            self._finish()

    def __enter__(self):
        if hasattr(self._stream, "__enter__"):
            self._stream.__enter__()
        return self

    def __exit__(self, *exc):
        try:
            return self._stream.__exit__(*exc) if hasattr(self._stream, "__exit__") else None
        finally:
            self._finish()

    async def _aiter(self):
        try:
            async for event in self._stream:
                self._feed(event)
                yield event
        finally:
            self._finish()

    def __aiter__(self):
        return self._aiter()

    async def __aenter__(self):
        if hasattr(self._stream, "__aenter__"):
            await self._stream.__aenter__()
        return self

    async def __aexit__(self, *exc):
        try:
            return await self._stream.__aexit__(*exc) if hasattr(self._stream, "__aexit__") else None
        finally:
            self._finish()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)


# --- wrapping -------------------------------------------------------------------------


def _wrap(owner: Any, attr: str, stardust: Stardust, provider: str, acc_cls: type,
          extractor: Callable[[Any, Optional[str]], Optional[Usage]], region: Optional[str],
          always_stream: bool = False) -> bool:
    original = getattr(owner, attr, None)
    if original is None or getattr(original, _INSTRUMENTED, False):
        return False

    def record(usage: Optional[Usage]) -> None:
        if usage is not None:
            stardust.record_usage(usage, region=region)

    def handle(result: Any, kwargs: Dict[str, Any]) -> Any:
        model = kwargs.get("model")
        try:
            if always_stream or kwargs.get("stream") is True:
                return _StreamProxy(result, acc_cls(provider, model), record)
            record(extractor(result, model))
        except Exception:  # noqa: BLE001 - metering must never break the caller
            log.exception("Stardust metering failed")
        return result

    # Check the result, not the function: async vendor methods are often plain functions
    # (behind decorators) that return an awaitable.
    @functools.wraps(original)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        if inspect.isawaitable(result):
            async def finish() -> Any:
                return handle(await result, kwargs)
            return finish()
        return handle(result, kwargs)

    setattr(wrapper, _INSTRUMENTED, True)
    setattr(owner, attr, wrapper)
    return True


def _path(obj: Any, *names: str) -> Any:
    for name in names:
        obj = getattr(obj, name, None)
        if obj is None:
            return None
    return obj


def instrument(client: Any, stardust: Stardust, *, region: Optional[str] = None) -> Any:
    """Meter every generation call made through ``client``. Returns the same client.

    ``region`` is the processing region (e.g. ``us-east-1`` for Bedrock); it overrides the
    Stardust client's default for this client only.
    """
    module = type(client).__module__ or ""
    wrapped = 0

    if module.startswith("anthropic"):
        wrapped += _wrap(_path(client, "messages"), "create", stardust, "anthropic", _AnthropicAcc, from_anthropic, region)
    elif module.startswith("openai"):
        wrapped += _wrap(_path(client, "chat", "completions"), "create", stardust, "openai", _OpenAIAcc, from_openai, region)
        wrapped += _wrap(_path(client, "responses"), "create", stardust, "openai", _OpenAIAcc, from_openai, region)
    elif module.startswith("google"):
        for models in (_path(client, "models"), _path(client, "aio", "models")):
            if models is None:
                continue
            wrapped += _wrap(models, "generate_content", stardust, "google", _GeminiAcc, from_gemini, region)
            wrapped += _wrap(models, "generate_content_stream", stardust, "google", _GeminiAcc, from_gemini, region,
                             always_stream=True)
    else:
        raise TypeError(f"Stardust can't instrument {type(client).__name__} from {module!r}")

    if not wrapped:
        log.info("Stardust: nothing new to instrument on %s", type(client).__name__)
    return client
