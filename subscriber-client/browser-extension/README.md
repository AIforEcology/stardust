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
- **Counts only.** The content script sends character counts, never message text. Tokens are estimated at 4 characters per token.
- **Estimated tier.** Chat web apps show neither the model nor token counts, so events go out as `model: "unknown"` with `tokens_estimated: true`. Core then applies conservative factors and tags the result Estimated (§8.3).
- **Input tokens** count every earlier visible message in the conversation, since chat apps resend it as context. System prompts and hidden context can't be seen, so input is undercounted.

## Site selectors

[`src/sites.ts`](src/sites.ts) holds the CSS selectors for each chat app. They follow each app's current page structure and will break when the apps change; they have not yet been verified against the live sites.

## Scripts

`npm run typecheck`, `npm test`, `npm run build`, `npm run watch`
