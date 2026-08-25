@echo off
REM Personal Jarvis - Windows build.
REM
REM Thin wrapper around packaging\windows\build.ps1, which is the one script
REM that builds the frontend, freezes the app with PyInstaller and wraps the
REM result in the Inno Setup wizard. Keeping the logic in one place is what
REM stops this file and the release workflow from drifting apart.
REM
REM Result: dist\installers\PersonalJarvis-Setup-x64.exe
REM         (the frozen bundle it is built from stays in dist\Jarvis)
REM
REM Any build.ps1 switch is passed straight through, for example:
REM   build.bat -SkipPyInstaller

setlocal
cd /d "%~dp0"

set "SCRIPT=%~dp0packaging\windows\build.ps1"
if not exist "%SCRIPT%" (
    echo [build] packaging\windows\build.ps1 is missing - aborting.
    exit /b 1
)

REM pwsh (PowerShell 7) when available, Windows PowerShell otherwise.
where pwsh >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    pwsh -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" %*
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" %*
)

if %ERRORLEVEL% NEQ 0 (
    echo [build] FAILED.
    exit /b 1
)

endlocal
