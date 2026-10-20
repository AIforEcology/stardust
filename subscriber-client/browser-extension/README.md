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
- **Only new usage.** Pages load history late: on open, when you scroll up (Gemini) and when you switch chats. So a reply counts only if it answers a message sent while the page was open, or if it was seen streaming. History arrives complete, together with its question, so it's never counted. See [`src/tracker.ts`](src/tracker.ts).
- **No double counting.** A reply the page removes and re-adds is recognized by a local fingerprint and isn't reported again.

## Site status

| Site | Replies | Model | Checked |
|---|---|---|---|
| claude.ai | ✅ `.font-claude-response`, streaming via `data-is-streaming` | ✅ from the model picker, e.g. "Opus 5.5" → `claude-opus-5-5` | 2026-09-24, live |
| ChatGPT | ✅ `[data-message-author-role]`, clean text on both sides | ✅ **per reply** from `data-message-model-slug`, e.g. `gpt-5-6` → `gpt-5.6` | 2026-09-24, live |
| Gemini | ✅ `model-response message-content`. User text from `.query-text-line`, since `user-query` also holds a hidden "You said …" copy | ⚠️ mode only ("Flash", "Pro") → `gemini-flash`, which sets the size tier. The app shows no version, so cost stays unknown | 2026-09-24, live |

Live check on claude.ai (2026-09-24):
- Three replies produced exactly three events, with character counts matching the page.
- A reopened conversation's input came out at 2,707 tokens against ~2,700 true (the previous version gave ~1,300).
- The hidden-turn estimate was within 4%.
- Costs matched Opus 5.5 pricing.

Live checks on ChatGPT and Gemini (2026-09-24), via read-only page inspection plus one test message on Gemini:
- The selectors match exactly one element per turn.
- Hidden accessibility text is excluded; without that, Gemini would have counted every user message twice.
- ChatGPT exposes the exact model on every reply, and Core prices `gpt-5.6` at $4 / $20 per million tokens.
- Neither page's streaming marker was confirmed, so on these two sites a reply is "finished" when its length stops changing for 2 seconds.

**Known limits:**
- **Thinking tokens:** extended-thinking and reasoning tokens aren't shown on the page, so output is undercounted for thinking models. The SDKs count them exactly from API usage data.
- **Hidden context:** system prompts, attachments and tool results aren't counted.
- **Edited or retried messages:** these start a new branch of the conversation, but the running total keeps the old one, so later input is overcounted.

[`src/sites.ts`](src/sites.ts) holds each site's selectors. They follow each app's current page structure and will need updating when the apps change.

## Scripts

`npm run typecheck`, `npm test`, `npm run build`, `npm run watch`

## Hosted Core and store builds

`npm run build:store` builds with the hosted Core (`https://core.aifore.org`, or set `STARDUST_API_BASE`) as the default and writes `stardust-extension.zip`, the package for both the Chrome Web Store and Microsoft Edge Add-ons. The hosted Core's address is in the manifest's host permissions, so users aren't asked for it.

On first use the extension registers with the Core (`POST /v1/installs`) and gets its own key and user ID. The key goes with every event and summary request; if the Core no longer knows it, the extension registers again. Keys are kept per Core address, so switching to a self-hosted Core never sends it the hosted key. A Core without self-registration (a local one) answers 404, and the extension uses its local pseudonymous ID with no key, as before.

Store listing text, permission justifications and the privacy policy draft are in [`store/`](store/).

### Store screenshots

`npm run screenshots` renders the real popup (built for the hosted Core) with Chromium and writes the store images to `store/screenshots/`: three 1280×800 screenshots and a 440×280 promo tile. The numbers shown come from `store/screenshots/fixtures.json`, events that Core actually enriched. Regenerate the images after any change to the popup's look.

### Releasing

1. Bump `version` in `package.json` (for example `0.1.0` → `0.1.1`) and merge to main.
2. Tag and push: `git tag extension-v0.1.1 && git push origin extension-v0.1.1`.
3. The **Release extension** workflow checks the tag matches `package.json`, runs the tests, builds `stardust-extension.zip` and attaches it to a GitHub Release.
4. Upload that zip in the Chrome Web Store and Edge Add-ons dashboards. Once each store approves it, users update automatically.
