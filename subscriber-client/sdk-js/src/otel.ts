// OpenTelemetry output (spec §11). Pass `tracer` to `new Stardust({ tracer })` and every
// metered call also becomes a GenAI client span ("chat claude-sonnet-5", gen_ai.*
// attributes), timed across the real call. Only types are imported from
// @opentelemetry/api, so the SDK has no runtime dependency on it.
//
// When an event is also sent directly, its event_id is derived from the span's ids exactly
// as Stardust Core derives it for incoming spans, so it is counted once.

import { createHash } from "node:crypto";

import type { Span, Tracer } from "@opentelemetry/api";

import type { Provider, Usage } from "./extract.ts";

// Must match stardust_core.otel.EVENT_ID_NAMESPACE.
export const EVENT_ID_NAMESPACE = "5a2b7f0e-3c1d-4e8a-9b6f-2d4c8e1a7f30";

// Values from @opentelemetry/api, inlined to avoid a runtime import.
const SPAN_KIND_CLIENT = 2;
const STATUS_ERROR = 2;
const INVALID_TRACE_ID = "0".repeat(32);

export const SEMCONV_PROVIDER: Record<string, string> = { anthropic: "anthropic", openai: "openai", google: "gcp.gemini" };
export const DEFAULT_OPERATION: Record<string, string> = { google: "generate_content" };

export type TraceIds = [traceId: string, spanId: string];

/** RFC 4122 version-5 UUID (SHA-1 of namespace + name). */
export function uuidv5(name: string, namespace: string): string {
  const hash = createHash("sha1").update(Buffer.from(namespace.replace(/-/g, ""), "hex")).update(name, "utf8").digest();
  const b = hash.subarray(0, 16);
  b[6] = (b[6] & 0x0f) | 0x50;
  b[8] = (b[8] & 0x3f) | 0x80;
  const h = b.toString("hex");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

export function eventIdForSpan(traceId: string, spanId: string): string {
  return uuidv5(`${traceId}:${spanId}`, EVENT_ID_NAMESPACE);
}

function clean(attrs: Record<string, unknown>): Record<string, string | number | boolean> {
  const out: Record<string, string | number | boolean> = {};
  for (const [k, v] of Object.entries(attrs)) {
    if (typeof v === "string" || typeof v === "number" || typeof v === "boolean") out[k] = v;
  }
  return out;
}

/** Internal hooks a Call needs from the Stardust client. */
export interface CallHost {
  readonly tracer?: Tracer;
  readonly enabled: boolean;
  spanAttributes(region?: string): Record<string, unknown>;
  queueUsage(usage: Usage, region: string | undefined, ids: TraceIds | undefined): void;
}

/** One metered API call: records its usage, and owns its GenAI span when a tracer is set. */
export class Call {
  private done = false;
  private readonly span?: Span;
  private readonly host: CallHost;
  private readonly region: string | undefined;

  constructor(host: CallHost, provider: Provider | string, operation: string, model: string | undefined, region: string | undefined) {
    this.host = host;
    this.region = region;
    if (host.tracer && host.enabled) {
      try {
        this.span = host.tracer.startSpan(model ? `${operation} ${model}` : operation, {
          kind: SPAN_KIND_CLIENT,
          attributes: clean({
            "gen_ai.operation.name": operation,
            "gen_ai.provider.name": SEMCONV_PROVIDER[provider] ?? provider,
            "gen_ai.request.model": model,
            ...host.spanAttributes(region),
          }),
        });
      } catch (e) {
        console.warn("[stardust] could not start a span", e);
      }
    }
  }

  /** Idempotent. Ends the span (if any) and queues the usage event (if any). */
  finish(usage: Usage | undefined, error?: unknown): void {
    if (this.done) return;
    this.done = true;
    let ids: TraceIds | undefined;
    if (this.span) {
      try {
        const ctx = this.span.spanContext();
        if (ctx.traceId !== INVALID_TRACE_ID) ids = [ctx.traceId, ctx.spanId];
        if (usage) {
          this.span.setAttributes(clean({
            "gen_ai.response.model": usage.model !== "unknown" ? usage.model : undefined,
            // Current semconv: input_tokens includes cached tokens, as Usage.tokensIn does.
            "gen_ai.usage.input_tokens": usage.tokensIn,
            "gen_ai.usage.output_tokens": usage.tokensOut,
            "gen_ai.usage.cache_read.input_tokens": usage.tokensCachedIn,
          }));
        }
        if (error !== undefined) {
          const err = error instanceof Error ? error : new Error(String(error));
          this.span.recordException(err);
          this.span.setAttribute("error.type", err.constructor?.name || "Error");
          this.span.setStatus({ code: STATUS_ERROR, message: err.message });
        }
        this.span.end();
      } catch (e) {
        console.warn("[stardust] could not end a span", e);
      }
    }
    if (usage) this.host.queueUsage(usage, this.region, ids);
  }

  fail(error: unknown, usage?: Usage): void {
    this.finish(usage, error);
  }
}
