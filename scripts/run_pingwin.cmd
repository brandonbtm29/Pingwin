@echo off
rem Runs Pingwin. Before each start it grabs updates from GitHub, and it
rem restarts Pingwin if it crashes. Close the window to stop it.
title Pingwin
setlocal
cd /d "%~dp0.."
rem UTF-8 so emoji in role names and logs display correctly
chcp 65001 >nul
set PYTHONUTF8=1

:loop
call :update
".venv\Scripts\python.exe" -m pingwin
set CODE=%ERRORLEVEL%
if "%CODE%"=="2" goto settings_problem
if "%CODE%"=="3" (
    echo Restarting to install an update...
    timeout /t 2 /nobreak >nul
    goto loop
)
echo Pingwin stopped ^(exit code %CODE%^). Restarting in 15 seconds...
timeout /t 15 /nobreak >nul
goto loop

:settings_problem
echo.
echo Pingwin stopped because of a settings problem ^(see the message above^).
echo Fix .env, config.toml or secrets.env, then run this again.
pause
exit /b 2

:update
if not exist ".git" exit /b 0
where git >nul 2>nul || exit /b 0
set BEFORE=
set AFTER=
for /f %%h in ('git rev-parse HEAD 2^>nul') do set BEFORE=%%h
git pull --ff-only --quiet 2>nul || echo Couldn't fetch updates from GitHub; starting the current version.
for /f %%h in ('git rev-parse HEAD 2^>nul') do set AFTER=%%h
if not "%BEFORE%"=="%AFTER%" (
    echo Updated to %AFTER:~0,7%. Installing...
    ".venv\Scripts\python.exe" -m pip install -e . --quiet --disable-pip-version-check
)
exit /b 0
