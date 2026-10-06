#!/usr/bin/env bash
# Monthly restore test (ADR 0009): prove the latest restic backup restores.
#
# Restores the latest snapshot into a throwaway Postgres container and asserts:
#   1. the finance ledger is non-empty (finance.journal_entry count > 0)
#   2. the raw-immutability trigger (trg_source_record_raw_immutable) exists
#
# This script is HONEST: it never exits 0 without having restored and checked
# something. Missing prerequisites are a loud failure, not a skip.
#
# Credentials live in ~/.config/lifeos/, OUTSIDE this repo (SAFETY.md Rule 2).
# Nothing here writes secrets anywhere.
#
# Usage: tools/backup_test.sh  (or: make backup-test)
#   SNAPSHOT_ID   restic snapshot to restore (default: latest)
#   POSTGRES_IMAGE image for the throwaway DB (default: postgres:17-alpine)
set -euo pipefail

SNAPSHOT_ID="${SNAPSHOT_ID:-latest}"
POSTGRES_IMAGE="${POSTGRES_IMAGE:-postgres:17-alpine}"
CONTAINER="lifeos-backup-test-$$"
TMPDIR="$(mktemp -d "${TMPDIR:-/tmp}/lifeos-backup-test.XXXXXX")"
RESTORE_USER="lifeos"
RESTORE_DB="lifeos"
# Throwaway password for the throwaway container only. It never leaves this
# machine and the container is removed on exit; it is NOT a credential.
RESTORE_PASSWORD="backup-test-only"

cleanup() {
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
    # TMPDIR is a mktemp directory under /tmp, created by this script in this
    # run — never the working tree. Guarded against the empty/unset case.
    if [ -n "${TMPDIR:-}" ] && [ -d "$TMPDIR" ]; then
        rm -rf "$TMPDIR"
    fi
}
trap cleanup EXIT

fail() {
    echo "backup_test: FAIL: $*" >&2
    exit 1
}

# --- prerequisites: fail loudly, never skip ---------------------------------
missing_env=()
[ -n "${RESTIC_REPOSITORY:-}" ] || missing_env+=("RESTIC_REPOSITORY")
if [ -z "${RESTIC_PASSWORD:-}" ] && [ -z "${RESTIC_PASSWORD_FILE:-}" ]; then
    missing_env+=("RESTIC_PASSWORD or RESTIC_PASSWORD_FILE")
fi
if [ "${#missing_env[@]}" -gt 0 ]; then
    cat >&2 <<EOF
backup_test: FAIL: missing required environment (stored in ~/.config/lifeos/, outside the repo):
EOF
    for v in "${missing_env[@]}"; do
        echo "backup_test: FAIL:   - $v is not set" >&2
    done
    echo "backup_test: FAIL: refusing to pass without a repository to restore from." >&2
    exit 1
fi
command -v restic >/dev/null 2>&1 || fail "restic is not installed or not on PATH."
command -v docker >/dev/null 2>&1 || fail "docker is not installed or not on PATH."
docker info >/dev/null 2>&1 || fail "docker daemon is not reachable (docker info failed)."

echo "backup_test: repository: $RESTIC_REPOSITORY"
echo "backup_test: snapshot:   $SNAPSHOT_ID"

# --- restore the snapshot ----------------------------------------------------
restic snapshots "$SNAPSHOT_ID" >/dev/null \
    || fail "snapshot '$SNAPSHOT_ID' not found in repository."
echo "backup_test: restoring snapshot '$SNAPSHOT_ID' ..."
restic restore "$SNAPSHOT_ID" --target "$TMPDIR/restore" \
    || fail "restic restore of snapshot '$SNAPSHOT_ID' failed."

# The snapshot must contain a Postgres dump. Accept plain SQL or a pg_restore
# custom-format archive; anything else is not a database backup.
mapfile -t SQL_DUMPS < <(find "$TMPDIR/restore" -type f -name '*.sql' | sort || true)
mapfile -t CUSTOM_DUMPS < <(find "$TMPDIR/restore" -type f \( -name '*.dump' -o -name '*.pgdump' -o -name '*.custom' \) | sort || true)
if [ "${#SQL_DUMPS[@]}" -eq 0 ] && [ "${#CUSTOM_DUMPS[@]}" -eq 0 ]; then
    echo "backup_test: FAIL: snapshot '$SNAPSHOT_ID' contains no database dump." >&2
    echo "backup_test: FAIL: restored tree ($TMPDIR/restore):" >&2
    find "$TMPDIR/restore" -maxdepth 3 >&2 || true
    echo "backup_test: FAIL: expected at least one *.sql or *.dump file." >&2
    exit 1
fi
echo "backup_test: found ${#SQL_DUMPS[@]} SQL dump(s), ${#CUSTOM_DUMPS[@]} custom-format dump(s)."

# --- throwaway Postgres -------------------------------------------------------
echo "backup_test: starting throwaway Postgres ($POSTGRES_IMAGE) ..."
docker run -d --rm --name "$CONTAINER" \
    -e POSTGRES_USER="$RESTORE_USER" \
    -e POSTGRES_PASSWORD="$RESTORE_PASSWORD" \
    -e POSTGRES_DB="$RESTORE_DB" \
    "$POSTGRES_IMAGE" >/dev/null \
    || fail "could not start throwaway Postgres container."

echo "backup_test: waiting for Postgres to accept connections ..."
ready=0
for _ in $(seq 1 60); do
    if docker exec "$CONTAINER" pg_isready -U "$RESTORE_USER" -d "$RESTORE_DB" >/dev/null 2>&1; then
        ready=1
        break
    fi
    sleep 1
done
[ "$ready" -eq 1 ] || fail "throwaway Postgres did not become ready in 60s."

psql() {
    docker exec -i "$CONTAINER" psql -v ON_ERROR_STOP=1 -U "$RESTORE_USER" -d "$RESTORE_DB" "$@"
}

# --- load the dump -------------------------------------------------------------
for f in ${SQL_DUMPS[@]+"${SQL_DUMPS[@]}"}; do
    echo "backup_test: loading $f ..."
    psql < "$f" \
        || fail "loading SQL dump $f failed."
done
for f in ${CUSTOM_DUMPS[@]+"${CUSTOM_DUMPS[@]}"}; do
    echo "backup_test: restoring $f ..."
    docker exec -i "$CONTAINER" pg_restore -v ON_ERROR_STOP=1 -U "$RESTORE_USER" -d "$RESTORE_DB" \
        < "$f" || fail "pg_restore of $f failed."
done

# --- assertions: the only path to exit 0 --------------------------------------
entry_count="$(psql -tAc 'SELECT count(*) FROM finance.journal_entry;')" \
    || fail "could not query finance.journal_entry (did the dump restore the finance schema?)."
# Integer check: the count must be digits, then compared numerically.
case "$entry_count" in
    ''|*[!0-9]*) fail "finance.journal_entry count is not a number: '$entry_count'." ;;
esac
[ "$entry_count" -gt 0 ] \
    || fail "finance.journal_entry is EMPTY in the restored backup (count=0). A ledger with no entries proves nothing."
echo "backup_test: finance.journal_entry count = $entry_count (non-empty, OK)."

trigger_count="$(psql -tAc "SELECT count(*) FROM pg_trigger WHERE tgname = 'trg_source_record_raw_immutable';")" \
    || fail "could not query pg_trigger for the raw-immutability trigger."
case "$trigger_count" in
    ''|*[!0-9]*) fail "trigger query returned a non-number: '$trigger_count'." ;;
esac
[ "$trigger_count" -ge 1 ] \
    || fail "trg_source_record_raw_immutable NOT FOUND in the restored backup. Raw-evidence protection did not survive."
echo "backup_test: trg_source_record_raw_immutable present (count=$trigger_count, OK)."

echo "backup_test: PASS: snapshot '$SNAPSHOT_ID' restored; ledger non-empty ($entry_count entries); raw-immutability trigger present."
