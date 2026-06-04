@echo off
REM Testlauncher: erzwingt den prozeduralen Orb, egal was in jarvis.toml steht.
set JARVIS_ORB_STYLE=orb
call "%~dp0run.bat" %*
