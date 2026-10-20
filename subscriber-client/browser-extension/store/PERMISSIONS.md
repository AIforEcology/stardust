# Permission justifications

Paste each into the store's privacy/permissions form.

**storage** — Saves the user's settings (which sites they turned on, which Stardust server to use), this install's anonymous ID and key, and a per-conversation character count so each reply's input size can be estimated. Stored locally in the browser.

**tabs** — Reads the active tab's address when the popup opens, to show the on/off switch for that chat site, and reloads the tab after the user turns measurement on or off so the change takes effect.

**Host permission: https://core.aifore.org/** — The Stardust server that turns character counts into cost and environmental estimates. The extension sends it the count and model name for each measured reply, and reads back the user's own totals.

**Host permissions: localhost and 127.0.0.1** — Lets developers and organizations run their own Stardust server on their machine instead of AIforE's.

**Content scripts on chatgpt.com, chat.openai.com, claude.ai, gemini.google.com** — Detects when a reply finishes and counts the characters in the user's message and the reply, and reads the model name the page shows. Nothing is measured until the user turns the site on, and message text never leaves the browser; only the counts do.

**Optional host permissions (https://*/*, http://*/*)** — Requested only if the user types the address of their own self-hosted Stardust server in the settings, and only for that one address. Never requested otherwise.

**Remote code:** None. All code is in the package.

**Data use disclosures** (Chrome "Privacy practices" tab):
- Collects: "Website content" is NOT collected (only counts). Collects anonymous usage statistics: character counts, model name, timestamps.
- Not sold, not used for anything unrelated to the single purpose, not used for credit or lending.
