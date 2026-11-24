$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$runtime = Join-Path $PSScriptRoot '.live'
New-Item -ItemType Directory -Force -Path $runtime | Out-Null
$tunnel = Join-Path $PSScriptRoot 'tools\cloudflared.exe'
if (!(Test-Path -LiteralPath $tunnel)) { throw 'Install cloudflared at tools\cloudflared.exe first.' }
if (Get-NetTCPConnection -LocalPort 5051 -State Listen -ErrorAction SilentlyContinue) { throw 'Port 5051 is already in use. Stop the previous live session first.' }
$pythonExe = (Get-Command python).Source
$collector = Start-Process $pythonExe -ArgumentList 'live_feed.py' -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtime 'collector.out.log') -RedirectStandardError (Join-Path $runtime 'collector.err.log')
$bridge = Start-Process $tunnel -ArgumentList 'tunnel','--url','http://127.0.0.1:5051','--no-autoupdate' -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtime 'tunnel.out.log') -RedirectStandardError (Join-Path $runtime 'tunnel.err.log')
Write-Output "Collector PID: $($collector.Id); tunnel PID: $($bridge.Id)"
for ($attempt=0; $attempt -lt 30; $attempt++) {
    Start-Sleep -Seconds 1
    $log = Get-Content -LiteralPath (Join-Path $runtime 'tunnel.err.log') -Raw -ErrorAction SilentlyContinue
    if ($log -match 'https://[a-z0-9-]+\.trycloudflare\.com') {
        $feedUrl = $Matches[0]
        Write-Output "Open: https://iiiiiiiigt.github.io/arbitrage/dashboard.html#feed=$feedUrl"
        Write-Output 'Keep this PC on. Closing the dashboard does not stop the collector.'
        exit 0
    }
}
throw 'Tunnel URL not received yet. Check .live\tunnel.err.log.'
