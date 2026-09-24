// Token estimation for surfaces that don't expose usage (Estimated tier, spec §8.3).

/** Common rule of thumb for English text with GPT/Claude-family tokenizers. */
export const CHARS_PER_TOKEN = 4;

export function estimateTokens(chars: number): number {
  if (!Number.isFinite(chars) || chars <= 0) return 0;
  return Math.ceil(chars / CHARS_PER_TOKEN);
}

/** ISO 8601 with the client's local UTC offset retained (spec §7 timestamp). */
export function isoWithOffset(d: Date): string {
  const pad = (n: number, w = 2) => String(Math.trunc(Math.abs(n))).padStart(w, "0");
  const off = -d.getTimezoneOffset();
  const sign = off >= 0 ? "+" : "-";
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` +
    `T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}.${pad(d.getMilliseconds(), 3)}` +
    `${sign}${pad(off / 60)}:${pad(off % 60)}`
  );
}
