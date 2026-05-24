@echo off
REM Rebuild figures + open ECON 30 thesis slideshow (18 slides).
cd /d "%~dp0"
python build_econ30_slideshow.py
start "" "%~dp0vercel_site\thesis_presentation.html"
