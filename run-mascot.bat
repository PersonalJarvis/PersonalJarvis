@echo off
REM Testlauncher: startet Jarvis explizit mit SWG/Gigi-Maskottchen.
REM Ueberschreibt die jarvis.toml-Einstellung [ui].orb_style fuer diese Session.
set JARVIS_ORB_STYLE=mascot
call "%~dp0run.bat" %*
