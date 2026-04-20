#!/bin/bash
# DLTRF Checkpoint — save / restore / status
#
# Reads configuration from dltrf.yaml (Memento: checkpoint + restore pattern).
# Falls back to configs/app_config.yaml for backward compatibility.
#
# Usage:
#   ./scripts/checkpoint.sh save     — snapshot DB after recording
#   ./scripts/checkpoint.sh restore  — restore DB before replay
#   ./scripts/checkpoint.sh status   — show saved checkpoints

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CHECKPOINT_DIR="$PROJECT_DIR/checkpoints"

# ── Config file resolution ────────────────────────────────────────────────────
# Search order: DLTRF_CONFIG env var → dltrf.yaml → configs/app_config.yaml
if [ -n "${DLTRF_CONFIG:-}" ] && [ -f "$DLTRF_CONFIG" ]; then
    CONFIG_FILE="$DLTRF_CONFIG"
elif [ -f "$PROJECT_DIR/dltrf.yaml" ]; then
    CONFIG_FILE="$PROJECT_DIR/dltrf.yaml"
elif [ -f "$PROJECT_DIR/configs/app_config.yaml" ]; then
    CONFIG_FILE="$PROJECT_DIR/configs/app_config.yaml"
    printf "  \033[1;33m⚠ Using deprecated configs/app_config.yaml — migrate to dltrf.yaml\033[0m\n"
else
    printf "  \033[0;31m✗ No config found. Create dltrf.yaml in %s\033[0m\n" "$PROJECT_DIR"
    exit 1
fi

mkdir -p "$CHECKPOINT_DIR"

# ── YAML reader (python3 + pyyaml) ────────────────────────────────────────────
cfg() {
    local keypath="$1"
    local default="${2:-}"
    python3 - <<PYEOF 2>/dev/null || echo "$default"
import yaml, sys
try:
    with open("$CONFIG_FILE") as f:
        c = yaml.safe_load(f) or {}

    def get_nested(d, path):
        keys = path.split(".")
        v = d
        for k in keys:
            if not isinstance(v, dict):
                return None
            v = v.get(k)
        return v

    v = get_nested(c, "$keypath")
    print(v if v is not None else "$default")
except Exception:
    print("$default")
PYEOF
}

# ── Colour helpers (printf only — no echo -e) ─────────────────────────────────
info()   { printf "  \033[0;36m▸ %s\033[0m\n" "$*"; }
ok()     { printf "  \033[0;32m✓ %s\033[0m\n" "$*"; }
warn()   { printf "  \033[1;33m⚠ %s\033[0m\n" "$*"; }
fail()   { printf "  \033[0;31m✗ %s\033[0m\n" "$*"; exit 1; }
banner() { printf "\n  %s\n\n" "$*"; }

check_container() {
    docker ps --format '{{.Names}}' | grep -q "^$1$" \
        || fail "Container '$1' is not running."
}

# ── Resolve config values ─────────────────────────────────────────────────────
DB_TYPE="$(      cfg state_management.type      "$(cfg app.db_type sqlite)")"
APP_CONTAINER="$(cfg state_management.container "$(cfg app.container_name juice-shop)")"
SQLITE_PATH="$(  cfg state_management.sqlite_path "$(cfg app.sqlite_path /juice-shop/data/juiceshop.sqlite)")"
CP_NAME="$(      cfg state_management.checkpoint_name "$(cfg checkpoint.name baseline)")"

CP_FILE="$CHECKPOINT_DIR/${CP_NAME}.checkpoint"

# ── SAVE ──────────────────────────────────────────────────────────────────────
do_save() {
    banner "DLTRF Checkpoint — SAVE"
    info "Config       : $CONFIG_FILE"
    info "DB type      : $DB_TYPE"
    info "Container    : $APP_CONTAINER"
    [ "$DB_TYPE" = "sqlite" ] && info "SQLite path  : $SQLITE_PATH"
    info "Checkpoint   : $CP_FILE"
    echo ""

    check_container "$APP_CONTAINER"

    case "$DB_TYPE" in
        sqlite)
            info "Copying SQLite DB from container..."
            docker cp "${APP_CONTAINER}:${SQLITE_PATH}" "$CP_FILE" \
                || fail "Copy failed. Check state_management.sqlite_path in dltrf.yaml"
            ok "SQLite checkpoint saved: $CP_FILE"
            ;;

        postgres)
            PG_CONTAINER="$(cfg state_management.postgres.container "$APP_CONTAINER")"
            PG_USER="$(      cfg state_management.postgres.user postgres)"
            PG_DB="$(        cfg state_management.postgres.database app)"
            PG_PASS="$(      cfg state_management.postgres.password "")"

            info "Dumping Postgres '$PG_DB' from '$PG_CONTAINER'..."
            check_container "$PG_CONTAINER"

            if [ -n "$PG_PASS" ]; then
                docker exec -e "PGPASSWORD=$PG_PASS" "$PG_CONTAINER" \
                    pg_dump -U "$PG_USER" -d "$PG_DB" --no-password \
                    --clean --if-exists --format=plain \
                    > "${CP_FILE}.sql"
            else
                docker exec "$PG_CONTAINER" \
                    pg_dump -U "$PG_USER" -d "$PG_DB" --no-password \
                    --clean --if-exists --format=plain \
                    > "${CP_FILE}.sql"
            fi
            CP_FILE="${CP_FILE}.sql"
            ok "Postgres dump saved: $CP_FILE"
            ;;

        mysql)
            MY_CONTAINER="$(cfg state_management.mysql.container "$APP_CONTAINER")"
            MY_USER="$(      cfg state_management.mysql.user root)"
            MY_DB="$(        cfg state_management.mysql.database app)"
            MY_PASS="$(      cfg state_management.mysql.password "")"

            info "Dumping MySQL '$MY_DB' from '$MY_CONTAINER'..."
            check_container "$MY_CONTAINER"

            if [ -n "$MY_PASS" ]; then
                docker exec -e "MYSQL_PWD=$MY_PASS" "$MY_CONTAINER" \
                    mysqldump --user="$MY_USER" \
                    --single-transaction --routines --triggers \
                    --add-drop-database --databases "$MY_DB" \
                    > "${CP_FILE}.sql"
            else
                docker exec "$MY_CONTAINER" \
                    mysqldump --user="$MY_USER" \
                    --single-transaction --routines --triggers \
                    --add-drop-database --databases "$MY_DB" \
                    > "${CP_FILE}.sql"
            fi
            CP_FILE="${CP_FILE}.sql"
            ok "MySQL dump saved: $CP_FILE"
            ;;

        custom)
            SNAPSHOT_CMD="$(cfg state_management.custom.snapshot_cmd "")"
            [ -z "$SNAPSHOT_CMD" ] && fail "custom type requires state_management.custom.snapshot_cmd in dltrf.yaml"
            info "Running custom snapshot: $SNAPSHOT_CMD"
            eval "$SNAPSHOT_CMD" || fail "Custom snapshot command failed"
            ok "Custom snapshot complete"
            ;;

        *)
            fail "Unknown db_type '$DB_TYPE'. Use: sqlite | postgres | mysql | custom"
            ;;
    esac

    # Write metadata JSON
    python3 - <<PYEOF
import json, datetime
meta = {
    "checkpoint_name": "$CP_NAME",
    "db_type":         "$DB_TYPE",
    "app_container":   "$APP_CONTAINER",
    "saved_at":        datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
    "file":            "$CP_FILE",
    "config":          "$CONFIG_FILE",
}
with open("$CHECKPOINT_DIR/${CP_NAME}.meta.json", "w") as f:
    json.dump(meta, f, indent=2)
PYEOF

    SIZE="$(du -sh "$CP_FILE" 2>/dev/null | cut -f1 || echo "?")"
    echo ""
    ok "Done (${SIZE})"
    printf "  Restore before replay:  ./scripts/checkpoint.sh restore\n\n"
}

# ── RESTORE ───────────────────────────────────────────────────────────────────
do_restore() {
    banner "DLTRF Checkpoint — RESTORE"

    local cfile="$CP_FILE"
    [ -f "$cfile" ] || cfile="${CP_FILE}.sql"
    [ -f "$cfile" ] || fail "No checkpoint at $CP_FILE — run: ./scripts/checkpoint.sh save"

    info "Config    : $CONFIG_FILE"
    info "DB type   : $DB_TYPE"
    info "Container : $APP_CONTAINER"
    info "File      : $cfile"
    echo ""

    check_container "$APP_CONTAINER"

    case "$DB_TYPE" in
        sqlite)
            # ── PAUSE → COPY → UNPAUSE ────────────────────────────────────────
            # Do NOT restart the container. Juice Shop generates its JWT signing
            # secret once at process startup. A restart creates a new secret,
            # invalidating every token captured during recording → all
            # authenticated endpoints get 401/500 on replay.
            #
            # pause/unpause freezes the process in place, swaps the DB file on
            # disk, then resumes — the Node.js process (and its secret) is never
            # replaced.
            # ──────────────────────────────────────────────────────────────────
            info "Pausing container to safely swap DB (keeps JWT secret alive)..."
            docker pause "$APP_CONTAINER" \
                || fail "Could not pause '$APP_CONTAINER'"

            info "Copying checkpoint DB into container..."
            if ! docker cp "$cfile" "${APP_CONTAINER}:${SQLITE_PATH}"; then
                # Always unpause even on failure so the container isn't left frozen
                docker unpause "$APP_CONTAINER" 2>/dev/null || true
                fail "DB copy failed. Check state_management.sqlite_path in dltrf.yaml"
            fi

            info "Resuming container (JWT secret preserved)..."
            docker unpause "$APP_CONTAINER" \
                || fail "Could not unpause '$APP_CONTAINER'"

            info "Waiting for app to be ready..."
            local n=0
            until docker inspect --format '{{.State.Running}}' "$APP_CONTAINER" \
                2>/dev/null | grep -q 'true' || [ $n -ge 10 ]; do
                sleep 1; n=$((n+1)); printf "."
            done
            sleep 1
            echo ""
            ok "SQLite restored — container live, JWT secret intact"
            ;;

        postgres)
            PG_CONTAINER="$(cfg state_management.postgres.container "$APP_CONTAINER")"
            PG_USER="$(      cfg state_management.postgres.user postgres)"
            PG_DB="$(        cfg state_management.postgres.database app)"
            PG_PASS="$(      cfg state_management.postgres.password "")"
            check_container "$PG_CONTAINER"

            _pg_exec() {
                if [ -n "$PG_PASS" ]; then
                    docker exec -e "PGPASSWORD=$PG_PASS" "$PG_CONTAINER" "$@"
                else
                    docker exec "$PG_CONTAINER" "$@"
                fi
            }

            info "Terminating active connections to '$PG_DB'..."
            _pg_exec psql -U "$PG_USER" --no-password -d postgres -c \
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='$PG_DB' AND pid<>pg_backend_pid();" \
                2>/dev/null || true

            info "Recreating database '$PG_DB'..."
            _pg_exec psql -U "$PG_USER" --no-password -d postgres \
                -c "DROP DATABASE IF EXISTS \"$PG_DB\";"
            _pg_exec psql -U "$PG_USER" --no-password -d postgres \
                -c "CREATE DATABASE \"$PG_DB\";"

            info "Restoring from dump..."
            _pg_exec psql -U "$PG_USER" --no-password \
                -d "$PG_DB" --set=ON_ERROR_STOP=1 < "$cfile"
            ok "Postgres restored"
            ;;

        mysql)
            MY_CONTAINER="$(cfg state_management.mysql.container "$APP_CONTAINER")"
            MY_USER="$(      cfg state_management.mysql.user root)"
            MY_DB="$(        cfg state_management.mysql.database app)"
            MY_PASS="$(      cfg state_management.mysql.password "")"
            check_container "$MY_CONTAINER"

            _my_exec() {
                if [ -n "$MY_PASS" ]; then
                    docker exec -e "MYSQL_PWD=$MY_PASS" "$MY_CONTAINER" "$@"
                else
                    docker exec "$MY_CONTAINER" "$@"
                fi
            }

            info "Restoring MySQL '$MY_DB'..."
            _my_exec mysql --user="$MY_USER" < "$cfile"
            ok "MySQL restored"
            ;;

        custom)
            RESTORE_CMD="$(cfg state_management.custom.restore_cmd "")"
            [ -z "$RESTORE_CMD" ] && fail "custom type requires state_management.custom.restore_cmd in dltrf.yaml"
            info "Running custom restore: $RESTORE_CMD"
            eval "$RESTORE_CMD" || fail "Custom restore command failed"
            ok "Custom restore complete"
            ;;
    esac

    # Show metadata
    local meta="$CHECKPOINT_DIR/${CP_NAME}.meta.json"
    if [ -f "$meta" ]; then
        local saved_at
        saved_at="$(python3 -c \
            "import json; print(json.load(open('$meta')).get('saved_at','?'))" \
            2>/dev/null || echo "?")"
        info "Checkpoint was saved at: $saved_at"
    fi

    echo ""
    ok "Restore complete — DB matches recording state"
    echo ""
}

# ── STATUS ────────────────────────────────────────────────────────────────────
do_status() {
    banner "DLTRF Checkpoint — STATUS"
    info "Config: $CONFIG_FILE"
    echo ""

    local found=0
    for meta in "$CHECKPOINT_DIR"/*.meta.json; do
        [ -f "$meta" ] || continue
        found=1
        python3 - <<PYEOF
import json
d = json.load(open("$meta"))
print(f"  Name     : {d.get('checkpoint_name','?')}")
print(f"  DB type  : {d.get('db_type','?')}")
print(f"  Saved at : {d.get('saved_at','?')}")
print(f"  File     : {d.get('file','?')}")
print()
PYEOF
    done

    [ $found -eq 0 ] && warn "No checkpoints in $CHECKPOINT_DIR — run: ./scripts/checkpoint.sh save"
}

# ── Dispatch ──────────────────────────────────────────────────────────────────
case "${1:-}" in
    save)    do_save    ;;
    restore) do_restore ;;
    status)  do_status  ;;
    *)
        printf "\n  Usage: %s <save|restore|status>\n\n" "$0"
        printf "  save    — snapshot DB (run after recording a session)\n"
        printf "  restore — restore DB  (run before every replay)\n"
        printf "  status  — show saved checkpoints\n\n"
        exit 1
        ;;
esac