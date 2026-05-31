@echo off
REM Personal Jarvis Phase 9 Overlay - Ein-Klick Smoke Test
REM
REM Was es macht:
REM   1. prueft ob das overlay-Package installiert ist (pip install -e . wenn nicht)
REM   2. prueft ob das Frontend gebaut ist (npm run build wenn nicht)
REM   3. startet das Overlay mit --smoke (zeigt Mascot 5s lang, beendet sich)
REM
REM Erwartung: Mascot oben-links auf Primary-Monitor erscheint, verschwindet nach 5s.

setlocal enabledelayedexpansion
cd /d "%~dp0"

echo === Personal Jarvis Phase 9 Overlay - Smoke Test ===
echo.

REM ---- Check 1: Overlay-Package importable? ----
echo [1/3] Pruefe ob 'overlay'-Package importable...
python -c "import overlay" 2>nul
if errorlevel 1 (
    echo     -^> nicht installiert. Installiere via 'pip install -e .' ...
    pip install -e . --quiet
    if errorlevel 1 (
        echo FEHLER: pip install fehlgeschlagen.
        echo Manuell pruefen: cd "%~dp0" ^& pip install -e .
        exit /b 1
    )
    echo     -^> OK
) else (
    echo     -^> bereits installiert
)
echo.

REM ---- Check 2: Frontend dist gebaut? ----
echo [2/3] Pruefe ob overlay-ui/dist/edge-glow.html existiert...
if not exist "overlay-ui\dist\edge-glow.html" (
    echo     -^> nicht gebaut. Baue via 'npm install' + 'npm run build' ...
    pushd overlay-ui
    if not exist "node_modules" (
        echo         npm install ...
        call npm install --silent
        if errorlevel 1 (
            echo FEHLER: npm install fehlgeschlagen. Pruefe Node.js installation.
            popd
            exit /b 1
        )
    )
    echo         npm run build ...
    call npm run build
    if errorlevel 1 (
        echo FEHLER: npm run build fehlgeschlagen.
        popd
        exit /b 1
    )
    popd
    echo     -^> OK
) else (
    echo     -^> bereits gebaut
)
echo.

REM ---- Check 3: Overlay starten ----
echo [3/3] Starte Overlay im Smoke-Mode (5 Sekunden)...
echo.
echo Was du sehen solltest:
echo   - Mascot oben links auf Primary-Monitor (schwarzes abgerundetes Quadrat
echo     mit zwei gelben Augen + Mund-Bogen)
echo   - KEIN Edge-Glow (das kommt erst wenn Hauptjarvis Actions ausloest)
echo   - Nach 5 Sekunden schliesst sich alles automatisch
echo.
python -m overlay --smoke
if errorlevel 1 (
    echo.
    echo FEHLER: Overlay-Smoke-Test ist gecrasht.
    echo Logs oben pruefen.
    exit /b 1
)

echo.
echo === Smoke-Test bestanden ===
echo Naechster Schritt: 'run.bat --debug' im Repo-Root fuer Production-Test.
endlocal
