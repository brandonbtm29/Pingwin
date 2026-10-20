# Opens Pingwin's dashboard, starting Pingwin first (in the background) if it isn't running.
# Used by the "Pingwin Dashboard" desktop shortcut and scripts\open_dashboard.cmd.
$ErrorActionPreference = "Continue"
$root = Split-Path $PSScriptRoot -Parent
$urlFile = Join-Path $root ".dashboard-url"

function Get-DashboardUrl {
    if (Test-Path $urlFile) { return (Get-Content $urlFile -TotalCount 1).Trim() }
    return $null
}

function Test-Up([string]$url) {
    if (-not $url) { return $false }
    try {
        $u = [uri]$url
        $client = New-Object System.Net.Sockets.TcpClient
        $task = $client.ConnectAsync($u.Host, $u.Port)
        $ok = $task.Wait(1500) -and $client.Connected
        $client.Close()
        return $ok
    } catch { return $false }
}

function Show-Message([string]$text) {
    $null = (New-Object -ComObject WScript.Shell).Popup($text, 0, "Pingwin", 48)
}

$url = Get-DashboardUrl
if (-not (Test-Up $url)) {
    Start-Process wscript.exe -ArgumentList "`"$(Join-Path $PSScriptRoot 'start_hidden.vbs')`""
    $deadline = (Get-Date).AddSeconds(90)
    do {
        Start-Sleep -Seconds 2
        $url = Get-DashboardUrl
    } until ((Test-Up $url) -or ((Get-Date) -gt $deadline))
    if (-not (Test-Up $url)) {
        Show-Message "Pingwin didn't start within 90 seconds.`n`nIf a Pingwin window opened, it says what's wrong. Otherwise see logs\pingwin.log in the Pingwin folder."
        exit 1
    }
}
Start-Process $url
