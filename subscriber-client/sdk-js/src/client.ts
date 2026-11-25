// Queues usage events and sends them to Core in the background.
// Recording never blocks or throws in the caller's code path (spec §15), and events are
// buffered and retried with backoff while Core is unreachable (§15 offline resilience).

import type { Tracer } from "@opentelemetry/api";

import { extract, type Provider, type Usage } from "./extract.ts";
import { Call, type CallHost, DEFAULT_OPERATION, eventIdForSpan, type TraceIds } from "./otel.ts";
import {
  type DailySummary,
  type Order,
  type Quote,
  StargrainError,
  type Summary,
} from "./results.ts";

export const DEFAULT_API_BASE = "https://core.aifore.org";
export const AUTH_WARNING = "Stargrain: API key missing or invalid; events are being dropped";

export interface StargrainOptions {
  /**
   * Core URL. Defaults to `STARGRAIN_API_BASE` (or the older `STARDUST_API_BASE`), else https://core.aifore.org.
   * `null` sends nothing directly (with `tracer`: spans only, via your collector).
   */
  apiBase?: string | null;
  /** API key, sent as `Authorization: Bearer <key>`. Defaults to `STARGRAIN_API_KEY` (or the older `STARDUST_API_KEY`). */
  apiKey?: string;
  /** An OpenTelemetry tracer: each metered call also becomes a GenAI client span. */
  tracer?: Tracer;
  userId?: string;
  orgId?: string;
  /** Default processing region, e.g. "us-east-1" (OTel cloud.region). */
  region?: string;
  enabled?: boolean;
  maxBuffer?: number;
  timeoutMs?: number;
  /** Receives Core's enriched event (indicator code, cost, CO2e…) for each sent event. */
  onResult?: (enriched: Record<string, unknown>) => void;
  fetch?: typeof fetch;
}

type UsageEvent = Record<string, unknown>;
type TimeArg = Date | string | undefined;

function env(name: string): string | undefined {
  // `process` is missing in browsers and some edge runtimes.
  const p = (globalThis as { process?: { env?: Record<string, string | undefined> } }).process;
  return p?.env?.[name] || undefined;
}

function iso(t: TimeArg): string | undefined {
  return t instanceof Date ? t.toISOString() : t;
}

const MAX_BACKOFF_MS = 60_000;
const FLUSH_RETRY_MS = 50;

function isoWithOffset(d: Date): string {
  const pad = (n: number, w = 2) => String(Math.trunc(Math.abs(n))).padStart(w, "0");
  const off = -d.getTimezoneOffset();
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:` +
    `${pad(d.getSeconds())}.${pad(d.getMilliseconds(), 3)}${off >= 0 ? "+" : "-"}${pad(off / 60)}:${pad(off % 60)}`
  );
}

export class Stargrain implements CallHost {
  readonly url: string | null;
  readonly apiBase: string | null;
  readonly tracer?: Tracer;
  dropped = 0;
  // A true private field: never in JSON.stringify, util.inspect or console.log output.
  readonly #apiKey: string | undefined;
  private authWarned = false;
  private readonly opts: Omit<StargrainOptions, "apiKey">;
  private readonly fetchImpl: typeof fetch;
  private readonly buffer: UsageEvent[] = [];
  private sending = false;
  private backoffMs = 0;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private flushWaiters: Array<() => void> = [];
  private flushing = 0;
  private closed = false;

  constructor(opts: StargrainOptions = {}) {
    const { apiKey, ...rest } = opts;
    this.opts = { enabled: true, maxBuffer: 10_000, timeoutMs: 5_000, ...rest };
    if (opts.apiBase === null && !opts.tracer) throw new Error("Stargrain needs apiBase, tracer, or both");
    this.apiBase = opts.apiBase === null ? null : (opts.apiBase ?? env("STARGRAIN_API_BASE") ?? env("STARDUST_API_BASE") ?? DEFAULT_API_BASE).replace(/\/+$/, "");
    this.url = this.apiBase === null ? null : `${this.apiBase}/v1/events`;
    this.#apiKey = apiKey || (env("STARGRAIN_API_KEY") ?? env("STARDUST_API_KEY"));
    this.tracer = opts.tracer;
    this.fetchImpl = opts.fetch ?? globalThis.fetch.bind(globalThis);
  }

  get hasApiKey(): boolean {
    return this.#apiKey !== undefined;
  }

  toJSON(): Record<string, unknown> {
    return { apiBase: this.apiBase, apiKey: this.#apiKey ? "set" : "unset", region: this.opts.region };
  }

  private headers(json: boolean): Record<string, string> {
    const h: Record<string, string> = { accept: "application/json" };
    if (json) h["content-type"] = "application/json";
    if (this.#apiKey) h.authorization = `Bearer ${this.#apiKey}`;
    return h;
  }

  get pending(): number {
    return this.buffer.length + (this.sending ? 1 : 0);
  }

  get enabled(): boolean {
    return Boolean(this.opts.enabled) && !this.closed;
  }

  /** Record one operation's usage. With a tracer, this also emits a (zero-length) span. */
  record(usage: Usage, options: { region?: string; operation?: string } = {}): void {
    if (!this.enabled) return;
    const operation = options.operation ?? DEFAULT_OPERATION[usage.provider] ?? "chat";
    this.startCall(usage.provider, operation, usage.model, options.region).finish(usage);
  }

  /** Begin metering a call; used by instrument(). `Call.finish(usage)` completes it. */
  startCall(provider: Provider | string, operation: string, model?: string, region?: string): Call {
    return new Call(this, provider, operation, model, region ?? this.opts.region);
  }

  /** @internal */
  spanAttributes(region?: string): Record<string, unknown> {
    return {
      "cloud.region": region,
      "stardust.source_layer": "infra_agent",
      "stardust.user_id": this.opts.userId,
      "stardust.org_id": this.opts.orgId,
    };
  }

  /** @internal Queue a usage event for Core, if direct sending is on. */
  queueUsage(usage: Usage, region: string | undefined, ids: TraceIds | undefined): void {
    if (!this.url || !this.enabled) return;
    const event: UsageEvent = {
      event_id: ids ? eventIdForSpan(...ids) : crypto.randomUUID(),
      source_layer: "infra_agent",
      provider: usage.provider,
      model: usage.model,
      timestamp: isoWithOffset(new Date()),
      tokens_in: usage.tokensIn,
      tokens_out: usage.tokensOut,
      tokens_estimated: false,
    };
    const optional: UsageEvent = {
      tokens_cached_in: usage.tokensCachedIn,
      region: region ?? this.opts.region,
      user_id: this.opts.userId,
      org_id: this.opts.orgId,
      otel_trace_id: ids?.[0],
      otel_span_id: ids?.[1],
    };
    for (const [k, v] of Object.entries(optional)) if (v != null) event[k] = v;

    if (this.buffer.length >= (this.opts.maxBuffer ?? 10_000)) {
      this.buffer.shift();
      this.dropped++;
      console.warn("[stargrain] buffer full; dropping the oldest event");
    }
    this.buffer.push(event);
    this.kick();
  }

  /** Record an Anthropic, OpenAI or Gemini response. Returns the usage found, if any. Never throws. */
  recordResponse(response: unknown, options: { provider?: Provider; model?: string; region?: string } = {}): Usage | undefined {
    try {
      const usage = extract(response, options.provider, options.model);
      if (usage) this.record(usage, { region: options.region });
      return usage;
    } catch (e) {
      console.warn("[stargrain] could not read usage from a response", e);
      return undefined;
    }
  }

  /** Resolves true once every queued event is sent, or false after `timeoutMs`. */
  async flush(timeoutMs?: number): Promise<boolean> {
    if (this.pending === 0) return true;
    this.flushing++;
    this.kick(true);
    try {
      return await new Promise<boolean>((resolve) => {
        const done = () => resolve(true);
        this.flushWaiters.push(done);
        if (timeoutMs != null) {
          setTimeout(() => {
            this.flushWaiters = this.flushWaiters.filter((w) => w !== done);
            resolve(false);
          }, timeoutMs).unref?.();
        }
      });
    } finally {
      this.flushing--;
    }
  }

  async close(timeoutMs = 5_000): Promise<void> {
    await this.flush(timeoutMs);
    this.closed = true;
    if (this.timer) clearTimeout(this.timer);
    if (this.buffer.length) console.warn(`[stargrain] closed with ${this.buffer.length} unsent events`);
  }

  private kick(now = false): void {
    if (this.sending || this.closed || !this.url) return;
    if (this.timer) {
      if (!now) return;
      clearTimeout(this.timer);
      this.timer = undefined;
    }
    void this.drain();
  }

  private async drain(): Promise<void> {
    this.sending = true;
    try {
      while (this.buffer.length && !this.closed) {
        const event = this.buffer[0];
        const retry = await this.send(event);
        if (retry) {
          this.backoffMs = Math.min(Math.max(this.backoffMs * 2, 1_000), MAX_BACKOFF_MS);
          const wait = this.flushing ? FLUSH_RETRY_MS : this.backoffMs;
          // unref: a pending retry must never keep the process alive.
          this.timer = setTimeout(() => {
            this.timer = undefined;
            void this.drain();
          }, wait);
          this.timer.unref?.();
          return;
        }
        this.buffer.shift();
        this.backoffMs = 0;
      }
    } finally {
      this.sending = false;
    }
    if (!this.buffer.length) {
      const waiters = this.flushWaiters;
      this.flushWaiters = [];
      waiters.forEach((w) => w());
    }
  }

  /** Returns true when the event should be retried. */
  private async send(event: UsageEvent): Promise<boolean> {
    let res: Response;
    try {
      res = await this.fetchImpl(this.url!, {
        method: "POST",
        headers: this.headers(true),
        body: JSON.stringify(event),
        signal: AbortSignal.timeout(this.opts.timeoutMs ?? 5_000),
      });
    } catch {
      return true; // unreachable or timed out
    }
    if (res.ok) {
      if (this.opts.onResult) {
        try {
          this.opts.onResult((await res.json()) as Record<string, unknown>);
        } catch (e) {
          console.warn("[stargrain] onResult failed", e);
        }
      }
      return false;
    }
    if (res.status >= 500 || res.status === 429) return true;
    if (res.status === 401 || res.status === 403) {
      // A missing, wrong or revoked key: retrying won't help, and the app must not notice.
      this.dropped++;
      if (!this.authWarned) {
        this.authWarned = true;
        console.warn(AUTH_WARNING);
      }
      return false;
    }
    console.warn(`[stargrain] Core rejected an event (${res.status}): ${(await res.text()).slice(0, 500)}`);
    return false;
  }

  // --- reading results and remediation (user-initiated: these reject with StargrainError) ------

  private async request<T>(method: string, path: string, body?: unknown, query?: Record<string, string | undefined>): Promise<T> {
    if (!this.apiBase) throw new StargrainError(0, "this Stargrain client has no apiBase, so it can't call Core");
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(query ?? {})) if (v != null) q.set(k, v);
    const url = `${this.apiBase}${path}${q.size ? `?${q}` : ""}`;
    let res: Response;
    try {
      res = await this.fetchImpl(url, {
        method,
        headers: this.headers(body !== undefined),
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: AbortSignal.timeout(this.opts.timeoutMs ?? 5_000),
      });
    } catch (e) {
      throw new StargrainError(0, `could not reach Core at ${this.apiBase} (${(e as Error)?.name ?? "error"})`);
    }
    const text = await res.text();
    let payload: unknown;
    try {
      payload = text ? JSON.parse(text) : undefined;
    } catch {
      payload = undefined;
    }
    if (!res.ok) {
      const p = (payload ?? {}) as { detail?: unknown; reason?: string };
      const detail = typeof p.detail === "string" ? p.detail : res.status === 401 ? "API key missing or invalid" : text.slice(0, 200) || `HTTP ${res.status}`;
      throw new StargrainError(res.status, detail, p.reason);
    }
    return payload as T;
  }

  /** Totals for the key's organization (or user): cost, energy, CO2e, water and the indicator code. */
  summary(options: { since?: TimeArg; until?: TimeArg } = {}): Promise<Summary> {
    return this.request<Summary>("GET", "/v1/summary", undefined, { since: iso(options.since), until: iso(options.until) });
  }

  /** The same totals per UTC day. */
  async summaryDaily(options: { since?: TimeArg; until?: TimeArg } = {}): Promise<DailySummary[]> {
    const body = await this.request<{ days: DailySummary[] }>("GET", "/v1/summary/daily", undefined, {
      since: iso(options.since),
      until: iso(options.until),
    });
    return body.days ?? [];
  }

  /** Quotes for removing `co2eG` grams of CO2e, cheapest first. A sandbox key gets sandbox quotes. */
  async requestQuote(co2eG: number, tierPreference?: string[]): Promise<Quote[]> {
    const req: Record<string, unknown> = { co2e_g: co2eG };
    if (tierPreference?.length) req.tier_preference = tierPreference;
    return (await this.request<{ quotes: Quote[] }>("POST", "/v1/remediation/quotes", req)).quotes ?? [];
  }

  /** Place an order for a quote. Each quote can be used once, within 15 minutes. */
  submitOrder(quote: string | Quote, paymentRef?: string): Promise<Order> {
    const req: Record<string, unknown> = { quote_id: typeof quote === "string" ? quote : quote.quote_id };
    if (paymentRef !== undefined) req.payment_ref = paymentRef;
    return this.request<Order>("POST", "/v1/remediation/orders", req);
  }

  /** An order's fulfillment status and certificate. */
  orderStatus(order: string | Order): Promise<Order> {
    const id = typeof order === "string" ? order : order.remediation_order_id;
    return this.request<Order>("GET", `/v1/remediation/orders/${encodeURIComponent(id)}`);
  }
}
