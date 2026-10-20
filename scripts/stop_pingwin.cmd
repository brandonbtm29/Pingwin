@echo off
rem Stops Pingwin on this PC (it runs in the background, with no window).
rem Backup PCs, if you have any, take over in about 2 minutes.
rem Start it again with the Pingwin Dashboard shortcut or scripts\start_hidden.vbs.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop_pingwin.ps1"
pause
