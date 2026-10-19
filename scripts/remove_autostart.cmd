@echo off
rem Stops Pingwin from starting automatically when you sign in.
set LAUNCHER=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Pingwin.cmd
if not exist "%LAUNCHER%" (
    echo Pingwin wasn't set to start automatically.
    pause
    exit /b 0
)
del "%LAUNCHER%"
echo Done. Pingwin won't start automatically anymore.
pause
