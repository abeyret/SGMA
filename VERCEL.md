# Deploy `sjv_equity_atlas` on Vercel

This repo includes a tiny **static** deploy: `npm run build` copies `data/clean/sjv_equity_atlas.html` → `vercel_site/index.html`.

## Before you connect Vercel

1. Regenerate the atlas locally (optional if the file is already current):

   `python build_sjv_equity_atlas_html.py`

2. Run:

   `npm run build`

3. **Commit** at least one of:

   - `data/clean/sjv_equity_atlas.html` (so Vercel can run `npm run build` in the cloud), **or**
   - `vercel_site/index.html` after a local `npm run build` (if you do not want the source HTML in git).

   If neither is in the repo, the Vercel build will fail.

## Vercel Dashboard

1. Push this project to **GitHub** (or GitLab / Bitbucket).
2. In [Vercel](https://vercel.com): **Add New… → Project** → import the repo.
3. Use these settings (should be picked up from `vercel.json`):

   - **Framework Preset:** Other  
   - **Build Command:** `npm run build`  
   - **Output Directory:** `vercel_site`  
   - **Install Command:** `npm install` (default; no dependencies, fast)

4. Deploy. The site loads at `/` (the atlas).

## CLI (optional)

```bash
npm i -g vercel
cd /path/to/SGMA-ECON30
vercel
vercel --prod
```

## Notes

- The atlas uses CDN scripts (Leaflet, Chart.js); the site must be served over **HTTPS** (Vercel does this by default).
- After you change the atlas, run `python build_sjv_equity_atlas_html.py`, then `npm run build`, commit, and push (or trigger a redeploy).
