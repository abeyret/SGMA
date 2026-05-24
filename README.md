# Sinking Valley Explorer

Interactive site on SGMA groundwater regulation in the San Joaquin Valley — subsidence (InSAR), GSP-level water tables, fallowing, dry wells, and takeaways by impact (agriculture, residents, environment).

**Live site:** deployed via [Vercel](https://vercel.com) from this repo.

## Local preview

```powershell
.\open_sinking_valley_explorer.bat
```

Open http://localhost:8765/

## Rebuild data & HTML

Requires Python 3 with `geopandas`, `pandas`, `requests`.

```powershell
npm run build:explorer
```

Writes `vercel_site/index.html`, `vercel_site/sinking_valley_explorer_data.json`, and subsidence map tiles.

## Deploy

See [WEBSITE_DEPLOY.md](./WEBSITE_DEPLOY.md) for pushing to GitHub and Vercel.

Alexandra Beyret · ECON 30 · UC Berkeley
