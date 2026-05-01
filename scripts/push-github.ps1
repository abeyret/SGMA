# One-time: log in to GitHub CLI, then create the remote repo and push.
# Run from repo root:  powershell -ExecutionPolicy Bypass -File scripts/push-github.ps1

$ErrorActionPreference = "Stop"
$gh = "C:\Program Files\GitHub CLI\gh.exe"
if (-not (Test-Path $gh)) { $gh = "gh" }

& $gh auth status 2>$null
if ($LASTEXITCODE -ne 0) {
  Write-Host "Log in to GitHub (browser will open or you'll get a device code)..."
  & $gh auth login -h github.com -p https -w
}

$repo = "SGMA-ECON30"
Write-Host "Creating https://github.com/<you>/$repo and pushing main..."
& $gh repo create $repo --public --description "SGMA San Joaquin Valley equity atlas" --source . --remote origin --push

if ($LASTEXITCODE -ne 0) {
  Write-Host "If the repo name is taken, edit `$repo in this script or run:"
  Write-Host "  gh repo create YOUR-NAME --public --source . --remote origin --push"
  exit $LASTEXITCODE
}

Write-Host "Done. Open the repo URL shown above, then connect it in Vercel."
