import { codeParts, energySourceLabel, fmt, fmtArea, fmtUsd, forestM2, fossilCo2G, treeIcons } from "./format.ts";
import { coreFetch, currentUserId, DEFAULT_API_BASE, getSettings, KeyRevokedError, setApiBase, setSiteEnabled } from "./settings.ts";
import { siteForHost } from "./sites.ts";
import type { EnrichedEvent, PageStatus, Summary, TabState } from "./types.ts";

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;

function row(label: string, value: string, cls = "row"): string {
  return `<div class="${cls}"><span>${label}</span><span>${value}</span></div>`;
}

const TREE_SVG =
  '<svg viewBox="0 0 16 20" width="11" height="14" aria-hidden="true"><path d="M8 1 12.5 7H10.5L14 12H11L15 17H1L5 12H2L5.5 7H3.5Z" fill="currentColor"/><rect x="7" y="17" width="2" height="3" fill="currentColor"/></svg>';

interface Figures {
  code: string | null;
  energyWh: number;
  co2eG: number;
  waterOnsiteMl?: number | null;
  waterOffsiteMl?: number | null;
  /** Water Core recorded before splitting it by source. */
  waterUnsplitMl?: number;
}

/** The calculator's extra comparisons, folded away under "More detail". */
function moreDetail(f: Figures): string {
  const parts: string[] = [];
  if (f.code) parts.push(`<p class="note">${codeParts(f.code)}</p>`);
  if (f.waterOnsiteMl != null && f.waterOffsiteMl != null) {
    parts.push('<div class="sublabel">Water</div>',
      row("Data-center cooling", fmt(f.waterOnsiteMl, "mL"), "row sub"),
      row("Power generation", fmt(f.waterOffsiteMl, "mL"), "row sub"));
    if (f.waterUnsplitMl) parts.push(row("Not split (older events)", fmt(f.waterUnsplitMl, "mL"), "row sub"));
  }
  parts.push('<div class="sublabel">Same electricity, if generated entirely from:</div>',
    row("Natural gas", fmt(fossilCo2G(f.energyWh, "gas"), "g CO₂"), "row sub"),
    row("Coal", fmt(fossilCo2G(f.energyWh, "coal"), "g CO₂"), "row sub"),
    row("Oil", fmt(fossilCo2G(f.energyWh, "oil"), "g CO₂"), "row sub"));
  const m2 = forestM2(f.co2eG);
  const n = treeIcons(m2);
  parts.push('<div class="sublabel">Young Pacific Northwest conifer forest needed to absorb this CO₂e:</div>',
    `<div class="row forest"><span class="trees" role="img" aria-label="About ${n} icons, each about 0.1 square meters of young forest">${TREE_SVG.repeat(n)}</span><span>${fmtArea(m2)} · 1 yr</span></div>`,
    '<p class="note">Fossil figures: U.S. EIA (2023). Forest: Gray, Whittier &amp; Harmon, Ecosphere (2016); excludes soil carbon. Same method as the <a href="https://aifore.org/#calculator" target="_blank" rel="noopener">Token Cost Calculator</a>.</p>');
  return `<details class="more"><summary>More detail</summary>${parts.join("")}</details>`;
}

function renderEvent(e: EnrichedEvent): void {
  $("code").textContent = e.indicator_code;
  $("code").dataset.grade = e.indicator_code[0];
  $("breakdown").innerHTML = [
    row("Model", e.model === "unknown" ? "not detected on this page" : e.model),
    row("Tokens in / out", `${e.tokens_in ?? "?"} / ${e.tokens_out ?? "?"}${e.tokens_estimated ? " (est.)" : ""}`),
    row("Cost", e.cost_usd == null ? (e.model === "unknown" ? "unknown (no model to price)" : "unknown (model not priced yet)") : fmtUsd(e.cost_usd)),
    row("Electricity", fmt(e.energy_wh, "Wh")),
    row("CO₂e", fmt(e.co2e_g, "g")),
    row("Water", fmt(e.water_ml, "mL")),
    row("Heat released", fmt(e.heat_rejected_wh ?? e.energy_wh, "Wh")),
    row("Energy source", e.energy_source_code === "UNK" && e.provider === "test" ? "unknown (old test event)" : energySourceLabel(e)),
    row("Confidence", e.confidence_tier),
    moreDetail({
      code: e.indicator_code, energyWh: e.energy_wh, co2eG: e.co2e_g,
      waterOnsiteMl: e.water_onsite_ml, waterOffsiteMl: e.water_offsite_ml,
    }),
  ].join("");
  $("sji").textContent = e.job_sji;
}

async function renderSummary(apiBase: string): Promise<void> {
  try {
    const userId = await currentUserId();
    const { r } = await coreFetch(`/v1/summary?user_id=${encodeURIComponent(userId)}`);
    if (!r.ok) throw new Error(String(r.status));
    const s = (await r.json()) as Summary;
    $("summary").innerHTML = [
      row("Operations", String(s.events)),
      row("Overall code", s.indicator_code ?? "–"),
      row("Cost", s.cost_unknown_events ? `${fmtUsd(s.cost_usd)} + ${s.cost_unknown_events} unpriced` : fmtUsd(s.cost_usd)),
      row("Electricity", fmt(s.energy_wh, "Wh")),
      row("CO₂e", fmt(s.co2e_g, "g")),
      row("Water", fmt(s.water_ml, "mL")),
      row("Heat released", fmt(s.heat_rejected_wh ?? s.energy_wh, "Wh")),
      moreDetail({
        code: s.indicator_code, energyWh: s.energy_wh, co2eG: s.co2e_g,
        waterOnsiteMl: s.water_onsite_ml, waterOffsiteMl: s.water_offsite_ml, waterUnsplitMl: s.water_unsplit_ml,
      }),
    ].join("");
    $("install-id").textContent = `Install ID: ${userId}`;
  } catch (e) {
    $("summary").textContent = e instanceof KeyRevokedError ? e.message : `Can't reach Stardust Core at ${apiBase}.`;
  }
}

/** Whether this tab is actually being measured, asked of the content script itself. */
async function renderTabStatus(tabId: number): Promise<void> {
  const el = $("tab-status");
  let status: PageStatus | undefined;
  try {
    status = (await chrome.tabs.sendMessage(tabId, { type: "stardust:status" })) as PageStatus | undefined;
  } catch {
    // No content script answered: the page was open before Stardust was installed or reloaded.
  }
  if (!status) {
    el.dataset.state = "warn";
    el.textContent = "Not running on this tab yet. Reload the page to start measuring.";
  } else if (!status.measuring) {
    el.dataset.state = "warn";
    el.textContent = "Measure was off when this page loaded. Reload the page to start measuring.";
  } else {
    el.dataset.state = status.repliesOnPage ? "ok" : "";
    const sent = `${status.repliesSent} new ${status.repliesSent === 1 ? "reply" : "replies"} sent`;
    el.textContent = status.repliesOnPage
      ? `Measuring this tab: ${sent} since the page loaded.`
      : `Measuring this tab, but no replies found on the page${status.repliesSent ? "" : " yet"}.`;
  }
}

async function main(): Promise<void> {
  $("version").textContent = `v${chrome.runtime.getManifest().version}`;
  const settings = await getSettings();
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const host = tab?.url ? new URL(tab.url).hostname : "";
  const site = siteForHost(host);

  const toggle = $<HTMLInputElement>("site-toggle");
  if (site) {
    $("site-name").textContent = site.name;
    toggle.checked = settings.enabledSites.includes(site.id);
    toggle.addEventListener("change", async () => {
      await setSiteEnabled(site.id, toggle.checked);
      if (tab?.id != null) await chrome.tabs.reload(tab.id);
    });
    if (toggle.checked && tab?.id != null) void renderTabStatus(tab.id);
  } else {
    $("site").hidden = true;
  }

  if (tab?.id != null) {
    const key = `tab:${tab.id}`;
    const state = ((await chrome.storage.session.get(key))[key] ?? {}) as TabState;
    if (state.last) renderEvent(state.last);
    else if (state.error) $("breakdown").textContent = state.error;

    $("test").addEventListener("click", async () => {
      const button = $<HTMLButtonElement>("test");
      button.disabled = true;
      button.textContent = "Sending…";
      // Waits until Core has answered (background.ts responds after recording the event).
      await chrome.runtime.sendMessage({ type: "stardust:test-event", tabId: tab.id });
      location.reload();
    });
  }

  const api = $<HTMLInputElement>("api");
  api.value = settings.apiBase;
  api.placeholder = DEFAULT_API_BASE;
  $("api-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const url = new URL(api.value.trim() || DEFAULT_API_BASE);
    const local = url.hostname === "localhost" || url.hostname === "127.0.0.1";
    // A Core other than the built-in ones needs a host permission the user grants explicitly.
    const builtIn = local || url.origin === new URL(DEFAULT_API_BASE).origin;
    if (!builtIn && !(await chrome.permissions.request({ origins: [`${url.origin}/*`] }))) return;
    await setApiBase(url.origin);
    location.reload();
  });

  await renderSummary(settings.apiBase);
}

void main();
