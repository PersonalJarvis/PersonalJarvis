@echo off
REM Personal Jarvis — Windows Launcher
REM Startet Jarvis im User-Context (kein Admin nötig)

setlocal
cd /d "%~dp0"

REM Aktiviere venv wenn vorhanden
if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
)

REM Pre-Boot-Drift-Check: restore any tracked files that vanished from
REM the working tree (see scripts/check-working-tree.ps1 docblock for the
REM 2026-05-14 incident this guards against). Always exits 0; never blocks
REM boot. Skipped silently when PowerShell is unavailable.
if exist "scripts\check-working-tree.ps1" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\check-working-tree.ps1"
)

REM Starte Jarvis Desktop-App (Phase 1a: FastAPI + pywebview + React).
REM --debug → Console sichtbar, --headless → nur Backend ohne Fenster,
REM --dev → Frontend aus Vite-Dev-Server (Port 5173) statt dist/.
if "%1"=="--debug" (
    REM --debug ist batch-lokal: Console sichtbar + Verbose-Logging.
    REM Weiterreichen an den Launcher wuerde argparse-Fehler ausloesen.
    set JARVIS_DEBUG=1
    python -m jarvis.ui.web.launcher
) else if "%1"=="--headless" (
    python -m jarvis.ui.web.launcher --headless
) else if "%1"=="--dev" (
    set JARVIS_DEV=1
    python -m jarvis.ui.web.launcher --dev
) else (
    start "" pythonw -m jarvis.ui.web.launcher
)

endlocal
