@echo off
rem Opens Pingwin's dashboard in your browser and signs you in. Pingwin must be running.
cd /d "%~dp0.."
if not exist ".dashboard-url" (
    echo The dashboard isn't ready yet. Start Pingwin with scripts\run_pingwin.cmd first.
    pause
    exit /b 1
)
set /p URL=<".dashboard-url"
start "" "%URL%"
