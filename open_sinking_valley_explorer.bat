@echo off
cd /d "%~dp0vercel_site"
set URL=http://localhost:8765/
echo Sinking Valley Explorer
echo.
echo   %URL%
echo   http://localhost:8765/sinking_valley_explorer.html
echo.

REM Healthy = HTTP 200 with a non-empty body (port open but hung returns empty / closes)
powershell -NoProfile -Command ^
  "try { $r = Invoke-WebRequest -Uri 'http://localhost:8765/' -UseBasicParsing -TimeoutSec 3; if ($r.StatusCode -eq 200 -and $r.Content.Length -gt 100) { exit 0 } else { exit 1 } } catch { exit 1 }"
if %errorlevel%==0 (
  echo Server already running on port 8765 — opening explorer...
  start "" "%URL%"
  exit /b 0
)

REM Kill stale listener on 8765 (hung python http.server)
echo Clearing stale process on port 8765...
powershell -NoProfile -Command ^
  "$p = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique; foreach ($pid in $p) { if ($pid) { Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue } }"

echo Starting local server on port 8765...
echo Press Ctrl+C to stop the server.
start "" cmd /c "timeout /t 2 /nobreak >nul && start %URL%"
python -m http.server 8765
