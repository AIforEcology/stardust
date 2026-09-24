// Queues usage events and sends them to Stardust Core in the background.
// Recording never blocks or throws in the caller's code path (spec §15), and events are
// buffered and retried with backoff while Core is unreachable (§15 offline resilience).

import { extract, type Provider, type Usage } from "./extract.ts";

export interface StardustOptions {
  apiBase?: string;
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

export class Stardust {
  readonly url: string;
  dropped = 0;
  private readonly opts: StardustOptions;
  private readonly fetchImpl: typeof fetch;
  private readonly buffer: UsageEvent[] = [];
  private sending = false;
  private backoffMs = 0;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private flushWaiters: Array<() => void> = [];
  private flushing = 0;
  private closed = false;

  constructor(opts: StardustOptions = {}) {
    this.opts = { enabled: true, maxBuffer: 10_000, timeoutMs: 5_000, ...opts };
    this.url = `${(opts.apiBase ?? "http://localhost:8080").replace(/\/+$/, "")}/v1/events`;
    this.fetchImpl = opts.fetch ?? globalThis.fetch.bind(globalThis);
  }

  get pending(): number {
    return this.buffer.length + (this.sending ? 1 : 0);
  }

  record(usage: Usage, options: { region?: string; timestamp?: Date } = {}): void {
    if (!this.opts.enabled || this.closed) return;
    const event: UsageEvent = {
      event_id: crypto.randomUUID(),
      source_layer: "infra_agent",
      provider: usage.provider,
      model: usage.model,
      timestamp: isoWithOffset(options.timestamp ?? new Date()),
      tokens_in: usage.tokensIn,
      tokens_out: usage.tokensOut,
      tokens_estimated: false,
    };
    const optional: UsageEvent = {
      tokens_cached_in: usage.tokensCachedIn,
      region: options.region ?? this.opts.region,
      user_id: this.opts.userId,
      org_id: this.opts.orgId,
    };
    for (const [k, v] of Object.entries(optional)) if (v != null) event[k] = v;

    if (this.buffer.length >= (this.opts.maxBuffer ?? 10_000)) {
      this.buffer.shift();
      this.dropped++;
      console.warn("[stardust] buffer full; dropping the oldest event");
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
      console.warn("[stardust] could not read usage from a response", e);
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
    if (this.buffer.length) console.warn(`[stardust] closed with ${this.buffer.length} unsent events`);
  }

  private kick(now = false): void {
    if (this.sending || this.closed) return;
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
      res = await this.fetchImpl(this.url, {
        method: "POST",
        headers: { "content-type": "application/json" },
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
          console.warn("[stardust] onResult failed", e);
        }
      }
      return false;
    }
    if (res.status >= 500 || res.status === 429) return true;
    console.warn(`[stardust] Core rejected an event (${res.status}): ${(await res.text()).slice(0, 500)}`);
    return false;
  }
}
