import { build, context } from "esbuild";
import { cpSync, mkdirSync, rmSync } from "node:fs";

const watch = process.argv.includes("--watch");
rmSync("dist", { recursive: true, force: true });
mkdirSync("dist", { recursive: true });
// Skip macOS "._" resource forks that exFAT volumes create.
cpSync("static", "dist", { recursive: true, filter: (src) => !src.split("/").pop().startsWith("._") });

const options = {
  entryPoints: { background: "src/background.ts", content: "src/content.ts", popup: "src/popup.ts" },
  bundle: true,
  format: "iife",
  target: "chrome120",
  outdir: "dist",
  sourcemap: watch ? "inline" : false,
  logLevel: "info",
};

if (watch) {
  await (await context(options)).watch();
} else {
  await build(options);
}
