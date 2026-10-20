@echo off
rem Opens Pingwin's dashboard in your browser and signs you in.
rem Starts Pingwin in the background first if it isn't running.
powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0open_dashboard.ps1"
