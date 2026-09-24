# Stardust browser extension

Chrome/Edge extension (Manifest V3) that meters AI chat usage on ChatGPT, Claude and Gemini, sends it to Stardust Core, and shows the indicator code on the toolbar badge.

## Build and load

```bash
npm install
npm run build        # outputs dist/
```

In Chrome or Edge, open `chrome://extensions`, turn on Developer mode, click **Load unpacked**, and pick `dist/`. Start Core on `http://localhost:8080` (see [middleware](../../middleware/README.md)), then press **Test** in the popup to check the connection.

## How it measures

- **Opt-in per site.** Nothing is measured until you turn on the site in the popup (§14.1).
- **Counts only.** The content script sends character counts and, where the page shows it, the selected model. Never message text. Tokens are estimated at 4 characters per token, so results are tagged Estimated (§8.3).
- **Finished replies only.** A reply is reported once its length stops changing and, on sites that mark it, once streaming has ended. This keeps replies that pause mid-stream from being cut short.
- **Input includes the whole conversation.** Chat apps resend it with every message. The extension keeps a running character total per conversation, stored locally as a number per conversation ID, so each reply's input is everything before it plus the new message.
- **Conversations the extension didn't see from the start:** some pages (claude.ai) render only the last few turns. For a conversation opened partway through, the hidden turns are estimated from the placeholder height, using the characters-per-pixel of the rendered turns.
- **No double counting.** A reply the page removes and re-adds is recognized by a local fingerprint and isn't reported again.

## Site status

| Site | Replies | Model | Checked |
|---|---|---|---|
| claude.ai | ✅ `.font-claude-response`, streaming via `data-is-streaming` | ✅ from the model picker, e.g. "Opus 5.5" → `claude-opus-5-5` | 2026-09-24, live |
| ChatGPT | selectors not yet verified | unknown | — |
| Gemini | selectors not yet verified | unknown | — |

Live check on claude.ai (2026-09-24):
- Three replies produced exactly three events, with character counts matching the page.
- A reopened conversation's input came out at 2,707 tokens against ~2,700 true (the previous version gave ~1,300).
- The hidden-turn estimate was within 4%.
- Costs matched Opus 5.5 pricing.

**Known limits:**
- **Thinking tokens:** extended-thinking and reasoning tokens aren't shown on the page, so output is undercounted for thinking models. The SDKs count them exactly from API usage data.
- **Hidden context:** system prompts, attachments and tool results aren't counted.
- **Edited or retried messages:** these start a new branch of the conversation, but the running total keeps the old one, so later input is overcounted.

[`src/sites.ts`](src/sites.ts) holds each site's selectors. They follow each app's current page structure and will need updating when the apps change.

## Scripts

`npm run typecheck`, `npm test`, `npm run build`, `npm run watch`
