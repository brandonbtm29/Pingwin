# Stops Pingwin on this PC: asks it to stop politely through the dashboard, then makes sure.
$ErrorActionPreference = "Continue"
$root = Split-Path $PSScriptRoot -Parent

function Get-Runners {
    Get-CimInstance Win32_Process -Filter "Name='cmd.exe'" |
        Where-Object { $_.CommandLine -like "*$root*run_pingwin.cmd*" }
}

if (-not (Get-Runners)) { Write-Host "Pingwin isn't running."; exit 0 }

$urlFile = Join-Path $root ".dashboard-url"
$keyFile = Join-Path $root ".dashboard-key"
if ((Test-Path $urlFile) -and (Test-Path $keyFile)) {
    try {
        $u = [uri](Get-Content $urlFile -TotalCount 1).Trim()
        $base = "http://127.0.0.1:$($u.Port)"
        $key = (Get-Content $keyFile -TotalCount 1).Trim()
        $web = New-Object Microsoft.PowerShell.Commands.WebRequestSession
        $web.Cookies.Add((New-Object System.Net.Cookie("pingwin_dashboard", $key, "/", "127.0.0.1")))
        $headers = @{ "X-Pingwin" = "1"; "Origin" = $base }
        $null = Invoke-RestMethod -Method Post -Uri "$base/api/action" -WebSession $web -Headers $headers -ContentType "application/json" -Body '{"action":"stop"}' -TimeoutSec 10 -UseBasicParsing
        Write-Host "Asked Pingwin to stop..."
        for ($i = 0; $i -lt 15 -and (Get-Runners); $i++) { Start-Sleep -Seconds 1 }
    } catch { }
}
foreach ($p in @(Get-Runners)) { & taskkill.exe /PID $p.ProcessId /T /F | Out-Null }
Write-Host "Pingwin is stopped."
