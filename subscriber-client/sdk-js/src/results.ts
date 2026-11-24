// Typed results for the reading and remediation helpers; each mirrors a Core REST response.
// Core may add fields later; they come through untyped.

/** A user-initiated call to Core failed. `status` is the HTTP status (0: Core unreachable). */
export class StargrainError extends Error {
  readonly status: number;
  readonly reason?: string;
  constructor(status: number, message: string, reason?: string) {
    super(`Core: ${message}${status ? ` (HTTP ${status})` : ""}`);
    this.name = "StargrainError";
    this.status = status;
    this.reason = reason;
  }
}

/** `GET /v1/summary`: totals for the key's organization (or user). */
export interface Summary {
  events: number;
  tokens_in: number;
  tokens_out: number;
  tokens_cached_in: number;
  cost_usd: number;
  cost_unknown_events: number;
  energy_wh: number;
  co2e_g: number;
  water_ml: number;
  indicator_code: string;
  methodology_version: string;
  /** Present and true for a sandbox key: these totals are test data only. */
  sandbox?: boolean;
  [field: string]: unknown;
}

/** One UTC day from `GET /v1/summary/daily`. */
export interface DailySummary {
  day: string;
  events: number;
  tokens_in: number;
  tokens_out: number;
  cost_usd: number;
  energy_wh: number;
  co2e_g: number;
  water_ml: number;
  [field: string]: unknown;
}

/** A remediation quote. `price_usd` is the provider's price; AIforE's disclosed fee is separate. */
export interface Quote {
  quote_id: string;
  provider_id: string;
  project_id: string;
  co2e_g: number;
  price_usd: number;
  fee_pct: number;
  fee_usd: number;
  total_usd: number;
  fulfillment_days: number;
  provider_tier: string;
  expires_at: string;
  sandbox: boolean;
  [field: string]: unknown;
}

export interface Certificate {
  ref: string;
  sandbox: boolean;
  note?: string;
}

/** A remediation order. A sandbox order is a test: no carbon was removed and no money moved. */
export interface Order {
  remediation_order_id: string;
  quote_id: string | null;
  provider_id: string | null;
  provider_tier: string | null;
  co2e_g_offset: number;
  fulfillment_status: "ordered" | "fulfilling" | "fulfilled" | "failed" | string;
  certificate_ref: string | null;
  certificate: Certificate | null;
  total_usd: number | null;
  fee_usd: number | null;
  sandbox: boolean;
  notice?: string;
  [field: string]: unknown;
}
