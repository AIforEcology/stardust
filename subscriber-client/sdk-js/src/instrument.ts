// Auto-instrument Anthropic, OpenAI and Google Gen AI clients.
//
// `instrument(client, stardust)` wraps the client's generation methods in place, so every
// call is metered, including streams. The vendor's own return values come back (with
// promise extras like `.withResponse()` still available), and a metering failure never
// breaks the API call.
//
// - @anthropic-ai/sdk: messages.create (with or without stream: true)
// - openai: chat.completions.create and responses.create. Streaming chat completions only
//   report usage when you pass stream_options: { include_usage: true }.
// - @google/genai: models.generateContent and models.generateContentStream

import type { Stardust } from "./client.ts";
import { fromAnthropic, fromGemini, fromOpenAI, type Provider, type Usage } from "./extract.ts";

type AnyFn = (...args: unknown[]) => unknown;
type Extractor = (r: unknown, model?: string) => Usage | undefined;

const INSTRUMENTED = Symbol.for("stardust.instrumented");

interface Accumulator {
  feed(event: unknown): void;
  usage(): Usage | undefined;
}

function field(obj: unknown, name: string): unknown {
  return obj != null && typeof obj === "object" ? (obj as Record<string, unknown>)[name] : undefined;
}

function anthropicAcc(model?: string): Accumulator {
  // message_start carries input usage; message_delta carries the running output count.
  const usage: Record<string, number> = {};
  let m = model;
  const merge = (u: unknown) => {
    for (const k of ["input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"]) {
      const v = field(u, k);
      if (typeof v === "number") usage[k] = v;
    }
  };
  return {
    feed(event) {
      const type = field(event, "type");
      if (type === "message_start") {
        const message = field(event, "message");
        m = (field(message, "model") as string | undefined) ?? m;
        merge(field(message, "usage"));
      } else if (type === "message_delta") {
        merge(field(event, "usage"));
      }
    },
    usage: () => (Object.keys(usage).length ? fromAnthropic({ model: m, usage }, m) : undefined),
  };
}

function lastUsageAcc(extractor: Extractor, model?: string, unwrap?: (e: unknown) => unknown): Accumulator {
  let final: Usage | undefined;
  return {
    feed(event) {
      const e = unwrap ? unwrap(event) : event;
      const u = extractor(e, model);
      if (u) final = u;
    },
    usage: () => final,
  };
}

const openaiUnwrap = (e: unknown) => {
  const type = field(e, "type");
  return type === "response.completed" || type === "response.incomplete" ? field(e, "response") : e;
};

const ACCUMULATORS: Record<Provider, (model?: string) => Accumulator> = {
  anthropic: anthropicAcc,
  openai: (m) => lastUsageAcc((r, mm) => (field(r, "usage") != null ? fromOpenAI(r, mm) : undefined), m, openaiUnwrap),
  google: (m) => lastUsageAcc(fromGemini, m),
};

/** Proxy a vendor async-iterable stream: events pass through, usage is recorded when it ends. */
function proxyStream<T extends object>(stream: T, acc: Accumulator, done: (u: Usage | undefined) => void): T {
  let finished = false;
  const finish = () => {
    if (!finished) {
      finished = true;
      done(acc.usage());
    }
  };
  const source = stream as unknown as AsyncIterable<unknown>;
  async function* iterate() {
    try {
      for await (const event of source) {
        try {
          acc.feed(event);
        } catch (e) {
          console.warn("[stardust] could not read a stream event", e);
        }
        yield event;
      }
    } finally {
      finish();
    }
  }
  return new Proxy(stream, {
    get(target, prop) {
      if (prop === Symbol.asyncIterator) return () => iterate();
      const value = Reflect.get(target, prop, target);
      return typeof value === "function" ? value.bind(target) : value;
    },
  });
}

/** Chain metering onto a vendor promise while keeping its extra methods (e.g. APIPromise.withResponse). */
function keepExtras<T extends object>(original: T, chained: Promise<unknown>): Promise<unknown> {
  return new Proxy(chained, {
    get(target, prop) {
      const source = prop in target ? target : original;
      const value = Reflect.get(source, prop, source);
      return typeof value === "function" ? value.bind(source) : value;
    },
  });
}

function wrap(
  owner: Record<string, unknown> | undefined,
  method: string,
  stardust: Stardust,
  provider: Provider,
  extractor: Extractor,
  region: string | undefined,
  alwaysStream = false,
): boolean {
  const original = owner?.[method] as (AnyFn & { [INSTRUMENTED]?: boolean }) | undefined;
  if (typeof original !== "function" || original[INSTRUMENTED]) return false;

  const record = (u: Usage | undefined) => {
    if (u) stardust.record(u, { region });
  };
  const handle = (result: unknown, params: unknown): unknown => {
    const model = field(params, "model") as string | undefined;
    try {
      if (alwaysStream || field(params, "stream") === true) {
        if (result != null && typeof result === "object" && Symbol.asyncIterator in result) {
          return proxyStream(result, ACCUMULATORS[provider](model), record);
        }
        return result;
      }
      record(extractor(result, model));
    } catch (e) {
      console.warn("[stardust] metering failed", e);
    }
    return result;
  };

  const wrapper = function (this: unknown, ...args: unknown[]) {
    const result = original.apply(this ?? owner, args);
    if (result != null && typeof (result as Promise<unknown>).then === "function") {
      const chained = (result as Promise<unknown>).then((r) => handle(r, args[0]));
      // Metering attaches at call time, so calls read via extras like .withResponse() are metered too.
      // If the call fails and the caller only uses those extras, this branch's rejection must not
      // surface as an unhandled rejection; callers awaiting the result still see the error.
      chained.catch(() => undefined);
      return keepExtras(result as object, chained);
    }
    return handle(result, args[0]);
  } as AnyFn & { [INSTRUMENTED]?: boolean };
  wrapper[INSTRUMENTED] = true;
  owner![method] = wrapper;
  return true;
}

type Client = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

/** Meter every generation call made through `client`. Returns the same client. */
export function instrument<T extends object>(client: T, stardust: Stardust, options: { region?: string } = {}): T {
  const c = client as Client;
  const { region } = options;
  if (typeof c.messages?.create === "function" && !c.chat) {
    wrap(c.messages, "create", stardust, "anthropic", fromAnthropic, region);
  } else if (typeof c.chat?.completions?.create === "function") {
    wrap(c.chat.completions, "create", stardust, "openai", fromOpenAI, region);
    wrap(c.responses, "create", stardust, "openai", fromOpenAI, region);
  } else if (typeof c.models?.generateContent === "function") {
    wrap(c.models, "generateContent", stardust, "google", fromGemini, region);
    wrap(c.models, "generateContentStream", stardust, "google", fromGemini, region, true);
  } else {
    throw new TypeError("Stardust can't instrument this client: expected an Anthropic, OpenAI or Google Gen AI client");
  }
  return client;
}
