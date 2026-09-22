# Pull a consistent desk.db from the VPS.
# Prefer: stop remote service → wal_checkpoint → scp desk.db
# Or copy data/backup/desk-YYYY-MM-DD-HHMMSS.db
#
#   HOST=root@vps REMOTE_DB=/opt/market-desk/data/desk.db ./scripts/pull-desk-db.sh

set -euo pipefail
HOST="${HOST:-root@YOUR_VPS}"
REMOTE_DB="${REMOTE_DB:-/opt/market-desk/data/desk.db}"
LOCAL_DIR="$(cd "$(dirname "$0")/.." && pwd)/data"
LOCAL_DB="$LOCAL_DIR/desk.db"

echo "Stop local market-desk first."
echo "Will wipe local desk.db + -shm + -wal then scp $HOST:$REMOTE_DB"
read -r -p "Type YES: " ans
[[ "$ans" == "YES" ]] || { echo aborted; exit 1; }

mkdir -p "$LOCAL_DIR"
rm -f "$LOCAL_DB" "$LOCAL_DB-shm" "$LOCAL_DB-wal"
scp "$HOST:$REMOTE_DB" "$LOCAL_DB"
echo "OK -> $LOCAL_DB"
