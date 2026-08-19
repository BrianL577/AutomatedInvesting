# Safe, unattended self-update for the live TopstepX trading bot. Meant to
# run once daily via Task Scheduler, well before the 9:30am ET session open,
# and as the "at startup"/"at logon" action in place of a bare
# run_live_loop.ps1 launch — see README.md's "Running it fully automated"
# section.
#
# Flow: stop whatever's currently running -> git fetch + fast-forward-only
# pull -> pip install -> pytest -> if tests fail, roll back to the
# pre-pull commit -> (re)launch run_live_loop.ps1, detached.
#
# Deliberately conservative:
#   - Never merges or force-resets onto the new code — a real merge
#     conflict (local edits on the machine, a rebase, etc.) needs a human,
#     not an unattended script guessing. Fast-forward-only; on failure, it
#     just restarts on whatever commit was already there.
#   - Never leaves the bot running on code that failed its own test suite
#     — rolls back to the last known-good commit instead.
#   - The bot's own single-instance lock (jj_bot/live_runner_topstepx.py,
#     topstepx_live.lock) is a second line of defense if the process-kill
#     step below misses something — a genuinely stuck process just makes
#     the freshly-launched one refuse to start, loudly, instead of both
#     trading at once.
#
# Usage (from Task Scheduler, or manually to test):
#   powershell -ExecutionPolicy Bypass -File scripts\self_update.ps1

$ErrorActionPreference = "Continue"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$LogDir = Join-Path $RepoRoot "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogFile = Join-Path $LogDir "self_update.log"

function Log($msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $msg"
    Write-Host $line
    Add-Content -Path $LogFile -Value $line
}

function Send-UpdateAlert($subject, $body) {
    try {
        $result = python scripts\send_update_alert.py $subject $body 2>&1
        $result | ForEach-Object { Log "  [alert] $_" }
    } catch {
        Log "Could not send alert email: $_"
    }
}

Log "=== self_update starting ==="

# --- Stop whatever's currently running (both the wrapper loop and the bot
# itself), so the git pull / dependency install below doesn't race a live
# process reading those same files. ---
Get-CimInstance Win32_Process -Filter "Name='python.exe' or Name='powershell.exe'" |
    Where-Object { $_.CommandLine -match "run_live\.py|run_live_loop\.ps1" } |
    ForEach-Object {
        Log "Stopping PID $($_.ProcessId): $($_.CommandLine)"
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
Start-Sleep -Seconds 3

# --- Fetch + fast-forward-only pull. ---
$oldCommit = (git rev-parse HEAD).Trim()
git fetch origin main 2>&1 | ForEach-Object { Log "  [git] $_" }

$pullOk = $true
git merge --ff-only origin/main 2>&1 | ForEach-Object { Log "  [git] $_" }
if ($LASTEXITCODE -ne 0) {
    Log "Fast-forward pull failed (local changes on this machine, or a real conflict) — leaving code as-is and restarting on the current commit."
    Send-UpdateAlert "[AutomatedInvesting] Self-update: pull failed" "git merge --ff-only failed on $RepoRoot (commit $oldCommit). Left the bot on its current commit and restarted. This needs a human to look at logs\self_update.log and resolve manually — auto-update will keep failing every day until then."
    $pullOk = $false
}

$newCommit = (git rev-parse HEAD).Trim()
if ($pullOk -and $newCommit -ne $oldCommit) {
    Log "Updated $oldCommit -> $newCommit. Installing dependencies and running tests before restarting..."
    pip install -r requirements.txt -q 2>&1 | ForEach-Object { Log "  [pip] $_" }

    python -m pytest tests\ -q 2>&1 | ForEach-Object { Log "  [pytest] $_" }
    if ($LASTEXITCODE -ne 0) {
        Log "Tests FAILED on $newCommit — rolling back to $oldCommit and restarting on the last known-good commit."
        git reset --hard $oldCommit 2>&1 | ForEach-Object { Log "  [git] $_" }
        Send-UpdateAlert "[AutomatedInvesting] Self-update: rolled back" "New commit $newCommit failed 'pytest tests\' during scripts\self_update.ps1. Rolled back to the last known-good commit $oldCommit and restarted on that. Check logs\self_update.log for the test failure."
    } else {
        Log "Tests passed on $newCommit — restarting on the new code."
    }
} elseif ($pullOk) {
    Log "Already up to date at $oldCommit — no code changes, restarting as-is."
}

# --- Relaunch, detached, so this scheduled task can exit while the bot
# keeps running under run_live_loop.ps1's own crash-restart loop. ---
Log "Relaunching run_live_loop.ps1..."
Start-Process -FilePath "powershell.exe" `
    -ArgumentList "-ExecutionPolicy", "Bypass", "-File", "scripts\run_live_loop.ps1" `
    -WorkingDirectory $RepoRoot -WindowStyle Hidden

Log "=== self_update finished ==="
