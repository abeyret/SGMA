@echo off
cd /d "%~dp0vercel_site"
set URL=http://localhost:8765/
echo Starting local server for Sinking Valley...
echo.
echo   %URL%
echo   http://localhost:8765/sinking_valley_explorer.html
echo   http://localhost:8765/sinking_valley.html
echo.
echo Press Ctrl+C to stop the server.

powershell -NoProfile -Command ^
  "$p = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique; foreach ($pid in $p) { if ($pid) { Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue } }"

start "" cmd /c "timeout /t 2 /nobreak >nul && start %URL%"
python -m http.server 8765
