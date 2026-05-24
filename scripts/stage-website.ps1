# Stage only Sinking Valley Explorer website files for push to github.com/abeyret/SGMA
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

Write-Host "Staging website files from $root ..."

$paths = @(
  "vercel.json",
  "package.json",
  "README.md",
  ".gitignore",
  "WEBSITE_DEPLOY.md",
  "build_sinking_valley_explorer.py",
  "build_sjv_subsidence.py",
  "open_sinking_valley_explorer.bat",
  "open_sinking_valley.bat",
  "sjv_gsp_fallow_by_year.csv",
  "data/raw/boundaries",
  "data/processed/csv/gsp_determination_status.csv",
  "data/processed/csv/subsidence_by_gsp_year.csv",
  "data/interim/dry_wells",
  "vercel_site/index.html",
  "vercel_site/sinking_valley_explorer.html",
  "vercel_site/sinking_valley_explorer.css",
  "vercel_site/sinking_valley_explorer.js",
  "vercel_site/sinking_valley_explorer_panels.js",
  "vercel_site/sinking_valley_explorer_data.json",
  "vercel_site/thesis_counties.geojson",
  "vercel_site/subsidence",
  "assets/intro_slide1.png",
  "assets/intro_slide2.png",
  "assets/intro_slide3.png",
  "assets/intro_slide4.png",
  "assets/subsidence_poland_comparison.png",
  "assets/quote_canal.png",
  "assets/quote_farmland_aerial.png",
  "assets/quote_farmworkers.png",
  "assets/quote_solar_fallow.png",
  "assets/ppt"
)

# vercel_site assets used by explorer intro
$vsAssets = @(
  "vercel_site/assets/intro_slide1.png",
  "vercel_site/assets/intro_slide2.png",
  "vercel_site/assets/intro_slide3.png",
  "vercel_site/assets/intro_slide4.png",
  "vercel_site/assets/subsidence_poland_comparison.png",
  "vercel_site/assets/quote_canal.png",
  "vercel_site/assets/quote_farmland_aerial.png",
  "vercel_site/assets/quote_farmworkers.png",
  "vercel_site/assets/quote_solar_fallow.png",
  "vercel_site/assets/ppt"
)
$paths += $vsAssets

foreach ($p in $paths) {
  $full = Join-Path $root $p
  if (Test-Path $full) {
    git add $p
    Write-Host "  + $p"
  } else {
    Write-Host "  skip (missing): $p" -ForegroundColor Yellow
  }
}

Write-Host ""
Write-Host "Done. Review: git status"
Write-Host "Then: git commit -m `"Publish Sinking Valley Explorer`""
Write-Host "      git push -u sgma main"
