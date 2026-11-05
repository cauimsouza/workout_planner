#!/bin/bash
# Back up the app's database to a restic repository (e.g. a Cloudflare R2 bucket), without stopping
# the app. Settings come from backup.env next to this script (see backup.env.example).
#
#   ./backup.sh                  Take a backup, then delete snapshots older than the retention policy
#   ./backup.sh init             Create the restic repository (once)
#   ./backup.sh restore [ID]     Save a snapshot's database (default: latest) to the current folder
#   ./backup.sh restic ARGS...   Run any restic command with the settings loaded, e.g. "snapshots"

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG="${BACKUP_CONFIG:-$SCRIPT_DIR/backup.env}"

if [[ ! -f "$CONFIG" ]]; then
    echo "Missing $CONFIG: copy backup.env.example to backup.env and fill it in." >&2
    exit 1
fi
set -a
# shellcheck source=/dev/null
source "$CONFIG"
set +a

: "${RESTIC_REPOSITORY:?Set RESTIC_REPOSITORY in $CONFIG}"
: "${AWS_ACCESS_KEY_ID:?Set AWS_ACCESS_KEY_ID in $CONFIG}"
: "${AWS_SECRET_ACCESS_KEY:?Set AWS_SECRET_ACCESS_KEY in $CONFIG}"
if [[ -z "${RESTIC_PASSWORD:-}" && -z "${RESTIC_PASSWORD_FILE:-}" ]]; then
    echo "Set RESTIC_PASSWORD or RESTIC_PASSWORD_FILE in $CONFIG." >&2
    exit 1
fi

CONTAINER="${CONTAINER:-workout-tracker}"
DATABASE="${DATABASE:-/app/data/database.db}"
KEEP="${KEEP:---keep-daily 7 --keep-weekly 8 --keep-monthly 24}"
# The snapshot is always uploaded from this path, so `restore` knows where to find it in a backup.
STAGING_DIR="${STAGING_DIR:-$HOME/.cache/workout-backup}"
STAGING_FILE="$STAGING_DIR/database.db"
SNAPSHOT_IN_CONTAINER=/tmp/workout-backup.db

ping_healthcheck() { # $1: "" on success, "/fail" on failure
    if [[ -n "${HEALTHCHECK_URL:-}" ]]; then
        curl -fsS -m 10 --retry 3 -o /dev/null "$HEALTHCHECK_URL$1" || echo "Couldn't ping $HEALTHCHECK_URL$1" >&2
    fi
}

backup() {
    trap 'ping_healthcheck /fail' ERR
    echo "$(date '+%F %T') Backing up $DATABASE from $CONTAINER"

    # VACUUM INTO makes a consistent copy while the app keeps running, then the copy is checked.
    docker exec "$CONTAINER" python -c "
import os, sqlite3, sys
out = '$SNAPSHOT_IN_CONTAINER'
if os.path.exists(out):
    os.remove(out)
sqlite3.connect('$DATABASE').execute('VACUUM INTO ?', (out,))
result = sqlite3.connect(out).execute('PRAGMA integrity_check').fetchone()[0]
if result != 'ok':
    sys.exit(f'Integrity check failed: {result}')
"
    mkdir -p "$STAGING_DIR"
    docker cp "$CONTAINER:$SNAPSHOT_IN_CONTAINER" "$STAGING_FILE"
    docker exec "$CONTAINER" rm -f "$SNAPSHOT_IN_CONTAINER"

    restic backup --tag workout-planner "$STAGING_FILE"
    # shellcheck disable=SC2086 # KEEP holds several options
    restic forget --tag workout-planner $KEEP --prune
    rm -f "$STAGING_FILE"

    ping_healthcheck ""
    echo "$(date '+%F %T') Backup done"
}

restore() {
    local snapshot="${1:-latest}"
    local out
    out="$PWD/workout-restore-$(date +%Y%m%d%H%M%S).db"
    restic dump "$snapshot" "$STAGING_FILE" > "$out"
    python3 -c "
import sqlite3, sys
result = sqlite3.connect(sys.argv[1]).execute('PRAGMA integrity_check').fetchone()[0]
sys.exit(0 if result == 'ok' else f'Integrity check failed: {result}')
" "$out"
    echo "Saved snapshot $snapshot to $out"
    echo
    echo "To replace the app's database with it (the current one is kept as database.db.before-restore):"
    echo "  docker stop $CONTAINER"
    echo "  docker cp $CONTAINER:$DATABASE ./database.db.before-restore"
    echo "  docker cp $out $CONTAINER:$DATABASE"
    echo "  docker start $CONTAINER"
}

case "${1:-backup}" in
    backup) backup ;;
    init) restic init ;;
    restore) shift; restore "$@" ;;
    restic) shift; restic "$@" ;;
    *) sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 1 ;;
esac
