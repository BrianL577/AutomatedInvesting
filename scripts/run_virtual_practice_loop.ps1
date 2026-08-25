# Restart wrapper for scripts/run_virtual_practice.py -- mirrors
# run_live_loop.ps1: relaunches the practice-mode process a few seconds
# after any exit (crash, network blip, etc.) so it survives unattended the
# same way the real bot does.
#
# Usage (from the repo root):
#   powershell -ExecutionPolicy Bypass -File scripts\run_virtual_practice_loop.ps1

$ErrorActionPreference = "Continue"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$LogDir = Join-Path $RepoRoot "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogFile = Join-Path $LogDir "run_virtual_practice_loop.log"

function Log($msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $msg"
    Write-Host $line
    Add-Content -Path $LogFile -Value $line
}

Log "=== run_virtual_practice_loop starting ==="

while ($true) {
    Log "Launching: python scripts/run_virtual_practice.py --accounts 10"
    python scripts/run_virtual_practice.py --accounts 10
    $exitCode = $LASTEXITCODE
    Log "run_virtual_practice.py exited with code $exitCode. Restarting in 15 seconds..."
    Start-Sleep -Seconds 15
}
