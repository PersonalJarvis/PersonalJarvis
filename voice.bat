@echo off
REM Personal Jarvis — Voice-Pipeline-Launcher
REM Startet den Speech-Watchdog (Wake-Word + STT + Brain + TTS).
REM
REM Benutzung:
REM   voice          → Default: Console sichtbar, volle Logs in data/jarvis_watchdog.log
REM   voice --quiet  → im Hintergrund (pythonw), kein Console-Fenster
REM   voice --stop   → alle laufenden Voice-Watchdogs beenden

setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
)

if "%1"=="--stop" (
    REM Beende alle python-Prozesse die jarvis.speech.watchdog ausfuehren.
    powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name LIKE 'python%%'\" | Where-Object { $_.CommandLine -match 'jarvis.speech.watchdog' } | ForEach-Object { Write-Host ('Stoppe PID ' + $_.ProcessId); Stop-Process -Id $_.ProcessId -Force }"
    goto :end
)

REM Check: ist bereits einer am Laufen?
powershell -NoProfile -Command "$p = Get-CimInstance Win32_Process -Filter \"Name LIKE 'python%%'\" | Where-Object { $_.CommandLine -match 'jarvis.speech.watchdog' }; if ($p) { Write-Host ('Voice-Watchdog laeuft bereits (PID ' + $p.ProcessId + '). Beende mit: voice --stop'); exit 1 } else { exit 0 }"
if errorlevel 1 goto :end

if "%1"=="--quiet" (
    REM Headless: pythonw laesst keine Console, Logs gehen in data/jarvis_watchdog.log
    start "" pythonw -m jarvis.speech.watchdog
    echo Voice-Watchdog gestartet im Hintergrund.
    echo Logs: data\jarvis_watchdog.log
) else (
    REM Default: Console bleibt offen — du siehst Live-Logs.
    echo ================================================================
    echo   Voice-Watchdog startet. Beenden mit Strg+C oder 'voice --stop'.
    echo   Logs laufen ins Terminal + data\jarvis_watchdog.log
    echo ================================================================
    python -m jarvis.speech.watchdog
)

:end
endlocal
