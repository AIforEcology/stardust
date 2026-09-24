import type { EnrichedEvent } from "./types.ts";

const ESC_NAMES: Record<string, string> = {
  NGP: "natural gas", RNG: "renewable natural gas", OPP: "oil", NPP: "nuclear", SMR: "small modular reactor",
  WEC: "wave/marine", HYD: "hydro", SPP: "solar", WPP: "wind", GPP: "geothermal",
  FPP: "fossil, type unresolved", ZEP: "zero-emission, type unresolved", REP: "renewable, type unresolved",
  UNK: "unknown source",
};

/** Energy-source copy that never overstates a plurality source (spec §5.3.3). */
export function energySourceLabel(e: Pick<EnrichedEvent, "energy_source_code" | "grid_style" | "grid_majority_share_pct" | "grid_diversified">): string {
  const code = e.energy_source_code;
  if (code === "UNK") return "Unknown source";
  if (e.grid_style === "dedicated") return `${code} (${ESC_NAMES[code] ?? code})`;
  if (e.grid_diversified) return "Mixed grid, no dominant source";
  const share = e.grid_majority_share_pct == null ? "" : `${Math.round(e.grid_majority_share_pct)}% `;
  return `Grid blend: ${share}${code} (${ESC_NAMES[code] ?? code})`;
}

export function badgeText(code: string): string {
  // Badges fit ~4 characters; show grade + cost tier, the popup shows the full code.
  return code.split("-")[0] ?? "";
}

export const GRADE_COLORS: Record<string, string> = {
  A: "#1f9d55", B: "#4AE87A", C: "#c9b200", D: "#e08a00", E: "#d35400", F: "#c0392b",
};

export function fmt(n: number | null | undefined, unit: string, digits = 3): string {
  if (n == null) return "unknown";
  if (n === 0) return `0 ${unit}`;
  const abs = Math.abs(n);
  const s = abs >= 100 ? n.toFixed(0) : abs >= 1 ? n.toFixed(2) : n.toPrecision(digits);
  return `${s} ${unit}`;
}

export function fmtUsd(n: number | null | undefined): string {
  if (n == null) return "unknown";
  return n >= 0.01 ? `$${n.toFixed(2)}` : `$${n.toPrecision(2)}`;
}
