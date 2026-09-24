// Read token usage from provider API responses (spec §12.2–§12.4, Measured-tier tokens).
// Works on the vendor SDK objects and on raw JSON from the HTTP APIs.

export type Provider = "anthropic" | "openai" | "google";

export interface Usage {
  provider: Provider;
  model: string;
  tokensIn: number;
  tokensOut: number;
  tokensCachedIn?: number;
}

type Obj = Record<string, unknown>;

function get(obj: unknown, ...names: string[]): unknown {
  if (obj == null || typeof obj !== "object") return undefined;
  for (const n of names) {
    const v = (obj as Obj)[n];
    if (v != null) return v;
  }
  return undefined;
}

function num(v: unknown): number {
  return typeof v === "number" && Number.isFinite(v) ? v : 0;
}

function str(v: unknown): string | undefined {
  return typeof v === "string" && v ? v : undefined;
}

/** Anthropic Messages API. `input_tokens` excludes cache reads/writes, so they are added back. */
export function fromAnthropic(response: unknown, model?: string): Usage | undefined {
  const usage = get(response, "usage");
  if (usage == null) return undefined;
  const cacheRead = num(get(usage, "cache_read_input_tokens"));
  const cacheWrite = num(get(usage, "cache_creation_input_tokens"));
  return {
    provider: "anthropic",
    model: str(get(response, "model")) ?? model ?? "unknown",
    tokensIn: num(get(usage, "input_tokens")) + cacheRead + cacheWrite,
    tokensOut: num(get(usage, "output_tokens")),
    tokensCachedIn: cacheRead || undefined,
  };
}

/** OpenAI Chat Completions (`prompt_tokens`) or Responses API (`input_tokens`). Cached and reasoning tokens are already in the totals. */
export function fromOpenAI(response: unknown, model?: string): Usage | undefined {
  const usage = get(response, "usage");
  if (usage == null) return undefined;
  const chat = get(usage, "prompt_tokens") != null;
  const details = get(usage, chat ? "prompt_tokens_details" : "input_tokens_details");
  const cached = num(get(details, "cached_tokens"));
  return {
    provider: "openai",
    model: str(get(response, "model")) ?? model ?? "unknown",
    tokensIn: num(get(usage, chat ? "prompt_tokens" : "input_tokens")),
    tokensOut: num(get(usage, chat ? "completion_tokens" : "output_tokens")),
    tokensCachedIn: cached || undefined,
  };
}

/** Gemini API / Vertex AI `usageMetadata`. Thinking tokens bill as output. */
export function fromGemini(response: unknown, model?: string): Usage | undefined {
  const meta = get(response, "usageMetadata", "usage_metadata");
  if (meta == null) return undefined;
  const cached = num(get(meta, "cachedContentTokenCount", "cached_content_token_count"));
  return {
    provider: "google",
    model: str(get(response, "modelVersion", "model_version")) ?? model ?? "unknown",
    tokensIn: num(get(meta, "promptTokenCount", "prompt_token_count")) + num(get(meta, "toolUsePromptTokenCount", "tool_use_prompt_token_count")),
    tokensOut: num(get(meta, "candidatesTokenCount", "candidates_token_count")) + num(get(meta, "thoughtsTokenCount", "thoughts_token_count")),
    tokensCachedIn: cached || undefined,
  };
}

export function detectProvider(response: unknown): Provider | undefined {
  if (response == null || typeof response !== "object") return undefined;
  if (get(response, "usageMetadata", "usage_metadata") != null) return "google";
  const usage = get(response, "usage");
  const object = get(response, "object");
  if (get(usage, "prompt_tokens") != null || object === "chat.completion" || object === "response") return "openai";
  if (get(response, "type") === "message" || get(usage, "cache_read_input_tokens") != null) return "anthropic";
  return undefined;
}

const EXTRACTORS: Record<Provider, (r: unknown, m?: string) => Usage | undefined> = {
  anthropic: fromAnthropic,
  openai: fromOpenAI,
  google: fromGemini,
};

export function extract(response: unknown, provider?: Provider, model?: string): Usage | undefined {
  const p = provider ?? detectProvider(response);
  return p ? EXTRACTORS[p](response, model) : undefined;
}
