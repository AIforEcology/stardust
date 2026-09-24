// Per-site adapters for AI chat web apps (spec §12.3–§12.4 browser fallback path).
// Selectors track each app's current DOM and WILL drift; when one breaks, only this
// file changes. Chat web apps don't expose token counts, so tokens are always estimated
// (Estimated tier, §8.3); where the page shows the selected model, it is read too.

export interface SiteAdapter {
  id: string;
  name: string;
  provider: string;
  hosts: string[];
  /** Matches each assistant reply, in document order. */
  assistantSelector: string;
  /** Matches each user turn, in document order. */
  userSelector: string;
  /** Ancestor attribute that is "true" while a reply is still streaming. */
  streamingAttr?: string;
  /** Placeholders a virtualized transcript uses for turns it hasn't rendered. */
  spacerSelector?: string;
  /** Element whose aria-label names the selected model. */
  modelSelector?: string;
  /** Turn the model element's label into an API model id; undefined if unrecognized. */
  parseModel?: (label: string) => string | undefined;
  /** Whether the selectors were checked against the live site (and when). */
  verified?: string;
}

/** "Model: Opus 5.5 Medium" → "claude-opus-5-5". The trailing effort level is ignored. */
export function parseClaudeModel(label: string): string | undefined {
  const m = label.replace(/^\s*model:\s*/i, "").match(/\b(opus|sonnet|haiku|fable)\s+(\d+(?:\.\d+)?)\b/i);
  return m ? `claude-${m[1].toLowerCase()}-${m[2].replace(".", "-")}` : undefined;
}

export const SITES: SiteAdapter[] = [
  {
    id: "chatgpt",
    name: "ChatGPT",
    provider: "openai",
    hosts: ["chatgpt.com", "chat.openai.com"],
    assistantSelector: '[data-message-author-role="assistant"]',
    userSelector: '[data-message-author-role="user"]',
  },
  {
    id: "claude",
    name: "Claude",
    provider: "anthropic",
    hosts: ["claude.ai"],
    assistantSelector: ".font-claude-response",
    userSelector: '[data-testid="user-message"]',
    streamingAttr: "data-is-streaming",
    spacerSelector: '[data-testid="transcript-spacer"]',
    modelSelector: '[data-testid="model-selector-dropdown"]',
    parseModel: parseClaudeModel,
    verified: "2026-09-24",
  },
  {
    id: "gemini",
    name: "Gemini",
    provider: "google",
    hosts: ["gemini.google.com"],
    assistantSelector: "model-response",
    userSelector: "user-query",
  },
];

export function siteForHost(host: string): SiteAdapter | undefined {
  return SITES.find((s) => s.hosts.includes(host.toLowerCase()));
}

export function siteById(id: string): SiteAdapter | undefined {
  return SITES.find((s) => s.id === id);
}
