# Start the first three camera-navigation runs (sim/live_runs.py) in this window.
#
#   wt -w new -d C:\Monad-Hackathon-live-nav powershell -NoExit -File scripts\launch_first3.ps1
#
# Every line, the runs' progress lines included, also goes to
# C:\Monad-Hackathon-Project\data\campaigns\live-nav-first3\launch.log.
# Run it again after a crash or a closed window: finished runs are skipped,
# unfinished ones start over. Extra arguments pass through to sim.live_runs
# (e.g. -- --serial, or --dry-run to print the plan only).

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
$host.UI.RawUI.WindowTitle = 'Lantern: live-nav-first3'
Write-Host "Lantern live-nav-first3 from $(Get-Location), branch $(git rev-parse --abbrev-ref HEAD) at $(git rev-parse --short HEAD)"
$env:PYTHONUNBUFFERED = '1'
python -m sim.live_runs @args
$code = $LASTEXITCODE
Write-Host ''
if ($code -eq 0) {
    Write-Host "All runs finished (exit 0). summary.csv and launch.log are in the batch folder printed above."
} else {
    Write-Host "sim.live_runs exited with $code. See launch.log; run this script again to retry the unfinished runs."
}
