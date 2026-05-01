/**
 * Copies the built atlas HTML into vercel_site/index.html for Vercel static hosting.
 * Run `python build_sjv_equity_atlas_html.py` first so data/clean/sjv_equity_atlas.html exists.
 */
const fs = require("fs");
const path = require("path");

const root = path.join(__dirname, "..");
const src = path.join(root, "data", "clean", "sjv_equity_atlas.html");
const outDir = path.join(root, "vercel_site");
const dest = path.join(outDir, "index.html");

if (!fs.existsSync(src)) {
  console.error(
    "Missing source file:\n  " +
      src +
      "\nRun: python build_sjv_equity_atlas_html.py\n"
  );
  process.exit(1);
}

fs.mkdirSync(outDir, { recursive: true });
fs.copyFileSync(src, dest);
console.log("Wrote", path.relative(root, dest));
