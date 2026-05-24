/**
 * Copies the Python-built standalone atlas HTML into vercel_site (optional artifact).
 * Does NOT overwrite vercel_site/index.html — the scrolly site is the default index.
 *
 * Run `python build_sjv_equity_atlas_html.py` first so data/clean/sjv_equity_atlas.html exists.
 * If that file is missing, the script no-ops so Vercel can still deploy committed vercel_site files.
 */
const fs = require("fs");
const path = require("path");

const root = path.join(__dirname, "..");
const src = path.join(root, "data", "clean", "sjv_equity_atlas.html");
const outDir = path.join(root, "vercel_site");
/** Python-only standalone page; main entry remains index.html (scrolly). */
const dest = path.join(outDir, "sjv_equity_atlas_py_build.html");

fs.mkdirSync(outDir, { recursive: true });

if (!fs.existsSync(src)) {
  console.warn(
    "copy-atlas: skip (no Python build at " +
      path.relative(root, src) +
      "). Deploying existing vercel_site/ as-is."
  );
  process.exit(0);
}

fs.copyFileSync(src, dest);
console.log("Wrote", path.relative(root, dest), "(does not replace index.html)");
