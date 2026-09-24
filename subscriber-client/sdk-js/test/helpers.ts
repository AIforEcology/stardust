import { Stardust } from "../src/client.ts";

/** Fake Stardust Core. `script` holds statuses (or "down") consumed in order; default 200. */
export function fakeCore(script: Array<number | "down"> = []) {
  const events: Array<Record<string, unknown>> = [];
  let calls = 0;
  const fetch = (async (_url: string, init: RequestInit) => {
    calls++;
    const step = script.length ? script.shift()! : 200;
    if (step === "down") throw new TypeError("fetch failed");
    const event = JSON.parse(String(init.body));
    if (step === 200) {
      events.push(event);
      return new Response(JSON.stringify({ ...event, indicator_code: "B2-S" }), { status: 200 });
    }
    return new Response('{"detail":"nope"}', { status: step });
  }) as unknown as typeof globalThis.fetch;
  return { events, fetch, get calls() { return calls; } };
}

export function stardustWith(core: ReturnType<typeof fakeCore>, opts = {}) {
  return new Stardust({ apiBase: "http://core.test", region: "us-east-1", fetch: core.fetch, ...opts });
}

export function sse(events: unknown[], named = true): string {
  return events
    .map((e) => {
      if (e === "[DONE]") return "data: [DONE]\n\n";
      const type = (e as { type?: string }).type;
      return named && type ? `event: ${type}\ndata: ${JSON.stringify(e)}\n\n` : `data: ${JSON.stringify(e)}\n\n`;
    })
    .join("");
}

/** A vendor-SDK fetch that returns JSON or an SSE stream depending on the request's `stream` flag. */
export function vendorFetch(json: unknown, stream?: string): typeof globalThis.fetch {
  return (async (_url: string, init: RequestInit) => {
    const body = JSON.parse(String(init.body));
    if (body.stream) return new Response(stream, { status: 200, headers: { "content-type": "text/event-stream" } });
    return new Response(JSON.stringify(json), { status: 200, headers: { "content-type": "application/json" } });
  }) as unknown as typeof globalThis.fetch;
}
