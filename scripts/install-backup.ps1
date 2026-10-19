<#
    Pingwin backup-PC installer.

    Checks what's already installed, asks before installing anything missing (Python, Git,
    SyncThing), downloads Pingwin, connects SyncThing to the main PC with an invite code,
    and sets Pingwin to start with Windows. At the end it shows a reply code to send back.

    Easiest way to run it (paste into PowerShell or Windows Terminal):
        & ([scriptblock]::Create((irm https://raw.githubusercontent.com/brandonbtm29/Pingwin/main/scripts/install-backup.ps1)))
    Or double-click Install-Pingwin-Backup.cmd.
#>
param(
    [string]$Invite = "",
    [string]$PcName = "",
    [string]$InstallDir = (Join-Path $env:USERPROFILE "Pingwin"),
    [string]$SettingsDir = (Join-Path $env:USERPROFILE "SyncThing\Pingwin"),
    [string]$SyncthingHome = (Join-Path $env:LOCALAPPDATA "Syncthing"),
    [string]$Repo = "https://github.com/brandonbtm29/Pingwin.git",
    [switch]$Yes,          # install missing tools without asking
    [switch]$NoAutostart,  # don't add anything to Windows startup (for testing)
    [switch]$NoStart       # don't start Pingwin at the end (for testing)
)

# Native tools write progress to stderr; check exit codes instead of letting that stop the script.
$ErrorActionPreference = "Continue"
$CustomSyncthingHome = $PSBoundParameters.ContainsKey("SyncthingHome")
$ProgressPreference = "SilentlyContinue"
$Startup = [Environment]::GetFolderPath("Startup")

function Say([string]$Text, [string]$Color = "Gray") { Write-Host $Text -ForegroundColor $Color }
function Ok([string]$Text) { Write-Host "  [ok] $Text" -ForegroundColor Green }
function Todo([string]$Text) { Write-Host "  [--] $Text" -ForegroundColor Yellow }
function Step([int]$N, [string]$Text) { Write-Host ""; Write-Host "[$N/6] $Text" -ForegroundColor Cyan }
function Stop-Setup([string]$Text) {
    Write-Host ""
    Write-Host "Setup stopped: $Text" -ForegroundColor Red
    Write-Host "Fix that and run the installer again. It picks up where it left off." -ForegroundColor Red
    throw "PINGWIN_SETUP_STOPPED"
}
function Ask-YesNo([string]$Question) {
    if ($Yes) { return $true }
    $a = Read-Host "$Question [Y/n]"
    return ($a -eq "" -or $a -match "^(y|yes)$")
}
function Refresh-Path {
    $m = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $u = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$m;$u;" + (Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Links")
}
function Write-Text([string]$Path, [string]$Text) {
    [IO.File]::WriteAllText($Path, $Text, (New-Object Text.UTF8Encoding $false))
}

# ---------------------------------------------------------------- detection
function Find-Python {
    foreach ($v in "3.13", "3.12", "3.11") {
        try {
            $p = & py "-$v" -c "import sys; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $p) { return "$p".Trim() }
        } catch { }
    }
    foreach ($name in "python", "python3") {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd -and $cmd.Source -notmatch "WindowsApps") {
            try {
                $p = & $cmd.Source -c "import sys; print(sys.executable if sys.version_info >= (3, 11) else '')" 2>$null
                if ($p -and "$p".Trim()) { return "$p".Trim() }
            } catch { }
        }
    }
    foreach ($v in "313", "312", "311") {
        $p = Join-Path $env:LOCALAPPDATA "Programs\Python\Python$v\python.exe"
        if (Test-Path $p) { return $p }
    }
    return $null
}

function Find-Git {
    $cmd = Get-Command git -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($p in "$env:ProgramFiles\Git\cmd\git.exe", "$env:LOCALAPPDATA\Programs\Git\cmd\git.exe") {
        if (Test-Path $p) { return $p }
    }
    return $null
}

function Find-Syncthing {
    $cmd = Get-Command syncthing -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $candidates = @(
        "$env:APPDATA\SyncTrayzor\syncthing.exe",
        "$env:ProgramFiles\SyncTrayzor\syncthing.exe",
        "$env:LOCALAPPDATA\Programs\Syncthing\syncthing.exe"
    )
    $candidates += @(Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Directory -Filter "Syncthing.Syncthing*" -ErrorAction SilentlyContinue |
        ForEach-Object { Get-ChildItem $_.FullName -Recurse -Filter syncthing.exe -ErrorAction SilentlyContinue } |
        ForEach-Object { $_.FullName })
    foreach ($c in $candidates) { if ($c -and (Test-Path $c)) { return $c } }
    $proc = Get-Process syncthing -ErrorAction SilentlyContinue | Where-Object { $_.Path } | Select-Object -First 1
    if ($proc) { return $proc.Path }
    return $null
}

function Find-SyncthingConfig {
    $candidates = @(Join-Path $SyncthingHome "config.xml")
    if (-not $CustomSyncthingHome) { $candidates += "$env:APPDATA\Syncthing\config.xml" }
    foreach ($p in $candidates) { if (Test-Path $p) { return $p } }
    return $null
}

function Find-SyncTrayzor {
    foreach ($p in "$env:ProgramFiles\SyncTrayzor\SyncTrayzor.exe", "${env:ProgramFiles(x86)}\SyncTrayzor\SyncTrayzor.exe") {
        if ($p -and (Test-Path $p)) { return $p }
    }
    return $null
}

function Install-WithWinget([string]$Id, [string]$Name, [string[]]$Extra = @()) {
    Say "  Installing $Name..."
    $wingetArgs = @("install", "-e", "--id", $Id, "--silent", "--accept-package-agreements", "--accept-source-agreements") + $Extra
    & winget @wingetArgs | Out-Host
    if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne -1978335189) {  # -1978335189 = already installed
        Stop-Setup "winget couldn't install $Name (code $LASTEXITCODE)."
    }
}

# ---------------------------------------------------------------- main
try {
    Say ""
    Say "  Pingwin backup PC setup" "White"
    Say "  ------------------------" "White"
    Say "  This sets up this PC to keep Pingwin running when the main PC is offline."
    Say "  It only installs things after asking, and skips anything already here."

    # Codes and names first, so nothing is changed before we know they're right.
    while (-not ($Invite -match "PINGWIN-INVITE-")) {
        if ($Invite) { Say "  That doesn't look like an invite code (it starts with PINGWIN-INVITE-)." "Yellow" }
        $Invite = (Read-Host "`n  Paste the invite code you were sent").Trim()
    }
    if (-not $PcName) {
        $entered = (Read-Host "  Name for this PC [$env:COMPUTERNAME]").Trim()
        $PcName = if ($entered) { $entered } else { $env:COMPUTERNAME }
    }
    if ($PcName -notmatch "^[A-Za-z0-9 ._-]{1,40}$") { Stop-Setup "The PC name can only use letters, numbers, spaces, dots, dashes and underscores." }

    # ------------------------------------------------------------ 1. check
    Step 1 "Checking what's already installed"
    Refresh-Path
    $python = Find-Python
    $git = Find-Git
    $syncthing = Find-Syncthing
    $stConfig = Find-SyncthingConfig
    $missing = @()

    if ($python) { Ok ("Python: " + (& $python --version 2>&1)) } else { Todo "Python 3.11 or newer: not found"; $missing += "Python" }
    if ($git) { Ok ("Git: " + ((& $git --version) -replace "git version ", "")) } else { Todo "Git: not found"; $missing += "Git" }
    if ($syncthing) {
        $running = [bool](Get-Process syncthing -ErrorAction SilentlyContinue)
        Ok ("SyncThing: found" + $(if ($running) { ", running" } else { ", not running" }) + $(if ($stConfig) { ", already set up" } else { "" }))
    } else { Todo "SyncThing: not found"; $missing += "SyncThing" }
    if (Test-Path (Join-Path $InstallDir ".git")) { Ok "Pingwin: already downloaded to $InstallDir (will update it)" }
    else { Todo "Pingwin: will download to $InstallDir" }

    # ------------------------------------------------------------ 2. install
    Step 2 "Installing what's missing"
    if (-not $missing) {
        Ok "Nothing to install."
    } else {
        if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
            Stop-Setup ("These need installing: " + ($missing -join ", ") + ". This PC doesn't have winget; install 'App Installer' from the Microsoft Store, or install them yourself from python.org, git-scm.com and syncthing.net.")
        }
        Say ("  Missing: " + ($missing -join ", "))
        if ($missing -contains "Git") { Say "  Windows may ask for permission while Git installs. Click Yes." "Yellow" }
        if (-not (Ask-YesNo "  Install them now with winget?")) { Stop-Setup "Install cancelled." }
        if ($missing -contains "Python") { Install-WithWinget "Python.Python.3.12" "Python 3.12" @("--scope", "user") }
        if ($missing -contains "Git") { Install-WithWinget "Git.Git" "Git" }
        if ($missing -contains "SyncThing") { Install-WithWinget "Syncthing.Syncthing" "SyncThing" }
        Refresh-Path
        if (-not $python) { $python = Find-Python; if (-not $python) { Stop-Setup "Python installed, but this window can't find it yet. Close it and run the installer again." } }
        if (-not $git) { $git = Find-Git; if (-not $git) { Stop-Setup "Git installed, but this window can't find it yet. Close it and run the installer again." } }
        if (-not $syncthing) { $syncthing = Find-Syncthing; if (-not $syncthing) { Stop-Setup "SyncThing installed, but this window can't find it yet. Close it and run the installer again." } }
        Ok "Installed."
    }

    # ------------------------------------------------------------ 3. Pingwin
    Step 3 "Getting Pingwin"
    if (Test-Path (Join-Path $InstallDir ".git")) {
        & $git -C $InstallDir pull --ff-only --quiet
        Ok "Updated $InstallDir"
    } elseif ((Test-Path $InstallDir) -and (Get-ChildItem $InstallDir -Force | Select-Object -First 1)) {
        Stop-Setup "$InstallDir already exists and isn't a Pingwin download. Move it, or run the installer with -InstallDir somewhere else."
    } else {
        & $git clone --quiet $Repo $InstallDir
        if ($LASTEXITCODE -ne 0) { Stop-Setup "Couldn't download Pingwin from $Repo." }
        Ok "Downloaded to $InstallDir"
    }
    $venvPy = Join-Path $InstallDir ".venv\Scripts\python.exe"
    if (-not (Test-Path $venvPy)) { & $python -m venv (Join-Path $InstallDir ".venv") }
    Say "  Installing Pingwin's parts (a minute or two the first time)..."
    & $venvPy -m pip install --upgrade pip --quiet --disable-pip-version-check 2>&1 | Out-Null
    & $venvPy -m pip install -e $InstallDir --quiet --disable-pip-version-check
    if ($LASTEXITCODE -ne 0) { Stop-Setup "Installing Pingwin's Python packages failed (see above)." }
    Ok "Pingwin is installed."

    # ------------------------------------------------------------ 4. SyncThing
    Step 4 "Connecting SyncThing to the main PC"
    $ownSyncthing = $false   # true when this installer is the one that runs SyncThing
    if (-not $stConfig) {
        & $syncthing generate --home $SyncthingHome 2>&1 | Out-Null
        $stConfig = Join-Path $SyncthingHome "config.xml"
        if (-not (Test-Path $stConfig)) { Stop-Setup "SyncThing couldn't create its settings." }
        Ok "Created SyncThing's settings."
    }
    $env:PINGWIN_SYNCTHING_CONFIG = $stConfig
    & $venvPy -m pingwin.pairing check 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        $tray = if ($CustomSyncthingHome) { $null } else { Find-SyncTrayzor }
        if ($tray) {
            Start-Process $tray
        } else {
            $stHome = Split-Path $stConfig
            Start-Process $syncthing -ArgumentList @("serve", "--home", "`"$stHome`"", "--no-browser") -WindowStyle Hidden
            $ownSyncthing = $true
        }
        Say "  Starting SyncThing..."
        $up = $false
        for ($i = 0; $i -lt 60 -and -not $up; $i++) {
            Start-Sleep -Seconds 1
            & $venvPy -m pingwin.pairing check 2>$null | Out-Null
            $up = ($LASTEXITCODE -eq 0)
        }
        if (-not $up) { Stop-Setup "SyncThing didn't start. Try starting it yourself, then run the installer again." }
    }
    Ok "SyncThing is running."
    $joinOutput = & $venvPy -m pingwin.pairing join $Invite --pc $PcName --folder $SettingsDir 2>&1
    $replyLine = $joinOutput | Where-Object { "$_" -like "REPLY *" } | Select-Object -First 1
    if (-not $replyLine) { Stop-Setup ("Couldn't use the invite code: " + (($joinOutput | ForEach-Object { "$_" }) -join " ")) }
    $reply = "$replyLine".Substring(6).Trim()
    Ok "This PC now trusts the main PC, and the shared settings folder is ready at $SettingsDir"

    # ------------------------------------------------------------ 5. settings + startup
    Step 5 "Settings and startup"
    Write-Text (Join-Path $InstallDir ".env") "# This PC's own Pingwin settings. Not synced, not committed.`r`nPINGWIN_DATA=$SettingsDir`r`nPINGWIN_NODE=$PcName`r`n"
    Ok "Saved this PC's settings (name: $PcName)."
    if ($NoAutostart) {
        Say "  Skipping Windows startup (-NoAutostart)."
    } else {
        $runCmd = Join-Path $InstallDir "scripts\run_pingwin.cmd"
        Write-Text (Join-Path $Startup "Pingwin.cmd") "@echo off`r`nrem Starts Pingwin (added by the Pingwin installer)`r`nstart `"Pingwin`" /min `"$runCmd`"`r`n"
        Ok "Pingwin will start when you sign in."
        if ($ownSyncthing) {
            $stHome = Split-Path $stConfig
            $vbs = "CreateObject(""WScript.Shell"").Run """"""$syncthing"""" serve --home """"$stHome"""" --no-browser"", 0, False"
            Write-Text (Join-Path $Startup "Syncthing (Pingwin).vbs") $vbs
            Ok "SyncThing will start in the background when you sign in."
        }
    }

    # ------------------------------------------------------------ 6. reply code
    Step 6 "Almost done"
    try { Set-Clipboard -Value $reply; $copied = $true } catch { $copied = $false }
    Say ""
    Say "  Send this reply code back to the person who invited you:" "White"
    Say ""
    Say "  $reply" "Green"
    Say ""
    if ($copied) { Say "  (It's already copied. Just paste it into your message.)" }
    Say "  Once they add it, the settings arrive within a minute or two and Pingwin starts on standby."
    if (-not $NoStart) {
        Start-Process -FilePath (Join-Path $InstallDir "scripts\run_pingwin.cmd") -WindowStyle Minimized
        Say "  Pingwin is starting in a minimized window. It waits there until the settings arrive."
    }
    Say ""
    Say "  All set on this PC. Keep it on and awake (Settings > System > Power > Sleep: Never)." "White"
} catch {
    if ("$($_.Exception.Message)" -ne "PINGWIN_SETUP_STOPPED") {
        Write-Host ""
        Write-Host "Setup stopped because of an unexpected error: $($_.Exception.Message)" -ForegroundColor Red
        Write-Host "Nothing harmful happened. Run the installer again, or send this message to whoever invited you." -ForegroundColor Red
    }
}
