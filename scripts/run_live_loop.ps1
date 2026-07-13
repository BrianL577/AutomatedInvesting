# Restart wrapper for scripts/run_live.py — NinjaTrader has no Railway-style
# auto-restart, so this loop relaunches the bot a few seconds after any exit
# (crash, NT8 hiccup, etc). Intended to be launched once by a Task Scheduler
# job (see NINJATRADER.md section 7) so it survives reboots too.
#
# Usage (from the repo root):
#   powershell -ExecutionPolicy Bypass -File scripts\run_live_loop.ps1

$ErrorActionPreference = "Continue"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$LogDir = Join-Path $RepoRoot "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogFile = Join-Path $LogDir "run_live_loop.log"

function Log($msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $msg"
    Write-Host $line
    Add-Content -Path $LogFile -Value $line
}

Log "=== run_live_loop starting ==="

while ($true) {
    Log "Launching: python scripts/run_live.py --symbol NQ"
    python scripts/run_live.py --symbol NQ
    $exitCode = $LASTEXITCODE
    Log "run_live.py exited with code $exitCode. Restarting in 15 seconds..."
    Start-Sleep -Seconds 15
}
