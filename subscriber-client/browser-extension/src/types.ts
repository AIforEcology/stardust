// Mirrors schema/usage-event.schema.json and schema/enriched-event.schema.json (spec §7).

export type SourceLayer = "chat_plugin" | "browser_ext" | "infra_agent";
export type ConfidenceTier = "measured" | "modeled" | "estimated";

export interface UsageEvent {
  event_id?: string;
  source_layer: SourceLayer;
  provider: string;
  model: string;
  region?: string | null;
  timestamp: string;
  tokens_in?: number | null;
  tokens_out?: number | null;
  tokens_estimated?: boolean;
  user_id?: string | null;
}

export interface EnrichedEvent extends UsageEvent {
  event_id: string;
  job_sji: string;
  cost_usd: number | null;
  energy_wh: number;
  grid_intensity_g_per_kwh: number;
  co2e_g: number;
  water_ml: number;
  confidence_tier: ConfidenceTier;
  indicator_code: string;
  energy_source_code: string;
  energy_mix_breakdown: Record<string, number> | null;
  grid_style: "dedicated" | "grid-blend";
  grid_majority_share_pct: number | null;
  grid_diversified: boolean;
  model_tier: string;
  methodology_version: string;
}

export interface Summary {
  events: number;
  tokens_in: number;
  tokens_out: number;
  cost_usd: number;
  cost_unknown_events: number;
  energy_wh: number;
  co2e_g: number;
  water_ml: number;
  indicator_code: string | null;
}

/** Content script → background. Only counts ever leave the page, never message text (§14.1). */
export interface UsageMessage {
  type: "stardust:usage";
  siteId: string;
  tokensIn: number;
  tokensOut: number;
}

export interface TestEventMessage {
  type: "stardust:test-event";
  tabId: number;
}

export type Message = UsageMessage | TestEventMessage;

export interface TabState {
  last?: EnrichedEvent;
  error?: string;
}
