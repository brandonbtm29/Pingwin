@echo off
rem Makes Pingwin start automatically, in the background, when you sign in to this PC
rem (no admin needed). Also adds a "Pingwin Dashboard" shortcut to your desktop.
rem Undo with scripts\remove_autostart.cmd.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0create_shortcuts.ps1" -Startup || goto :error
echo.
echo Done. Pingwin will start in the background each time you sign in to %COMPUTERNAME%.
echo Use the Pingwin Dashboard shortcut to open it, and Status ^> Stop Pingwin to stop it.
pause
exit /b 0
:error
echo Couldn't add Pingwin to your Startup folder.
pause
exit /b 1
