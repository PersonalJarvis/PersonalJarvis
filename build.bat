@echo off
REM Personal Jarvis — Production-Build (Phase 1a → 6)
REM Schritt 1: Frontend-Bundle via Vite → jarvis/ui/web/dist/
REM Schritt 2: PyInstaller → dist/Jarvis/Jarvis.exe (onedir-Bundle)

setlocal EnableDelayedExpansion
cd /d "%~dp0"

if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
)

echo [build] 1/2 Frontend
pushd jarvis\ui\web\frontend
if not exist node_modules (
    echo [build] npm ci...
    call npm ci || goto :error
)
call npm run build || goto :error
popd

echo [build] 2/2 PyInstaller
if not exist jarvis.spec (
    echo [build] jarvis.spec fehlt — Abbruch.
    exit /b 1
)
pyinstaller jarvis.spec --noconfirm --clean || goto :error

echo [build] Fertig. Artefakt: dist\Jarvis\Jarvis.exe
goto :end

:error
echo [build] FEHLGESCHLAGEN.
exit /b 1

:end
endlocal
