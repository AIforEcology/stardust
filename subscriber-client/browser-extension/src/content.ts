// Content script for AI chat web apps. Watches for finished assistant replies and
// reports *character counts only* as estimated tokens. No message text leaves the
// page (spec §14.1). Does nothing unless the user opted in to this site.

import { estimateTokens } from "./estimate.ts";
import { siteForHost } from "./sites.ts";
import type { UsageMessage } from "./types.ts";

const POLL_MS = 1000;
/** A reply counts as finished once its length is unchanged for this many polls. */
const STABLE_POLLS = 2;

const site = siteForHost(location.hostname);

const reported = new WeakSet<Element>();
const lastLength = new WeakMap<Element, { len: number; stable: number }>();
let baselined = false;

function textLength(el: Element): number {
  return (el.textContent ?? "").trim().length;
}

function precedes(a: Element, b: Element): boolean {
  return Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
}

function tick(): void {
  if (!site || document.visibilityState !== "visible") return;
  const replies = Array.from(document.querySelectorAll(site.assistantSelector));

  // Don't report conversation history that was already on the page when we loaded.
  if (!baselined) {
    replies.forEach((r) => reported.add(r));
    baselined = true;
    return;
  }

  for (const reply of replies) {
    if (reported.has(reply)) continue;
    const len = textLength(reply);
    const prev = lastLength.get(reply);
    const stable = prev && prev.len === len ? prev.stable + 1 : 0;
    lastLength.set(reply, { len, stable });
    if (len === 0 || stable < STABLE_POLLS) continue;

    reported.add(reply);
    // Chat apps resend the whole visible conversation as context each turn, so input
    // is every earlier message. System prompts and hidden context are not visible.
    const turns = Array.from(document.querySelectorAll(`${site.assistantSelector}, ${site.userSelector}`));
    const contextChars = turns.filter((t) => t !== reply && precedes(t, reply)).reduce((n, t) => n + textLength(t), 0);

    const msg: UsageMessage = {
      type: "stardust:usage",
      siteId: site.id,
      tokensIn: estimateTokens(contextChars),
      tokensOut: estimateTokens(len),
    };
    void chrome.runtime.sendMessage(msg);
  }
}

async function start(): Promise<void> {
  if (!site) return;
  const { enabledSites } = await chrome.storage.local.get("enabledSites");
  if (!(enabledSites as string[] | undefined)?.includes(site.id)) return;
  setInterval(tick, POLL_MS);
}

void start();
