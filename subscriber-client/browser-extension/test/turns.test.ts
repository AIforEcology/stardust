import assert from "node:assert/strict";
import { test } from "node:test";

import { siteById } from "../src/sites.ts";
import { turnChars, turnText, type TurnElement } from "../src/turns.ts";

/** A tiny stand-in for a DOM element: matches/querySelectorAll by exact selector list entries. */
function el(tags: string[], text: string, children: TurnElement[] = []): TurnElement & { tags: string[] } {
  const node = {
    tags,
    get textContent() {
      return children.length ? children.map((c) => c.textContent).join("") : text;
    },
    matches: (selector: string) => selector.split(",").some((s) => tags.includes(s.trim())),
    querySelectorAll(selector: string): TurnElement[] {
      const out: TurnElement[] = [];
      const visit = (n: TurnElement & { tags?: string[] }, self: boolean) => {
        if (!self && n.matches(selector)) out.push(n);
        for (const c of (n as { children?: TurnElement[] }).children ?? []) visit(c, false);
      };
      visit(node, true);
      return out;
    },
    children,
  };
  return node;
}

const claude = siteById("claude")!;

test("claude.ai (Oct 2026): a reply counts only its markdown answer", () => {
  // Shape measured on claude.ai 2026-10-21: sr-only heading, a turn-status line, the answer, the action bar.
  const answer = "x".repeat(368);
  const reply = el(['[data-testid="assistant-message"]'], "", [
    el(["h2.sr-only"], "h".repeat(59)),
    el(["div"], "", [el(['[data-cds="TurnStatus"]'], "s".repeat(104)), el([".standard-markdown"], answer)]),
    el(['[data-testid="message-actions"]'], "Copy Retry"),
  ]);
  assert.ok(reply.matches(claude.assistantSelector));
  assert.equal(turnChars(claude, reply), 368);
  assert.equal(turnText(claude, reply), answer);
});

test("claude.ai: a reply with several answer blocks (tool use in between) sums them", () => {
  const reply = el(['[data-testid="assistant-message"]'], "", [
    el([".standard-markdown"], "a".repeat(100)),
    el(['[data-cds="TurnStatus"]'], "Searched the web"),
    el([".progressive-markdown"], "b".repeat(50)),
  ]);
  assert.equal(turnChars(claude, reply), 150);
});

test("claude.ai: a reply with no answer yet counts as empty (still starting)", () => {
  const reply = el(['[data-testid="assistant-message"]'], "", [el(["h2.sr-only"], "Claude is responding")]);
  assert.equal(turnChars(claude, reply), 0);
});

test("claude.ai: a user turn counts its whole text", () => {
  const user = el(['[data-testid="user-message"]'], "u".repeat(42));
  assert.equal(turnChars(claude, user), 42);
});

test("sites without a reply text selector count the whole reply (ChatGPT)", () => {
  const chatgpt = siteById("chatgpt")!;
  const reply = el(['[data-message-author-role="assistant"]'], "r".repeat(77));
  assert.equal(turnChars(chatgpt, reply), 77);
});
