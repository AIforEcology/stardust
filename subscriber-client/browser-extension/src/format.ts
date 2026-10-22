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

// Comparisons the AIforE Token Cost Calculator shows (aifore.org/#calculator), with the same
// constants, so the popup and the calculator give the same answer.

const GRADE_WORDS: Record<string, string> = {
  A: "very low", B: "low", C: "moderate", D: "high", E: "very high", F: "severe",
};

/** Spells out an indicator code such as "C4-M": impact grade, cost tier, size (spec §5.1). */
export function codeParts(code: string): string {
  const [head = "", size = "?"] = code.split("-");
  const grade = head[0] ?? "?";
  const tier = head.slice(1) || "?";
  const words = GRADE_WORDS[grade];
  return `Impact ${grade}${words ? ` (${words})` : ""} · Cost tier ${tier === "X" ? "X (unknown)" : tier} · Size ${size}`;
}

/** kg CO₂ per kWh if the electricity came entirely from one fuel. U.S. EIA (2023), eia.gov/tools/faqs/faq.php?id=74. */
export const FOSSIL_KG_CO2_PER_KWH = { gas: 0.44, coal: 1.05, oil: 1.12 } as const;

/** Grams of CO₂ for energyWh generated entirely from fuel (Wh × kg/kWh = g). */
export function fossilCo2G(energyWh: number, fuel: keyof typeof FOSSIL_KG_CO2_PER_KWH): number {
  return energyWh * FOSSIL_KG_CO2_PER_KWH[fuel];
}

/**
 * Young Pacific Northwest conifer forest: ~1.5 Mg C/ha/yr net woody-carbon accumulation for 20–100-year-old
 * stands (Gray, Whittier & Harmon, Ecosphere 7(1):e01224, 2016, Fig. 5), i.e. ≈0.55 kg CO₂ per m² per year.
 * Excludes soil carbon and varies widely by site.
 */
export const FOREST_KG_CO2_PER_M2_YR = 0.55;
export const M2_PER_TREE_ICON = 0.1;

/** Square meters of young forest needed for a year to absorb co2eG. */
export function forestM2(co2eG: number): number {
  return co2eG / 1000 / FOREST_KG_CO2_PER_M2_YR;
}

export function fmtArea(m2: number): string {
  if (m2 < 0.01) return `${Math.max(1, Math.round(m2 * 10000))} cm²`;
  return `${m2 < 10 ? m2.toFixed(2) : m2.toFixed(1)} m²`;
}

/** Tree icons for the forest row: one per ~0.1 m², at least 1, at most 10. */
export function treeIcons(m2: number): number {
  return Math.min(10, Math.max(1, Math.round(m2 / M2_PER_TREE_ICON)));
}
