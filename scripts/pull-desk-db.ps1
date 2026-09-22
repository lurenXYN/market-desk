# Pull a consistent desk.db from VPS to local (PowerShell).
# Usage (edit HOST / paths first):
#   .\scripts\pull-desk-db.ps1
#
# Critical: stop local market-desk first; delete local desk.db + -shm + -wal
# before copying. Prefer a checkpointed remote copy or data/backup/desk-*.db.

param(
    [string]$HostName = "root@YOUR_VPS",
    [string]$RemoteDb = "/opt/market-desk/data/desk.db",
    [string]$LocalDir = (Join-Path $PSScriptRoot "..\data")
)

$ErrorActionPreference = "Stop"
$localDb = Join-Path $LocalDir "desk.db"
$localShm = Join-Path $LocalDir "desk.db-shm"
$localWal = Join-Path $LocalDir "desk.db-wal"

Write-Host "1) Stop local market-desk (uvicorn/start.bat) before continuing."
Write-Host "2) Will remove local desk.db / -shm / -wal then scp $RemoteDb"
$confirm = Read-Host "Type YES to continue"
if ($confirm -ne "YES") { throw "aborted" }

New-Item -ItemType Directory -Force -Path $LocalDir | Out-Null
foreach ($f in @($localDb, $localShm, $localWal)) {
    if (Test-Path $f) { Remove-Item -Force $f }
}

# Prefer copying a remote backup snapshot if you pass -RemoteDb to desk-*.db
scp "${HostName}:${RemoteDb}" $localDb
Write-Host "Done: $localDb"
Write-Host "Start local market-desk. Do NOT keep old -wal/-shm beside a replaced db."
