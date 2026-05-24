# Deploy Sinking Valley Explorer to Vercel (via GitHub)

Repo: **https://github.com/abeyret/SGMA**

Vercel serves the pre-built site from `vercel_site/`. Rebuild locally when data changes, then push.

---

## What goes in the SGMA repo (website only)

```
SGMA/
├── vercel.json
├── package.json          # optional; local rebuild scripts
├── README.md
├── .gitignore
├── build_sinking_valley_explorer.py   # local rebuild
├── build_sjv_subsidence.py
├── open_sinking_valley_explorer.bat
├── assets/               # intro images (source for build)
├── sjv_gsp_fallow_by_year.csv
├── data/
│   ├── raw/boundaries/   # GSP geojson
│   ├── processed/csv/    # status, subsidence cache (NOT 4.8GB InSAR zip)
│   └── interim/dry_wells/
├── outputs/subsidence/   # optional if not copying PNGs into vercel_site/subsidence
└── vercel_site/          # ★ what Vercel publishes (~100 MB)
    ├── index.html
    ├── sinking_valley_explorer.*
    ├── sinking_valley_explorer_data.json
    ├── thesis_counties.geojson
    ├── assets/
    └── subsidence/
```

**Do not commit:** PowerPoints, `sgma_research/`, `householdwatersupply*.csv`, `data/raw/subsidence/*.zip` (4.8 GB), old scrolly atlas, thesis/briefing HTML unless you want them live.

---

## One-time setup

### 1. Rebuild the site locally

```powershell
cd "c:\Users\alexa\OneDrive\Desktop\SGMA-ECON30"
npm run build:explorer
```

### 2. Create the GitHub repo (if empty)

On GitHub: **New repository** → `SGMA` → public → no README (or merge later).

### 3. Connect this folder to GitHub

```powershell
cd "c:\Users\alexa\OneDrive\Desktop\SGMA-ECON30"

git remote add sgma https://github.com/abeyret/SGMA.git
# if remote exists: git remote set-url sgma https://github.com/abeyret/SGMA.git
```

### 4. Stage **website files only**

```powershell
git add vercel.json package.json README.md .gitignore WEBSITE_DEPLOY.md
git add build_sinking_valley_explorer.py build_sjv_subsidence.py
git add open_sinking_valley_explorer.bat open_sinking_valley.bat
git add assets/ sjv_gsp_fallow_by_year.csv
git add data/raw/boundaries/ data/processed/csv/gsp_determination_status.csv
git add data/processed/csv/subsidence_by_gsp_year.csv
git add data/interim/dry_wells/
git add vercel_site/index.html vercel_site/sinking_valley_explorer.*
git add vercel_site/sinking_valley_explorer_data.json vercel_site/thesis_counties.geojson
git add vercel_site/assets/intro_slide*.png vercel_site/assets/ppt/ vercel_site/assets/quote_*.png
git add vercel_site/assets/subsidence_poland_comparison.png
git add vercel_site/subsidence/
```

Or run: `powershell -File scripts/stage-website.ps1`

### 5. Commit and push

```powershell
git commit -m "Publish Sinking Valley Explorer for Vercel"
git push -u sgma main
```

If GitHub repo already has commits, you may need `git pull sgma main --rebase` first, or force-push only if you intend to replace everything.

---

## Connect Vercel

1. Go to [vercel.com](https://vercel.com) → **Add New Project**
2. **Import** `abeyret/SGMA` from GitHub (authorize GitHub if prompted)
3. Settings:
   - **Framework Preset:** Other
   - **Root Directory:** `.` (repo root)
   - **Build Command:** leave **empty** (site is pre-built in `vercel_site/`)
   - **Output Directory:** `vercel_site`
   - **Install Command:** leave empty
4. **Deploy**

Your site will be at `https://sgma-*.vercel.app` (or a custom domain).

---

## Update after changes

```powershell
npm run build:explorer
git add vercel_site/
git commit -m "Rebuild explorer data"
git push sgma main
```

Vercel redeploys automatically on every push to `main`.

---

## Local preview

```powershell
.\open_sinking_valley_explorer.bat
# → http://localhost:8765/
```
