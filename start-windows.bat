@echo off
REM Pocket Bridge - dubbelklik om te starten (Windows).
cd /d "%~dp0"
set "PATH=%USERPROFILE%\.local\bin;%PATH%"
where uv >nul 2>nul
if errorlevel 1 (
  echo Eenmalige installatie van uv ^(regelt Python voor je^)...
  powershell -ExecutionPolicy ByPass -NoProfile -Command "irm https://astral.sh/uv/install.ps1 | iex"
)
echo Pocket Bridge voorbereiden... ^(de eerste keer duurt dit ongeveer een minuut^)
uv sync --quiet --python 3.12
if errorlevel 1 (
  echo Installatie mislukt / install failed.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m pocket_bridge web
pause
