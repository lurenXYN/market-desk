# Copy a consistent snapshot of the VPS desk.db into the temp dir for research.
# Never touches the local data/desk.db, so the local desk can keep running.
#
# Usage:
#   $env:MD_VPS_HOST = "root@1.2.3.4"      # or pass -HostName
#   .\scripts\pull-vps-db-research.ps1
#   .\.venv\Scripts\python.exe scripts\audit_attribution.py --db $env:TEMP\desk-vps.db
#
# The remote side uses sqlite3's online backup (".backup"), which is consistent
# while the service is writing in WAL mode; needs the sqlite3 CLI on the VPS.

param(
    [string]$HostName = $env:MD_VPS_HOST,
    [string]$RemoteDb = "/opt/market-desk/data/desk.db",
    [string]$Out = (Join-Path $env:TEMP "desk-vps.db")
)

$ErrorActionPreference = "Stop"
if (-not $HostName) { throw "Set -HostName root@IP or `$env:MD_VPS_HOST first" }

$stamp = Get-Date -Format "yyyyMMddHHmmss"
$remoteTmp = "/tmp/desk-research-$stamp.db"

ssh $HostName "sqlite3 '$RemoteDb' '.backup $remoteTmp'"
if ($LASTEXITCODE -ne 0) { throw "remote backup failed" }
try {
    scp "${HostName}:$remoteTmp" $Out
    if ($LASTEXITCODE -ne 0) { throw "scp failed" }
} finally {
    ssh $HostName "rm -f '$remoteTmp'" | Out-Null
}
Write-Host "Done: $Out  (pass --db `"$Out`" to scripts/audit_*.py)"
