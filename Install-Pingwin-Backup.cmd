@echo off
rem Sets up this PC as a Pingwin backup. Double-click it, paste the invite code you were sent,
rem and follow the prompts. It checks what's already installed and asks before installing anything.
powershell -NoProfile -ExecutionPolicy Bypass -Command "& ([scriptblock]::Create((Invoke-RestMethod 'https://raw.githubusercontent.com/brandonbtm29/Pingwin/main/scripts/install-backup.ps1')))"
echo.
pause
