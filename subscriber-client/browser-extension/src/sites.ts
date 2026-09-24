// Per-site adapters for AI chat web apps (spec §12.3–§12.4 browser fallback path).
// Selectors track each app's current DOM and WILL drift; when one breaks, only this
// file changes. None of these apps expose the model or token counts in the page, so
// events are sent with model "unknown" and estimated tokens (Estimated tier, §8.3).

export interface SiteAdapter {
  id: string;
  name: string;
  provider: string;
  hosts: string[];
  /** Matches each assistant reply, in document order. */
  assistantSelector: string;
  /** Matches each user turn, in document order. */
  userSelector: string;
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
    assistantSelector: ".font-claude-response, .font-claude-message",
    userSelector: '[data-testid="user-message"]',
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
