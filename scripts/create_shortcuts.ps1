# Pingwin's shortcuts:
#   (default)  "Pingwin Dashboard" on the desktop and in the Start menu. It opens the dashboard,
#              starting Pingwin in the background first if it isn't running.
#   -Startup   start Pingwin in the background (no window) when you sign in to Windows.
#   -NoDesktop skip the desktop/Start menu shortcuts (with -Startup).
param([switch]$Startup, [switch]$NoDesktop, [switch]$Quiet)
$ErrorActionPreference = "Continue"
$root = Split-Path $PSScriptRoot -Parent
$shell = New-Object -ComObject WScript.Shell
$icon = Join-Path $root "assets\pingwin.ico"

function New-Shortcut([string]$path, [string]$target, [string]$arguments, [string]$description) {
    $lnk = $shell.CreateShortcut($path)
    $lnk.TargetPath = $target
    $lnk.Arguments = $arguments
    $lnk.WorkingDirectory = $root
    $lnk.WindowStyle = 7  # minimized, so no window flashes up
    if (Test-Path $icon) { $lnk.IconLocation = $icon }
    $lnk.Description = $description
    $lnk.Save()
    if (-not $Quiet) { Write-Host "Added: $path" }
}

if (-not $NoDesktop) {
    $ps = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
    $dashArgs = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$(Join-Path $PSScriptRoot 'open_dashboard.ps1')`""
    foreach ($dir in @([Environment]::GetFolderPath("Desktop"), [Environment]::GetFolderPath("Programs"))) {
        if ($dir -and (Test-Path $dir)) {
            New-Shortcut (Join-Path $dir "Pingwin Dashboard.lnk") $ps $dashArgs "Open Pingwin's dashboard (starts Pingwin if needed)"
        }
    }
}

if ($Startup) {
    $folder = [Environment]::GetFolderPath("Startup")
    $wscript = Join-Path $env:SystemRoot "System32\wscript.exe"
    New-Shortcut (Join-Path $folder "Pingwin.lnk") $wscript "`"$(Join-Path $PSScriptRoot 'start_hidden.vbs')`"" "Starts Pingwin in the background"
    $old = Join-Path $folder "Pingwin.cmd"  # older versions started Pingwin in a minimized window
    if (Test-Path $old) { Remove-Item $old -Force; if (-not $Quiet) { Write-Host "Removed the old launcher: $old" } }
}
