import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

// Minimal chrome.storage.local and fetch stand-ins for settings.ts.
const store: Record<string, unknown> = {};
(globalThis as unknown as { chrome: unknown }).chrome = {
  storage: {
    local: {
      get: async (keys: string | string[]) => Object.fromEntries((Array.isArray(keys) ? keys : [keys]).filter((k) => k in store).map((k) => [k, store[k]])),
      set: async (o: Record<string, unknown>) => void Object.assign(store, o),
      remove: async (k: string) => void delete store[k],
    },
  },
};

const { coreFetch, KeyRevokedError } = await import("../src/settings.ts");

let calls: string[] = [];
let respond: (path: string, auth: string | null) => Response;
globalThis.fetch = (async (url: string, init?: RequestInit) => {
  const path = new URL(url).pathname;
  const auth = new Headers(init?.headers).get("authorization");
  calls.push(`${init?.method ?? "GET"} ${path} ${auth ?? "-"}`);
  return respond(path, auth);
}) as typeof fetch;

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
let n = 0;
const install = () => json(200, { key: `sdk_k${++n}`, user_id: `user-${n}` });

beforeEach(() => {
  for (const k of Object.keys(store)) delete store[k];
  store.apiBase = "https://core.example";
  calls = [];
  n = 0;
});

test("a revoked key is not replaced: no re-registration, KeyRevokedError", async () => {
  respond = (path) => (path === "/v1/installs" ? install() : json(401, { reason: "unauthorized", key_revoked: true }));
  await assert.rejects(coreFetch("/v1/summary"), KeyRevokedError);
  assert.deepEqual(calls, ["POST /v1/installs -", "GET /v1/summary Bearer sdk_k1"]);
  assert.deepEqual(store["auth:https://core.example"], { key: "sdk_k1", userId: "user-1" }, "the revoked key is kept, not swapped");
});

test("an unknown key (Core was reset) re-registers once and retries", async () => {
  respond = (path, auth) => (path === "/v1/installs" ? install() : auth === "Bearer sdk_k1" ? json(401, { reason: "unauthorized" }) : json(200, { events: 0 }));
  const { r, userId } = await coreFetch("/v1/summary");
  assert.equal(r.status, 200);
  assert.equal(userId, "user-2");
  assert.deepEqual(calls, ["POST /v1/installs -", "GET /v1/summary Bearer sdk_k1", "POST /v1/installs -", "GET /v1/summary Bearer sdk_k2"]);
});

test("a second 401 after re-registering is returned, not retried forever", async () => {
  respond = (path) => (path === "/v1/installs" ? install() : json(401, { reason: "unauthorized" }));
  const { r } = await coreFetch("/v1/summary");
  assert.equal(r.status, 401);
  assert.equal(calls.length, 4);
});
