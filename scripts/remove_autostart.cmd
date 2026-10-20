@echo off
rem Stops Pingwin from starting automatically when you sign in.
set STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
set FOUND=
if exist "%STARTUP%\Pingwin.lnk" del "%STARTUP%\Pingwin.lnk" & set FOUND=1
if exist "%STARTUP%\Pingwin.cmd" del "%STARTUP%\Pingwin.cmd" & set FOUND=1
if defined FOUND (
    echo Done. Pingwin won't start automatically anymore.
) else (
    echo Pingwin wasn't set to start automatically.
)
pause
