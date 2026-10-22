// Which text in a chat turn counts toward its tokens. Kept separate from content.ts (and typed
// against a minimal element interface) so it can be tested without a browser.

import type { SiteAdapter } from "./sites.ts";

/** The parts of a DOM element this module uses. */
export interface TurnElement {
  textContent: string | null;
  matches(selector: string): boolean;
  querySelectorAll(selector: string): ArrayLike<TurnElement>;
}

/**
 * The text a turn contributes: for user turns with `userTextSelector`, only the typed text;
 * for replies with `replyTextSelector`, only the answer (not screen-reader headings, status
 * lines or action buttons). Otherwise the whole element.
 */
type TurnAdapter = Pick<SiteAdapter, "userSelector" | "userTextSelector" | "replyTextSelector">;

function countedParts(adapter: TurnAdapter, el: TurnElement): string[] {
  const parts = el.matches(adapter.userSelector) ? adapter.userTextSelector : adapter.replyTextSelector;
  if (!parts) return [(el.textContent ?? "").trim()];
  return Array.from(el.querySelectorAll(parts)).map((part) => (part.textContent ?? "").trim());
}

export function turnText(adapter: TurnAdapter, el: TurnElement): string {
  return countedParts(adapter, el).join("\n");
}

export function turnChars(adapter: TurnAdapter, el: TurnElement): number {
  return countedParts(adapter, el).reduce((n, part) => n + part.length, 0);
}
