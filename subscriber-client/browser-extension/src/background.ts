// MV3 service worker: turns usage counts from content scripts into Stardust events,
// sends them to Stardust Core, and shows the indicator code on the toolbar badge.

import { isoWithOffset } from "./estimate.ts";
import { badgeText, GRADE_COLORS } from "./format.ts";
import { coreFetch, currentUserId, getSettings } from "./settings.ts";
import { siteById } from "./sites.ts";
import type { EnrichedEvent, Message, TabState, UsageEvent } from "./types.ts";

/**
 * What the Test button sends: a realistic reply on a real, priced model, so the popup shows a
 * cost and an energy source like a real chat would. Change the model here when it's retired.
 */
export const TEST_SAMPLE = { provider: "anthropic", model: "claude-sonnet-5-5", tokensIn: 1000, tokensOut: 250 };

async function send(event: UsageEvent): Promise<EnrichedEvent> {
  const { r } = await coreFetch("/v1/events", {
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

chrome.runtime.onMessage.addListener((msg: Message, sender, sendResponse) => {
  void (async () => {
    const { enabledSites } = await getSettings();
    const userId = await currentUserId();
    if (msg.type === "stardust:usage") {
      const tabId = sender.tab?.id;
      const site = siteById(msg.siteId);
      // Re-check opt-in here too, so a stale content script can't report after opt-out.
      if (tabId == null || !site || !enabledSites.includes(site.id)) return;
      await record(tabId, {
        source_layer: "browser_ext",
        provider: site.provider,
        model: msg.model || "unknown",
        timestamp: isoWithOffset(new Date()),
        tokens_in: msg.tokensIn,
        tokens_out: msg.tokensOut,
        tokens_estimated: true,
        user_id: userId,
      });
    } else if (msg.type === "stardust:test-event") {
      await record(msg.tabId, {
        source_layer: "browser_ext",
        provider: TEST_SAMPLE.provider,
        model: TEST_SAMPLE.model,
        timestamp: isoWithOffset(new Date()),
        tokens_in: TEST_SAMPLE.tokensIn,
        tokens_out: TEST_SAMPLE.tokensOut,
        tokens_estimated: true,
        user_id: userId,
      });
    }
  })().finally(() => sendResponse({ done: true }));
  // Keep the channel open so the popup can wait for Core's answer instead of guessing.
  return true;
});

chrome.tabs.onRemoved.addListener((tabId) => {
  void chrome.storage.session.remove(`tab:${tabId}`);
});
