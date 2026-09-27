@echo off
REM Personal Jarvis — Voice-Pipeline-Launcher
REM Startet den Speech-Watchdog (Wake-Word + STT + Brain + TTS).
REM
REM Benutzung:
REM   voice          → Default: Console sichtbar, volle Logs in data/jarvis_watchdog.log
REM   voice --quiet  → im Hintergrund (pythonw), kein Console-Fenster
REM   voice --stop   → alle laufenden Voice-Watchdogs beenden

setlocal EnableDelayedExpansion
cd /d "%~dp0"
set PIDFILE=data\jarvis_watchdog.pid

if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
)

if "%1"=="--stop" goto :do_stop
if not exist "%PIDFILE%" goto :do_start
goto :check_running

:do_stop
REM Fast stop via PID file — no PowerShell, no process scan.
if not exist "%PIDFILE%" goto :stop_none
set /p WATCHDOG_PID=<"%PIDFILE%"
if "!WATCHDOG_PID!"=="" goto :stop_empty
tasklist /FI "PID eq !WATCHDOG_PID!" 2>NUL | find "!WATCHDOG_PID!" >NUL
if errorlevel 1 goto :stop_stale
REM PID is alive — verify it is really our watchdog (PID reuse guard).
python -c "import sys; from jarvis.speech.watchdog import recorded_watchdog_alive; sys.exit(0 if recorded_watchdog_alive() else 1)" >NUL 2>&1
if errorlevel 1 goto :stop_foreign
echo Stoppe PID !WATCHDOG_PID!
taskkill /PID !WATCHDOG_PID! /F
del "%PIDFILE%" 2>NUL
goto :end
:stop_none
echo Voice-Watchdog laeuft nicht (keine PID-Datei).
goto :end
:stop_empty
del "%PIDFILE%" 2>NUL
echo Voice-Watchdog laeuft nicht (leere PID-Datei aufgeraeumt).
goto :end
:stop_stale
echo Kein laufender Prozess zu PID !WATCHDOG_PID! — raeume stale PID-Datei auf.
del "%PIDFILE%" 2>NUL
goto :end
:stop_foreign
echo PID !WATCHDOG_PID! ist kein Voice-Watchdog — raeume stale PID-Datei auf (Prozess wird NICHT beendet).
del "%PIDFILE%" 2>NUL
goto :end

:check_running
REM Fast single-instance check: no PID file = instant start, straight to STT.
REM Only when a PID file exists do we spend one tasklist probe (~50ms) plus
REM one light python verify (~250ms) — still far below the old PowerShell
REM Get-CimInstance scan (~1s+). No permission hop anywhere on this path.
set /p WATCHDOG_PID=<"%PIDFILE%"
if "!WATCHDOG_PID!"=="" goto :stale_pid
tasklist /FI "PID eq !WATCHDOG_PID!" 2>NUL | find "!WATCHDOG_PID!" >NUL
if errorlevel 1 goto :stale_pid
python -c "import sys; from jarvis.speech.watchdog import recorded_watchdog_alive; sys.exit(0 if recorded_watchdog_alive() else 1)" >NUL 2>&1
if not errorlevel 1 goto :already_running
:stale_pid
del "%PIDFILE%" 2>NUL
goto :do_start
:already_running
echo Voice-Watchdog laeuft bereits (PID !WATCHDOG_PID!). Beende mit: voice --stop
goto :end

:do_start

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
