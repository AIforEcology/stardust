// Keeps static/manifest.json's version equal to package.json's, the one source of truth.
// `node scripts/sync-version.mjs` copies the version across; `--check` only compares and
// exits 1 when they differ (for CI). Runs on `npm version` and before `build:store`.
import { readFileSync, writeFileSync } from "node:fs";

const manifestPath = new URL("../static/manifest.json", import.meta.url);
const { version } = JSON.parse(readFileSync(new URL("../package.json", import.meta.url), "utf8"));
const text = readFileSync(manifestPath, "utf8");
const current = JSON.parse(text).version;

if (current === version) {
  console.log(`manifest.json version matches package.json (${version})`);
} else if (process.argv.includes("--check")) {
  console.error(`manifest.json version ${current} differs from package.json ${version}; run npm run sync-version`);
  process.exit(1);
} else {
  // Replace only the version value, so the rest of the file keeps its formatting.
  writeFileSync(manifestPath, text.replace(/("version"\s*:\s*)"[^"]*"/, `$1"${version}"`));
  console.log(`manifest.json version ${current} -> ${version}`);
}
