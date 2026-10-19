@echo off
rem Makes Pingwin start automatically when you sign in to this PC,
rem by adding a small launcher to your Windows Startup folder (no admin needed).
rem Undo with scripts\remove_autostart.cmd.
setlocal
set STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
set SCRIPT=%~dp0run_pingwin.cmd
(
    echo @echo off
    echo rem Starts Pingwin ^(added by scripts\install_autostart.cmd^)
    echo start "Pingwin" /min "%SCRIPT%"
) > "%STARTUP%\Pingwin.cmd" || goto :error
echo Done. Pingwin will start in a minimized window each time you sign in to %COMPUTERNAME%.
pause
exit /b 0
:error
echo Couldn't write to your Startup folder: %STARTUP%
pause
exit /b 1
