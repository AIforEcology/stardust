"""OpenTelemetry output for the SDK (spec §11).

With ``Stardust(otel=True)``, every metered call also becomes an OpenTelemetry GenAI
client span (``chat claude-sonnet-5``, ``gen_ai.*`` attributes per the GenAI semantic
conventions), timed across the real call and emitted through your app's tracer
provider. Your collector routes it anywhere, including Stardust Core's OTLP receiver.

When an event is also sent directly, its ``event_id`` is derived from the span's ids
exactly as Core derives it for incoming spans, so an event that reaches Core by both
paths is counted once.

Requires the optional ``opentelemetry-api`` package: ``pip install "stardust-sdk[otel]"``.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple

from .extract import Usage

if TYPE_CHECKING:
    from .client import Stardust

# Must match stardust_core.otel.EVENT_ID_NAMESPACE.
EVENT_ID_NAMESPACE = uuid.UUID("5a2b7f0e-3c1d-4e8a-9b6f-2d4c8e1a7f30")

# Stardust provider slug → gen_ai.provider.name (GenAI semantic conventions).
SEMCONV_PROVIDER = {"anthropic": "anthropic", "openai": "openai", "google": "gcp.gemini"}

# gen_ai.operation.name for manual record() calls, where the SDK didn't see the method.
DEFAULT_OPERATION = {"google": "generate_content"}


def event_id_for_span(trace_id: str, span_id: str) -> uuid.UUID:
    return uuid.uuid5(EVENT_ID_NAMESPACE, f"{trace_id}:{span_id}")


class OtelEmitter:
    def __init__(self, tracer_provider: Any = None):
        try:
            from opentelemetry import trace
        except ImportError as e:  # pragma: no cover - exercised only without the extra
            raise ImportError('Stardust(otel=...) needs OpenTelemetry: pip install "stardust-sdk[otel]"') from e
        from . import __version__

        provider = tracer_provider or trace.get_tracer_provider()
        self.tracer = provider.get_tracer("stardust_sdk", __version__)

    def start(self, provider: str, operation: str, model: Optional[str], attrs: Dict[str, Any]) -> Any:
        from opentelemetry.trace import SpanKind

        attributes = {
            "gen_ai.operation.name": operation,
            "gen_ai.provider.name": SEMCONV_PROVIDER.get(provider, provider),
            "gen_ai.request.model": model,
            **attrs,
        }
        name = f"{operation} {model}" if model else operation
        return self.tracer.start_span(
            name, kind=SpanKind.CLIENT, attributes={k: v for k, v in attributes.items() if v is not None}
        )

    @staticmethod
    def ids(span: Any) -> Optional[Tuple[str, str]]:
        ctx = span.get_span_context()
        if not ctx.is_valid:
            return None
        return format(ctx.trace_id, "032x"), format(ctx.span_id, "016x")

    @staticmethod
    def end(span: Any, usage: Optional[Usage], error: Optional[BaseException] = None) -> None:
        if usage is not None:
            usage_attrs = {
                "gen_ai.response.model": usage.model if usage.model != "unknown" else None,
                # Current semconv: input_tokens includes cached tokens, as Usage.tokens_in does.
                "gen_ai.usage.input_tokens": usage.tokens_in,
                "gen_ai.usage.output_tokens": usage.tokens_out,
                "gen_ai.usage.cache_read.input_tokens": usage.tokens_cached_in,
            }
            span.set_attributes({k: v for k, v in usage_attrs.items() if v is not None})
        if error is not None:
            from opentelemetry.trace import Status, StatusCode

            span.record_exception(error)
            span.set_attribute("error.type", type(error).__qualname__)
            span.set_status(Status(StatusCode.ERROR, str(error)))
        span.end()


class Call:
    """One metered API call: records its usage, and owns its GenAI span when OpenTelemetry is on."""

    def __init__(self, stardust: "Stardust", provider: str, operation: str, model: Optional[str],
                 region: Optional[str]):
        self._sd, self._region = stardust, region
        self._done = False
        self._span = None
        emitter = stardust._otel if stardust.enabled else None
        if emitter is not None:
            try:
                self._span = emitter.start(provider, operation, model, stardust._span_attributes(region))
            except Exception:  # noqa: BLE001 - tracing must never break the call
                stardust._log_exception("Stardust could not start a span")

    def finish(self, usage: Optional[Usage], error: Optional[BaseException] = None) -> None:
        """Idempotent. Ends the span (if any) and queues the usage event (if any)."""
        if self._done:
            return
        self._done = True
        ids = None
        if self._span is not None:
            try:
                ids = OtelEmitter.ids(self._span)
                OtelEmitter.end(self._span, usage, error)
            except Exception:  # noqa: BLE001
                self._sd._log_exception("Stardust could not end a span")
        if usage is not None:
            self._sd._queue_usage(usage, region=self._region, trace_ids=ids)

    def fail(self, error: BaseException, usage: Optional[Usage] = None) -> None:
        self.finish(usage, error)
