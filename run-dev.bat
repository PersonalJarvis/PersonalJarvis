@echo off
REM Personal Jarvis — DEV INSTANCE launcher (Windows).
REM
REM Starts a SECOND desktop app beside the regular one, from this same checkout:
REM "Personal Jarvis Dev" — own data dir (data-dev\), own ports (+100), DEV-badged
REM icon, no wake word / global hotkeys / chat channels / autostart (those stay
REM with the regular app). Restart it as often as you like; the regular app and
REM the coding sessions inside it are never touched.
REM
REM Not the same as dev.bat / run.bat --dev (those load the frontend from a Vite
REM HMR server). This one runs the built frontend exactly like run.bat does.
REM Same as: set JARVIS_INSTANCE=dev && run.bat

setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
)

if exist "scripts\check-working-tree.ps1" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\check-working-tree.ps1"
)

if "%1"=="--debug" (
    set JARVIS_DEBUG=1
    python -m jarvis.ui.web.launcher --instance dev
) else if "%1"=="--headless" (
    python -m jarvis.ui.web.launcher --instance dev --headless
) else (
    start "" pythonw -m jarvis.ui.web.launcher --instance dev
)

endlocal
