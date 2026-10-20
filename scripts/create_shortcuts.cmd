@echo off
rem Puts a "Pingwin Dashboard" shortcut on your desktop. It opens the dashboard,
rem and starts Pingwin in the background first if it isn't running.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0create_shortcuts.ps1"
pause
