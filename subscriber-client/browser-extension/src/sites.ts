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
  /** Within a user turn, the parts holding the typed text (excludes hidden labels); all matches are summed. */
  userTextSelector?: string;
  /** Ancestor attribute that is "true" while a reply is still streaming. */
  streamingAttr?: string;
  /** Placeholders a virtualized transcript uses for turns it hasn't rendered. */
  spacerSelector?: string;
  /** Element whose aria-label names the selected model. */
  modelSelector?: string;
  /** Turn the model element's label into an API model id; undefined if unrecognized. */
  parseModel?: (label: string) => string | undefined;
  /** The model that produced a specific reply, when the page records it per message (preferred). */
  replyModel?: (reply: Element) => string | undefined;
  /** Whether the selectors were checked against the live site (and when). */
  verified?: string;
}

/** "Model: Opus 5.5 Medium" → "claude-opus-5-5". The trailing effort level is ignored. */
export function parseClaudeModel(label: string): string | undefined {
  const m = label.replace(/^\s*model:\s*/i, "").match(/\b(opus|sonnet|haiku|fable)\s+(\d+(?:\.\d+)?)\b/i);
  return m ? `claude-${m[1].toLowerCase()}-${m[2].replace(".", "-")}` : undefined;
}

/** ChatGPT's data-message-model-slug uses hyphens where API ids use dots: "gpt-5-6" → "gpt-5.6". */
export function parseChatGPTSlug(slug: string | null | undefined): string | undefined {
  if (!slug) return undefined;
  return slug.trim().toLowerCase().replace(/^gpt-(\d+)-(\d+)(?=$|-)/, "gpt-$1.$2");
}

/**
 * Gemini shows a mode ("Open mode picker, currently Flash"), not a model version. It maps to a
 * family name that sets the size tier; with no exact version there's no price, so cost stays unknown.
 */
export function parseGeminiMode(label: string): string | undefined {
  const m = label.match(/currently\s+(.+?)\s*$/i);
  if (!m) return undefined;
  return `gemini-${m[1].trim().toLowerCase().replace(/\s+/g, "-")}`;
}

export const SITES: SiteAdapter[] = [
  {
    id: "chatgpt",
    name: "ChatGPT",
    provider: "openai",
    hosts: ["chatgpt.com", "chat.openai.com"],
    assistantSelector: '[data-message-author-role="assistant"]',
    userSelector: '[data-message-author-role="user"]',
    replyModel: (reply) => parseChatGPTSlug(reply.getAttribute("data-message-model-slug")),
    verified: "2026-09-24",
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
    // model-response also wraps UI text; message-content is the answer itself.
    assistantSelector: "model-response message-content",
    // user-query carries a hidden "You said …" copy of the message for screen readers.
    userSelector: "user-query",
    userTextSelector: ".query-text-line",
    modelSelector: '[data-test-id="bard-mode-menu-button"]',
    parseModel: parseGeminiMode,
    verified: "2026-09-24",
  },
];

export function siteForHost(host: string): SiteAdapter | undefined {
  return SITES.find((s) => s.hosts.includes(host.toLowerCase()));
}

export function siteById(id: string): SiteAdapter | undefined {
  return SITES.find((s) => s.id === id);
}
