@echo off
rem Removes Pingwin from this PC. It shows what it will do and asks before deleting anything.
copy /y "%~dp0scripts\uninstall.ps1" "%TEMP%\pingwin-uninstall.ps1" >nul && powershell -NoProfile -ExecutionPolicy Bypass -File "%TEMP%\pingwin-uninstall.ps1" -InstallDir "%~dp0." & exit /b
