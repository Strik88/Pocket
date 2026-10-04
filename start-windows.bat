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
start "" ".venv\Scripts\pythonw.exe" -m pocket_bridge tray
echo Pocket Bridge draait. Je vindt het icoon rechtsonder in het systeemvak.
echo Pocket Bridge is running. Look for the icon in the system tray.
timeout /t 6 >nul
