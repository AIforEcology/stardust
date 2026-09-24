import { energySourceLabel, fmt, fmtUsd } from "./format.ts";
import { DEFAULT_API_BASE, getSettings, setApiBase, setSiteEnabled } from "./settings.ts";
import { siteForHost } from "./sites.ts";
import type { EnrichedEvent, Summary, TabState } from "./types.ts";

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;

function row(label: string, value: string): string {
  return `<div class="row"><span>${label}</span><span>${value}</span></div>`;
}

function renderEvent(e: EnrichedEvent): void {
  $("code").textContent = e.indicator_code;
  $("code").dataset.grade = e.indicator_code[0];
  $("breakdown").innerHTML = [
    row("Model", e.model === "unknown" ? "not shown by site" : e.model),
    row("Tokens in / out", `${e.tokens_in ?? "?"} / ${e.tokens_out ?? "?"}${e.tokens_estimated ? " (est.)" : ""}`),
    row("Cost", fmtUsd(e.cost_usd)),
    row("Electricity", fmt(e.energy_wh, "Wh")),
    row("CO₂e", fmt(e.co2e_g, "g")),
    row("Water", fmt(e.water_ml, "mL")),
    row("Energy source", energySourceLabel(e)),
    row("Confidence", e.confidence_tier),
  ].join("");
  $("sji").textContent = e.job_sji;
}

async function renderSummary(apiBase: string, userId: string): Promise<void> {
  try {
    const r = await fetch(`${apiBase}/v1/summary?user_id=${encodeURIComponent(userId)}`);
    if (!r.ok) throw new Error(String(r.status));
    const s = (await r.json()) as Summary;
    $("summary").innerHTML = [
      row("Operations", String(s.events)),
      row("Overall code", s.indicator_code ?? "–"),
      row("Cost", s.cost_unknown_events ? `${fmtUsd(s.cost_usd)} + ${s.cost_unknown_events} unpriced` : fmtUsd(s.cost_usd)),
      row("Electricity", fmt(s.energy_wh, "Wh")),
      row("CO₂e", fmt(s.co2e_g, "g")),
      row("Water", fmt(s.water_ml, "mL")),
    ].join("");
  } catch {
    $("summary").textContent = `Can't reach Stardust Core at ${apiBase}.`;
  }
}

async function main(): Promise<void> {
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
  } else {
    $("site").hidden = true;
  }

  if (tab?.id != null) {
    const key = `tab:${tab.id}`;
    const state = ((await chrome.storage.session.get(key))[key] ?? {}) as TabState;
    if (state.last) renderEvent(state.last);
    else if (state.error) $("breakdown").textContent = state.error;

    $("test").addEventListener("click", async () => {
      await chrome.runtime.sendMessage({ type: "stardust:test-event", tabId: tab.id });
      setTimeout(() => location.reload(), 500);
    });
  }

  const api = $<HTMLInputElement>("api");
  api.value = settings.apiBase;
  api.placeholder = DEFAULT_API_BASE;
  $("api-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const url = new URL(api.value.trim() || DEFAULT_API_BASE);
    const local = url.hostname === "localhost" || url.hostname === "127.0.0.1";
    // A remote Core needs a host permission the user grants explicitly.
    if (!local && !(await chrome.permissions.request({ origins: [`${url.origin}/*`] }))) return;
    await setApiBase(url.origin);
    location.reload();
  });

  await renderSummary(settings.apiBase, settings.userId);
}

void main();
