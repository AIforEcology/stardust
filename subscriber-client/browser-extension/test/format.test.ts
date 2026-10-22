import assert from "node:assert/strict";
import { test } from "node:test";

import { estimateTokens, isoWithOffset } from "../src/estimate.ts";
import { badgeText, codeParts, energySourceLabel, fmtArea, fmtUsd, forestM2, fossilCo2G, treeIcons } from "../src/format.ts";
import { siteForHost } from "../src/sites.ts";

test("estimateTokens", () => {
  assert.equal(estimateTokens(0), 0);
  assert.equal(estimateTokens(-5), 0);
  assert.equal(estimateTokens(NaN), 0);
  assert.equal(estimateTokens(1), 1);
  assert.equal(estimateTokens(400), 100);
});

test("isoWithOffset keeps a parseable local offset", () => {
  const d = new Date("2026-09-23T12:34:56.789Z");
  const s = isoWithOffset(d);
  assert.match(s, /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}[+-]\d\d:\d\d$/);
  assert.equal(new Date(s).getTime(), d.getTime());
});

test("energy source copy never overstates a plurality (§5.3.3)", () => {
  const base = { grid_style: "grid-blend" as const, grid_diversified: false };
  assert.equal(energySourceLabel({ ...base, energy_source_code: "NGP", grid_majority_share_pct: 42.6 }), "Grid blend: 43% NGP (natural gas)");
  assert.equal(energySourceLabel({ ...base, energy_source_code: "NGP", grid_majority_share_pct: 22, grid_diversified: true }), "Mixed grid, no dominant source");
  assert.equal(energySourceLabel({ ...base, energy_source_code: "UNK", grid_majority_share_pct: null }), "Unknown source");
  assert.equal(energySourceLabel({ energy_source_code: "HYD", grid_style: "dedicated", grid_majority_share_pct: null, grid_diversified: false }), "HYD (hydro)");
});

test("badge and money formatting", () => {
  assert.equal(badgeText("B2-XL"), "B2");
  assert.equal(fmtUsd(null), "unknown");
  assert.equal(fmtUsd(1.5), "$1.50");
  assert.equal(fmtUsd(0.00042), "$0.00042");
});

test("site lookup", () => {
  assert.equal(siteForHost("chatgpt.com")?.provider, "openai");
  assert.equal(siteForHost("Claude.ai")?.provider, "anthropic");
  assert.equal(siteForHost("example.com"), undefined);
});

test("codeParts spells out the indicator code like the calculator", () => {
  assert.equal(codeParts("C4-M"), "Impact C (moderate) · Cost tier 4 · Size M");
  assert.equal(codeParts("AX-XL"), "Impact A (very low) · Cost tier X (unknown) · Size XL");
});

test("fossil and forest comparisons use the calculator's constants", () => {
  // 0.8 Wh: the Claude screenshot fixture.
  assert.ok(Math.abs(fossilCo2G(0.8, "gas") - 0.352) < 1e-9);
  assert.ok(Math.abs(fossilCo2G(0.8, "coal") - 0.84) < 1e-9);
  assert.ok(Math.abs(fossilCo2G(0.8, "oil") - 0.896) < 1e-9);
  assert.ok(Math.abs(forestM2(550) - 1) < 1e-9);
  assert.equal(fmtArea(forestM2(0.2936)), "5 cm²");
  assert.equal(fmtArea(1.234), "1.23 m²");
  assert.equal(fmtArea(12.34), "12.3 m²");
  assert.equal(treeIcons(0), 1);
  assert.equal(treeIcons(0.3), 3);
  assert.equal(treeIcons(50), 10);
});
