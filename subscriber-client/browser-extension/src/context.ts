// Conversation-context accounting for chat pages (pure functions, unit-tested).
//
// Chat apps resend the whole conversation as input on every turn. Pages like claude.ai
// virtualize the transcript, so earlier turns aren't in the DOM to count. Instead the
// content script keeps a running character total per conversation, stored locally, and
// only falls back to measuring the page for conversations it didn't see from the start.

/** Conversations whose running totals are kept (oldest dropped first). */
export const MAX_TRACKED_CONVERSATIONS = 500;

/**
 * Estimate characters in turns a virtualized transcript hasn't rendered, from the
 * placeholders' pixel height and the characters-per-pixel of the turns it has rendered.
 */
export function hiddenCharsEstimate(spacerPx: number, renderedChars: number, renderedPx: number): number {
  if (spacerPx <= 0 || renderedChars <= 0 || renderedPx <= 0) return 0;
  return Math.round(spacerPx * (renderedChars / renderedPx));
}

export interface TurnResult {
  /** Characters of input the model received for this reply. */
  inputChars: number;
  /** Running total to store for the conversation after this reply. */
  nextContextChars: number;
}

/** Input for a reply = everything before this turn + the user's new message. */
export function accountTurn(priorContextChars: number, userChars: number, replyChars: number): TurnResult {
  const inputChars = priorContextChars + userChars;
  return { inputChars, nextContextChars: inputChars + replyChars };
}

/** Bounded map update: set `key`, keeping at most `max` entries (drops the oldest insertions). */
export function remember(totals: Record<string, number>, key: string, value: number, max = MAX_TRACKED_CONVERSATIONS): Record<string, number> {
  const next = { ...totals };
  delete next[key];
  next[key] = value;
  const keys = Object.keys(next);
  for (const k of keys.slice(0, Math.max(0, keys.length - max))) delete next[k];
  return next;
}

/** Small non-cryptographic hash (FNV-1a) used to recognize a reply that was re-rendered. Never leaves the page. */
export function fingerprint(text: string): string {
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return `${text.length}:${(h >>> 0).toString(36)}`;
}
