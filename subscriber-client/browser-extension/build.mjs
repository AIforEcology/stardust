import { build, context } from "esbuild";
import { cpSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";

// STARDUST_API_BASE picks the Core this build talks to by default: the hosted Core for store
// builds (npm run build:store), localhost for development.
const apiBase = (process.env.STARDUST_API_BASE || "http://localhost:8080").replace(/\/+$/, "");
const origin = new URL(apiBase).origin;
if (!/^https:\/\//.test(origin) && !/^http:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/.test(origin)) {
  throw new Error(`STARDUST_API_BASE must be https (or localhost), got ${apiBase}`);
}

const watch = process.argv.includes("--watch");
rmSync("dist", { recursive: true, force: true });
mkdirSync("dist", { recursive: true });
// Skip macOS "._" resource forks that exFAT volumes create.
cpSync("static", "dist", { recursive: true, filter: (src) => !src.split("/").pop().startsWith("._") });

// The default Core is reachable without asking; any other Core still needs the user's permission.
const manifest = JSON.parse(readFileSync("dist/manifest.json", "utf8"));
const hostPattern = `${origin}/*`;
if (!manifest.host_permissions.includes(hostPattern) && !/localhost|127\.0\.0\.1/.test(origin)) {
  manifest.host_permissions.push(hostPattern);
}
manifest.version = JSON.parse(readFileSync("package.json", "utf8")).version;
writeFileSync("dist/manifest.json", JSON.stringify(manifest, null, 2) + "\n");

const options = {
  entryPoints: { background: "src/background.ts", content: "src/content.ts", popup: "src/popup.ts" },
  bundle: true,
  format: "iife",
  target: "chrome120",
  outdir: "dist",
  sourcemap: watch ? "inline" : false,
  logLevel: "info",
  define: { __STARDUST_DEFAULT_API_BASE__: JSON.stringify(apiBase) },
};

if (watch) {
  await (await context(options)).watch();
} else {
  await build(options);
  console.log(`Default Stardust Core: ${apiBase}`);
}
