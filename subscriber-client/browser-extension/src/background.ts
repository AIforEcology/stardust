// MV3 service worker: turns usage counts from content scripts into Stardust events,
// sends them to Stardust Core, and shows the indicator code on the toolbar badge.

import { isoWithOffset } from "./estimate.ts";
import { badgeText, GRADE_COLORS } from "./format.ts";
import { getSettings } from "./settings.ts";
import { siteById } from "./sites.ts";
import type { EnrichedEvent, Message, TabState, UsageEvent } from "./types.ts";

async function send(event: UsageEvent): Promise<EnrichedEvent> {
  const { apiBase } = await getSettings();
  const r = await fetch(`${apiBase}/v1/events`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(event),
  });
  if (!r.ok) throw new Error(`Stardust Core returned ${r.status}`);
  return (await r.json()) as EnrichedEvent;
}

async function record(tabId: number, event: UsageEvent): Promise<void> {
  let state: TabState;
  try {
    const enriched = await send(event);
    state = { last: enriched };
    await chrome.action.setBadgeText({ tabId, text: badgeText(enriched.indicator_code) });
    await chrome.action.setBadgeBackgroundColor({ tabId, color: GRADE_COLORS[enriched.indicator_code[0]] ?? "#555" });
  } catch (e) {
    state = { error: e instanceof Error ? e.message : String(e) };
    await chrome.action.setBadgeText({ tabId, text: "!" });
    await chrome.action.setBadgeBackgroundColor({ tabId, color: "#777" });
  }
  await chrome.storage.session.set({ [`tab:${tabId}`]: state });
}

chrome.runtime.onMessage.addListener((msg: Message, sender) => {
  void (async () => {
    const { userId, enabledSites } = await getSettings();
    if (msg.type === "stardust:usage") {
      const tabId = sender.tab?.id;
      const site = siteById(msg.siteId);
      // Re-check opt-in here too, so a stale content script can't report after opt-out.
      if (tabId == null || !site || !enabledSites.includes(site.id)) return;
      await record(tabId, {
        source_layer: "browser_ext",
        provider: site.provider,
        model: "unknown",
        timestamp: isoWithOffset(new Date()),
        tokens_in: msg.tokensIn,
        tokens_out: msg.tokensOut,
        tokens_estimated: true,
        user_id: userId,
      });
    } else if (msg.type === "stardust:test-event") {
      await record(msg.tabId, {
        source_layer: "browser_ext",
        provider: "test",
        model: "unknown",
        timestamp: isoWithOffset(new Date()),
        tokens_in: 1000,
        tokens_out: 250,
        tokens_estimated: true,
        user_id: userId,
      });
    }
  })();
  return false;
});

chrome.tabs.onRemoved.addListener((tabId) => {
  void chrome.storage.session.remove(`tab:${tabId}`);
});
