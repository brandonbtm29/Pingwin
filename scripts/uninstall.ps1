<#
    Removes Pingwin from this PC. Shows what it will do and asks first.

    - Stops Pingwin and removes it from Windows startup
    - Takes this PC off the PC list and stops syncing the settings folder
      (other PCs keep their settings)
    - Optionally deletes this PC's copy of the settings, SyncThing (if the installer set it up)
      and programs the installer installed
    - Deletes the Pingwin folder

    Usually started by double-clicking Uninstall-Pingwin.cmd in the Pingwin folder.
#>
param(
    [string]$InstallDir = "",
    [switch]$Yes   # accept the default answer to every question (for testing)
)

$ErrorActionPreference = "Continue"
$Startup = [Environment]::GetFolderPath("Startup")

function Say([string]$Text, [string]$Color = "Gray") { Write-Host $Text -ForegroundColor $Color }
function Ok([string]$Text) { Write-Host "  [ok] $Text" -ForegroundColor Green }
function Warn([string]$Text) { Write-Host "  [!]  $Text" -ForegroundColor Yellow }
function Ask([string]$Question, [bool]$Default) {
    if ($Yes) { return $Default }
    $hint = if ($Default) { "[Y/n]" } else { "[y/N]" }
    $a = (Read-Host "  $Question $hint").Trim()
    if (-not $a) { return $Default }
    return ($a -match "^(y|yes)$")
}
function Read-DotEnv([string]$Path) {
    $values = @{}
    if (Test-Path $Path) {
        foreach ($line in Get-Content $Path) {
            if ($line -match "^\s*([A-Z_]+)\s*=\s*(.*)$") { $values[$matches[1]] = $matches[2].Trim().Trim('"') }
        }
    }
    return $values
}
function Stop-Tree([int]$ProcessId) { & taskkill /T /F /PID $ProcessId 2>&1 | Out-Null }

try {
    # ------------------------------------------------------------ find the install
    if (-not $InstallDir) {
        $InstallDir = if ($PSScriptRoot -and (Test-Path (Join-Path $PSScriptRoot "..\pyproject.toml"))) { Join-Path $PSScriptRoot ".." } else { Join-Path $env:USERPROFILE "Pingwin" }
    }
    $InstallDir = [IO.Path]::GetFullPath($InstallDir.TrimEnd("\", "."))
    $pyproject = Join-Path $InstallDir "pyproject.toml"
    if (-not ((Test-Path $pyproject) -and (Select-String -Path $pyproject -Pattern '^name = "pingwin"' -Quiet))) {
        Say "Couldn't find Pingwin in $InstallDir. Nothing was changed." "Red"
        throw "PINGWIN_UNINSTALL_STOPPED"
    }
    $envFile = Read-DotEnv (Join-Path $InstallDir ".env")
    $settingsDir = if ($envFile["PINGWIN_DATA"]) { [IO.Path]::GetFullPath($envFile["PINGWIN_DATA"]) } else { $InstallDir }
    $pcName = if ($envFile["PINGWIN_NODE"]) { $envFile["PINGWIN_NODE"] } else { $env:COMPUTERNAME }
    $recordPath = Join-Path $InstallDir "install-record.json"
    $record = if (Test-Path $recordPath) { Get-Content $recordPath -Raw | ConvertFrom-Json } else { $null }
    $venvPy = Join-Path $InstallDir ".venv\Scripts\python.exe"
    $hasPython = Test-Path $venvPy
    $settingsShared = -not ($settingsDir -eq $InstallDir)
    if ($record -and $record.syncthing_home) { $env:PINGWIN_SYNCTHING_CONFIG = Join-Path $record.syncthing_home "config.xml" }

    $pcs = @()
    if ($hasPython -and $settingsShared) {
        $pcs = @(& $venvPy -c "from pingwin.__main__ import _load_env; _load_env(); from pingwin.config import load_config; print('\n'.join(load_config(require_token=False).failover.pcs))" 2>$null)
    }
    $isMain = ($pcs.Count -gt 0 -and $pcs[0] -eq $pcName)

    Say ""
    Say "  Uninstall Pingwin" "White"
    Say "  -----------------" "White"
    Say "  Pingwin folder:   $InstallDir"
    Say "  Settings folder:  $settingsDir"
    Say "  This PC's name:   $pcName"
    Say ""
    Say "  This will:"
    Say "   - stop Pingwin on this PC and remove it from Windows startup"
    if ($settingsShared) {
        Say "   - take $pcName off the PC list and stop syncing the settings folder here (other PCs keep theirs)"
    }
    Say "   - delete the Pingwin folder above (code and logs)"
    Say "  It asks separately before deleting settings or removing any other programs."
    if ($isMain) {
        Say ""
        Warn "$pcName is the main PC (first in the list). Backups will take over answering in Discord."
    }
    Say ""
    if (-not ($Yes -or (Ask "Uninstall Pingwin from this PC?" $false))) { Say "  Nothing was changed."; throw "PINGWIN_UNINSTALL_STOPPED" }

    # ------------------------------------------------------------ 1. stop it
    Say ""
    Say "[1/5] Stopping Pingwin" "Cyan"
    $all = Get-CimInstance Win32_Process
    # The window that restarts Pingwin first, so it can't start it again...
    $all | Where-Object { $_.Name -eq "cmd.exe" -and $_.CommandLine -like "*$InstallDir*run_pingwin.cmd*" } | ForEach-Object { Stop-Tree $_.ProcessId }
    # ...then Pingwin itself (its Python lives in the Pingwin folder).
    $all | Where-Object { $_.ExecutablePath -and $_.ExecutablePath -like "$InstallDir\*" } | ForEach-Object { Stop-Tree $_.ProcessId }
    Ok "Pingwin is stopped."

    # ------------------------------------------------------------ 2. startup
    Say "[2/5] Removing it from Windows startup" "Cyan"
    $removed = $false
    $launcher = Join-Path $Startup "Pingwin.cmd"
    if ((Test-Path $launcher) -and (Select-String -Path $launcher -SimpleMatch $InstallDir -Quiet)) {
        Remove-Item $launcher -Force; $removed = $true
    }
    # Shortcuts (startup, desktop, Start menu) that point into this Pingwin folder
    $wsh = New-Object -ComObject WScript.Shell
    $links = @((Join-Path $Startup "Pingwin.lnk"),
        (Join-Path ([Environment]::GetFolderPath("Desktop")) "Pingwin Dashboard.lnk"),
        (Join-Path ([Environment]::GetFolderPath("Programs")) "Pingwin Dashboard.lnk"))
    foreach ($l in $links) {
        if ((Test-Path $l) -and ($wsh.CreateShortcut($l).Arguments -like "*$InstallDir*")) { Remove-Item $l -Force; $removed = $true }
    }
    if ($removed) { Ok "Removed Pingwin from startup and removed its shortcuts." } else { Ok "It wasn't set to start with Windows." }

    # ------------------------------------------------------------ 3. PC list + SyncThing
    Say "[3/5] Shared settings" "Cyan"
    $unsynced = -not $settingsShared
    if ($settingsShared -and $hasPython) {
        & $venvPy -m pingwin.pairing check 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) {
            if ($pcs -contains $pcName) { & $venvPy -m pingwin.pairing leave --pc $pcName 2>&1 | ForEach-Object { Ok "$_" } }
            $out = & $venvPy -m pingwin.pairing unsync 2>&1
            if ($LASTEXITCODE -eq 0) { $out | ForEach-Object { Ok "$_" }; $unsynced = $true }
            else { $out | ForEach-Object { Warn "$_" } }
        } else {
            Warn "SyncThing isn't running, so the settings folder is still set to sync. Its files will be left alone."
        }
    } elseif ($settingsShared) {
        Warn "Pingwin's Python setup is missing, so SyncThing was left as it is."
    } else { Ok "Settings live in the Pingwin folder; nothing is shared." }

    # ------------------------------------------------------------ 4. optional extras
    Say "[4/5] Optional clean-up" "Cyan"
    if ($settingsShared -and (Test-Path $settingsDir)) {
        if (-not $unsynced) {
            Warn "Keeping $settingsDir, because deleting it while it still syncs would delete it on your other PCs too."
        } elseif (Ask "Delete this PC's copy of the settings (including the bot token) at $settingsDir?" (-not $isMain)) {
            Remove-Item $settingsDir -Recurse -Force
            Ok "Deleted the settings copy."
        } else { Ok "Kept the settings copy." }
    }
    if ($record -and $record.syncthing_own -and $hasPython) {
        $remaining = @()
        try { $remaining = @(& $venvPy -c "from pingwin.pairing import Syncthing; print('\n'.join(f['label'] or f['id'] for f in Syncthing.from_config().folders()))" 2>$null) } catch { }
        $remaining = @($remaining | Where-Object { $_ })
        if ($remaining.Count -eq 0 -and (Ask "SyncThing was set up by the Pingwin installer and syncs nothing else. Remove it too?" $true)) {
            Get-CimInstance Win32_Process -Filter "Name='syncthing.exe'" | Where-Object { $_.CommandLine -like "*$($record.syncthing_home)*" } | ForEach-Object { Stop-Tree $_.ProcessId }
            foreach ($f in @($record.startup)) { if ($f -and $f -like "*Syncthing*" -and (Test-Path $f)) { Remove-Item $f -Force } }
            if (@($record.installed) -contains "SyncThing") { & winget uninstall -e --id Syncthing.Syncthing --silent 2>&1 | Out-Null }
            if (Test-Path $record.syncthing_home) { Remove-Item $record.syncthing_home -Recurse -Force -ErrorAction SilentlyContinue }
            Ok "Removed SyncThing."
        } elseif ($remaining.Count -gt 0) {
            Ok ("Kept SyncThing; it still syncs: " + ($remaining -join ", "))
        }
    }
    $programs = @{ "Python" = "Python.Python.3.12"; "Git" = "Git.Git" }
    foreach ($name in @($record.installed)) {
        if ($programs.ContainsKey($name) -and (Ask "The installer installed $name. Uninstall it too? (Other programs may use it.)" $false)) {
            & winget uninstall -e --id $programs[$name] --silent 2>&1 | Out-Null
            Ok "Uninstalled $name."
        }
    }

    # ------------------------------------------------------------ 5. delete the folder
    Say "[5/5] Deleting the Pingwin folder" "Cyan"
    Set-Location $env:TEMP
    Remove-Item $InstallDir -Recurse -Force -ErrorAction SilentlyContinue
    if (Test-Path $InstallDir) { Start-Sleep -Seconds 3; Remove-Item $InstallDir -Recurse -Force -ErrorAction SilentlyContinue }
    if (Test-Path $InstallDir) { Warn "Some files are still in use. Restart the PC and delete $InstallDir yourself." }
    else { Ok "Deleted $InstallDir" }

    Say ""
    Say "  Pingwin is uninstalled from this PC." "White"
    if ($settingsShared -and -not $isMain) {
        Say "  If this PC won't be coming back, ask the main PC's owner to reset the bot token, since this PC had a copy."
    }
} catch {
    if ("$($_.Exception.Message)" -ne "PINGWIN_UNINSTALL_STOPPED") {
        Write-Host "Uninstall stopped because of an unexpected error: $($_.Exception.Message)" -ForegroundColor Red
    }
}
if (-not $Yes) { Read-Host "`n  Press Enter to close" | Out-Null }
