// Content script for AI chat web apps. Watches for finished assistant replies and
// reports *character counts only* as estimated tokens, plus the selected model where the
// page shows it. No message text leaves the page (spec §14.1). Does nothing unless the
// user opted in to this site.

import { accountTurn, fingerprint, hiddenCharsEstimate, remember } from "./context.ts";
import { estimateTokens } from "./estimate.ts";
import { siteForHost, type SiteAdapter } from "./sites.ts";
import { ReplyTracker, type Turn } from "./tracker.ts";
import type { UsageMessage } from "./types.ts";

const POLL_MS = 1000;
/** A reply counts as finished once its length is unchanged for this many polls (and it isn't streaming). */
const STABLE_POLLS = 2;
const TOTALS_KEY = "conversationChars";

const site = siteForHost(location.hostname);

// Decides which replies are new usage rather than history (see tracker.ts).
const tracker = new ReplyTracker<Element>(STABLE_POLLS);
// Replies already reported this page session, by conversation + content fingerprint, so a
// reply the page unmounts and re-renders (virtualized transcripts) isn't counted twice.
const reportedPrints = new Set<string>();

function textLength(el: Element): number {
  return (el.textContent ?? "").trim().length;
}

/** Characters a turn contributes: for user turns with a text selector, only the typed text. */
function turnChars(adapter: SiteAdapter, el: Element): number {
  if (adapter.userTextSelector && el.matches(adapter.userSelector)) {
    return Array.from(el.querySelectorAll(adapter.userTextSelector)).reduce((n, part) => n + textLength(part), 0);
  }
  return textLength(el);
}

function precedes(a: Element, b: Element): boolean {
  return Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
}

function conversationKey(): string {
  return `${site!.id}:${location.pathname}`;
}

function isStreaming(adapter: SiteAdapter, reply: Element): boolean {
  if (!adapter.streamingAttr) return false;
  return reply.closest(`[${adapter.streamingAttr}]`)?.getAttribute(adapter.streamingAttr) === "true";
}

function selectedModel(adapter: SiteAdapter, reply: Element): string | undefined {
  const perReply = adapter.replyModel?.(reply);
  if (perReply) return perReply;
  if (!adapter.modelSelector || !adapter.parseModel) return undefined;
  const el = document.querySelector(adapter.modelSelector);
  const label = el?.getAttribute("aria-label") || el?.textContent || "";
  return adapter.parseModel(label);
}

/** Context before `userTurn` for a conversation we have no running total for: count what's rendered, estimate the rest. */
function measuredContextBefore(adapter: SiteAdapter, userTurn: Element | undefined, reply: Element): number {
  const boundary = userTurn ?? reply;
  const turns = Array.from(document.querySelectorAll(`${adapter.assistantSelector}, ${adapter.userSelector}`));
  const earlier = turns.filter((t) => t !== reply && t !== userTurn && precedes(t, boundary));
  const renderedChars = earlier.reduce((n, t) => n + turnChars(adapter, t), 0);
  if (!adapter.spacerSelector) return renderedChars;

  const rendered = turns.filter((t) => t !== reply);
  const ratioChars = rendered.reduce((n, t) => n + turnChars(adapter, t), 0);
  const ratioPx = rendered.reduce((n, t) => n + t.getBoundingClientRect().height, 0);
  const spacerPx = Array.from(document.querySelectorAll(adapter.spacerSelector))
    .filter((s) => precedes(s, boundary))
    .reduce((n, s) => n + s.getBoundingClientRect().height, 0);
  return renderedChars + hiddenCharsEstimate(spacerPx, ratioChars, ratioPx);
}

async function report(adapter: SiteAdapter, reply: Element, replyChars: number, userTurn: Element | undefined): Promise<void> {
  const userChars = userTurn ? turnChars(adapter, userTurn) : 0;

  const key = conversationKey();
  const totals = ((await chrome.storage.local.get(TOTALS_KEY))[TOTALS_KEY] ?? {}) as Record<string, number>;
  const prior = totals[key] ?? measuredContextBefore(adapter, userTurn, reply);
  const { inputChars, nextContextChars } = accountTurn(prior, userChars, replyChars);
  await chrome.storage.local.set({ [TOTALS_KEY]: remember(totals, key, nextContextChars) });

  const msg: UsageMessage = {
    type: "stardust:usage",
    siteId: adapter.id,
    model: selectedModel(adapter, reply),
    tokensIn: estimateTokens(inputChars),
    tokensOut: estimateTokens(replyChars),
  };
  void chrome.runtime.sendMessage(msg);
}

function tick(): void {
  if (!site) return;
  // Runs in background tabs too (Chrome throttles the timer there): a message sent before
  // switching tabs is still matched with its reply when it finishes.
  const turns: Turn<Element>[] = Array.from(document.querySelectorAll(`${site.assistantSelector}, ${site.userSelector}`)).map((el) => {
    const isUser = el.matches(site.userSelector);
    return {
      el,
      kind: isUser ? "user" : "reply",
      chars: turnChars(site, el),
      streaming: isUser ? false : isStreaming(site, el),
    };
  });

  for (const { reply, user } of tracker.observe(turns)) {
    const print = `${conversationKey()}|${fingerprint((reply.el.textContent ?? "").trim())}`;
    if (reportedPrints.has(print)) continue; // the same reply, re-rendered
    reportedPrints.add(print);
    void report(site, reply.el, reply.chars, user?.el);
  }
}

async function start(): Promise<void> {
  if (!site) return;
  const { enabledSites } = await chrome.storage.local.get("enabledSites");
  if (!(enabledSites as string[] | undefined)?.includes(site.id)) return;
  setInterval(tick, POLL_MS);
}

void start();
