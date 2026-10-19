@echo off
rem One-time setup on a new PC: creates .venv, installs Pingwin, and writes this PC's .env.
setlocal
cd /d "%~dp0.."
where git >nul 2>nul || echo Note: git isn't installed, so this PC won't auto-update. Get it from https://git-scm.com
if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    py -3.12 -m venv .venv 2>nul || py -3 -m venv .venv 2>nul || python -m venv .venv || goto :error
)
".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet --disable-pip-version-check
".venv\Scripts\python.exe" -m pip install -e ".[dev]" --quiet --disable-pip-version-check || goto :error

if exist ".env" (
    echo .env already exists; leaving it alone.
    goto :done
)
echo.
echo Where is the shared Pingwin settings folder ^(the SyncThing one^)?
echo Example: C:\Users\%USERNAME%\SyncThing\Pingwin
set /p DATA=Folder path (leave blank to keep settings in this project folder): 
echo.
echo What should this PC be called? Use the same name as in the pcs list in config.toml.
set /p NODE=PC name [%COMPUTERNAME%]: 
if "%NODE%"=="" set NODE=%COMPUTERNAME%
(
    echo # This PC's own Pingwin settings. Not synced, not committed.
    echo PINGWIN_DATA=%DATA%
    echo PINGWIN_NODE=%NODE%
) > ".env"
echo Wrote .env for %NODE%.
echo If you run Pingwin on more than one PC, make sure "%NODE%" is in the pcs list in config.toml.
set TARGET=%DATA%
if "%TARGET%"=="" set TARGET=%CD%
if not exist "%TARGET%\config.toml" copy "config.example.toml" "%TARGET%\config.toml" >nul && echo Created %TARGET%\config.toml
if not exist "%TARGET%\secrets.env" copy "secrets.env.example" "%TARGET%\secrets.env" >nul && echo Created %TARGET%\secrets.env ^(paste the bot token into it^)

:done
echo.
echo Setup done. Start Pingwin with scripts\run_pingwin.cmd
pause
exit /b 0
:error
echo Setup failed. Scroll up for the error.
pause
exit /b 1
