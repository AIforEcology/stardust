// Store screenshots (npm run screenshots): renders the real popup from dist/ with chrome.* and
// fetch stubbed, using data Core computed (store/screenshots/fixtures.json), and writes the PNGs
// the Chrome Web Store and Edge Add-ons ask for into store/screenshots/.
//
// Regenerate fixtures.json by sending the sample events in its header to a local Core, if the
// methodology changes enough that the numbers shown should change.
import { chromium } from "playwright";
import { readFileSync, existsSync } from "node:fs";
import { extname, join } from "node:path";

const OUT = "store/screenshots";
const fixtures = JSON.parse(readFileSync(join(OUT, "fixtures.json"), "utf8"));
const HOSTED = "https://core.aifore.org";
const VERSION = JSON.parse(readFileSync("package.json", "utf8")).version;
if (!existsSync("dist/popup.js")) throw new Error("Run npm run build first");

const scenes = [
  {
    file: "popup-claude.png",
    title: "See what every AI reply costs",
    sub: "Cost, electricity, CO₂e and water for each reply, with an A–F indicator code.",
    url: "https://claude.ai/chat/1",
    sites: ["claude"],
    last: fixtures.claude,
  },
  {
    file: "popup-total.png",
    title: "Your running total",
    sub: "Everything you've measured across ChatGPT, Claude and Gemini, in one place.",
    url: "https://chatgpt.com/c/1",
    sites: ["chatgpt", "claude", "gemini"],
    last: fixtures.chatgpt,
  },
  {
    file: "popup-optin.png",
    title: "Off until you turn it on",
    sub: "You choose which sites to measure. Only character counts are sent, never your messages.",
    url: "https://chatgpt.com/",
    sites: [],
    last: null,
  },
];

// Runs in the popup frame before its script: just enough of chrome.* for popup.ts.
function stubs({ scene, fixtures, hosted, version }) {
  if (window.top === window) return;
  const local = {
    apiBase: hosted, enabledSites: scene.sites, userId: fixtures.user_id,
    [`auth:${hosted}`]: { key: "sdk_screenshot", userId: fixtures.user_id },
  };
  const pick = (store, keys) => {
    const list = typeof keys === "string" ? [keys] : keys;
    return Object.fromEntries(list.filter((k) => k in store).map((k) => [k, store[k]]));
  };
  const session = scene.last ? { "tab:1": { last: scene.last } } : {};
  window.chrome = {
    storage: {
      local: { get: async (k) => pick(local, k), set: async () => {}, remove: async () => {} },
      session: { get: async (k) => pick(session, k), set: async () => {} },
    },
    tabs: { query: async () => [{ id: 1, url: scene.url }], reload: async () => {} },
    runtime: { sendMessage: async () => {}, getManifest: () => ({ version }) },
    permissions: { request: async () => true },
  };
  window.fetch = async (url) => {
    if (String(url).includes("/v1/summary")) {
      return new Response(JSON.stringify(fixtures.summary), { headers: { "content-type": "application/json" } });
    }
    return new Response("{}", { status: 404 });
  };
}

const frame = (s) => `<!doctype html><meta charset="utf-8"><style>
  body { margin: 0; width: 1280px; height: 800px; display: flex; align-items: center; gap: 72px;
         padding: 0 96px; box-sizing: border-box; font-family: system-ui, sans-serif;
         background: radial-gradient(circle at 75% 40%, #16302a 0%, #0a100d 60%); color: #e8f1ec; }
  .text { flex: 1; } h1 { font-size: 46px; line-height: 1.1; margin: 0 0 18px; color: #4AE87A; }
  p { font-size: 22px; line-height: 1.45; margin: 0; color: #b9cdc2; }
  .brand { margin-top: 34px; font-size: 15px; color: #8aa397; letter-spacing: .04em; }
  iframe { border: 1px solid #23312a; border-radius: 10px; width: 320px; height: 560px; zoom: 1.3;
           box-shadow: 0 24px 70px rgba(0,0,0,.55); background: #0d1411; }
</style><div class="text"><h1>${s.title}</h1><p>${s.sub}</p><div class="brand">STARDUST · AI FOR ECOLOGY</div></div>
<iframe src="/popup.html"></iframe>`;

const types = { ".html": "text/html", ".js": "text/javascript", ".png": "image/png", ".json": "application/json" };
const browser = await chromium.launch();
try {
  for (const scene of scenes) {
    const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
    await page.route("https://ext.local/**", (route) => {
      const path = new URL(route.request().url()).pathname;
      if (path === "/frame.html") return route.fulfill({ contentType: "text/html", body: frame(scene) });
      const file = join("dist", path);
      if (!existsSync(file)) return route.fulfill({ status: 404, body: "" });
      return route.fulfill({ contentType: types[extname(file)] ?? "application/octet-stream", body: readFileSync(file) });
    });
    await page.addInitScript(stubs, { scene, fixtures, hosted: HOSTED, version: VERSION });
    await page.goto("https://ext.local/frame.html");
    const popup = page.frames().find((f) => f.url().endsWith("/popup.html"));
    await popup.waitForFunction(() => !document.getElementById("summary").textContent.includes("Loading"));
    // Fit the frame to the popup's content so nothing is cut off or padded.
    const h = await popup.evaluate(() => document.documentElement.scrollHeight);
    // and zoom it as large as fits in 800px with a margin.
    await page.evaluate((h) => {
      const f = document.querySelector("iframe");
      f.style.height = `${h}px`;
      f.style.zoom = String(Math.min(1.3, 740 / h));
    }, h);
    await page.screenshot({ path: join(OUT, scene.file) });
    console.log(`${OUT}/${scene.file}`);
    await page.close();
  }

  // 440x280 promo tile for the Chrome Web Store.
  const icon = readFileSync("static/icons/icon128.png").toString("base64");
  const tile = await browser.newPage({ viewport: { width: 440, height: 280 } });
  await tile.setContent(`<!doctype html><meta charset="utf-8"><style>
    body { margin: 0; width: 440px; height: 280px; display: flex; align-items: center; gap: 22px; padding: 0 32px;
           box-sizing: border-box; font-family: system-ui, sans-serif;
           background: radial-gradient(circle at 80% 30%, #16302a 0%, #0a100d 70%); color: #e8f1ec; }
    img { width: 104px; height: 104px; } h1 { margin: 0 0 8px; font-size: 30px; color: #4AE87A; }
    p { margin: 0; font-size: 16px; line-height: 1.35; color: #b9cdc2; }
  </style><img src="data:image/png;base64,${icon}"><div><h1>Stardust</h1><p>AI Cost &amp; Carbon Meter</p></div>`);
  await tile.screenshot({ path: join(OUT, "promo-tile.png") });
  console.log(`${OUT}/promo-tile.png`);
} finally {
  await browser.close();
}
