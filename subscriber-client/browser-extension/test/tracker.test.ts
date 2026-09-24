import assert from "node:assert/strict";
import { test } from "node:test";

import { ReplyTracker, type Turn } from "../src/tracker.ts";

// Stand-ins for DOM elements: identity is all the tracker uses.
const el = (name: string) => ({ name });
const user = (e: object, chars = 20): Turn<object> => ({ el: e, kind: "user", chars });
const reply = (e: object, chars: number, streaming = false): Turn<object> => ({ el: e, kind: "reply", chars, streaming });

/** Feed the same transcript `n` times and collect everything reported. */
function settle(tracker: ReplyTracker<object>, turns: Turn<object>[], n = 3) {
  const out = [];
  for (let i = 0; i < n; i++) out.push(...tracker.observe(turns));
  return out;
}

test("a message sent on the page and its streamed reply are reported once", () => {
  const t = new ReplyTracker();
  const [u1, r1] = [el("u1"), el("r1")];
  t.observe([]); // page loads, empty chat
  t.observe([user(u1)]); // user sends
  t.observe([user(u1), reply(r1, 50)]);
  t.observe([user(u1), reply(r1, 400)]);
  const done = settle(t, [user(u1), reply(r1, 900)]);
  assert.equal(done.length, 1);
  assert.equal(done[0].reply.chars, 900);
  assert.equal(done[0].user?.el, u1);
  assert.deepEqual(settle(t, [user(u1), reply(r1, 900)]), []); // never twice
});

test("history already on the page is ignored", () => {
  const t = new ReplyTracker();
  const turns = [user(el("u1")), reply(el("r1"), 900), user(el("u2")), reply(el("r2"), 700)];
  assert.deepEqual(settle(t, turns, 5), []);
});

test("history that renders after the first check is ignored (late load / switching chats)", () => {
  const t = new ReplyTracker();
  t.observe([]); // first check: page still empty
  const history = [user(el("u1")), reply(el("r1"), 900), user(el("u2")), reply(el("r2"), 700)];
  assert.deepEqual(settle(t, history, 5), []);
});

test("older turns loaded by scrolling up are ignored (the Gemini case)", () => {
  const t = new ReplyTracker();
  const [u3, r3] = [el("u3"), el("r3")];
  t.observe([user(u3), reply(r3, 500)]); // only the latest turn rendered at first
  const loaded = [user(el("u1")), reply(el("r1"), 3851), user(el("u2")), reply(el("r2"), 4602), user(u3), reply(r3, 500)];
  assert.deepEqual(settle(t, loaded, 5), []);
});

test("a reply that finishes between observations still counts if its question was sent here", () => {
  const t = new ReplyTracker();
  const [u1, r1] = [el("u1"), el("r1")];
  t.observe([]);
  t.observe([user(u1)]);
  // Tab was in the background: the reply appears complete.
  assert.equal(settle(t, [user(u1), reply(r1, 1200)]).length, 1);
});

test("a regenerated reply (same question, new element) counts because it streams", () => {
  const t = new ReplyTracker();
  const u1 = el("u1");
  t.observe([user(u1), reply(el("old"), 800)]); // history
  const regen = el("regen");
  t.observe([user(u1), reply(regen, 0)]);
  t.observe([user(u1), reply(regen, 300)]);
  assert.equal(settle(t, [user(u1), reply(regen, 750)]).length, 1);
});

test("the site's streaming flag holds a paused reply open", () => {
  const t = new ReplyTracker();
  const [u1, r1] = [el("u1"), el("r1")];
  t.observe([]);
  t.observe([user(u1)]);
  // Paused mid-reply (thinking / tool call) but still flagged as streaming: not finished.
  assert.deepEqual(settle(t, [user(u1), reply(r1, 200, true)], 5), []);
  assert.equal(settle(t, [user(u1), reply(r1, 900)]).length, 1);
});

test("a reply that appears complete with its question in one observation is treated as history", () => {
  // The price of never counting history: a reply that streams entirely within one poll,
  // together with its question, is missed. Real replies take longer than a poll.
  const t = new ReplyTracker();
  t.observe([]);
  assert.deepEqual(settle(t, [user(el("u1")), reply(el("r1"), 40)]), []);
});
