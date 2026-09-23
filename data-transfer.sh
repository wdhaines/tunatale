#!/usr/bin/env bash
# Move TunaTale's state between this Mac (dev) and the production box, either way.
#
#   ./data-transfer.sh status            compare dev and prod now, change nothing
#   ./data-transfer.sh to-prod           dry run: show the plan and the diff
#   ./data-transfer.sh to-prod --apply   dev -> prod, then hand AnkiWeb sync to prod
#   ./data-transfer.sh to-dev  --apply   prod -> dev, then hand AnkiWeb sync to dev
#
# A transfer is a HANDOVER, not a copy: exactly one side syncs with AnkiWeb
# afterwards (the destination), because two TunaTales pushing to one AnkiWeb
# account from different states is how scheduling data gets overwritten.
# Before running: do a final TunaTale sync ON THE SOURCE, and stop the dev
# server (the script refuses to apply while it runs).
#
# Safety: the databases and TunaTale's own Anki collection are copied with
# SQLite's backup API (backend/scripts/data_snapshot.py), never `cp` — WAL
# databases hold committed rows outside the main file. The destination's
# current state is saved to a timestamped transfer-backups/ dir first, and
# rsync's --backup-dir keeps every file it overwrites or deletes, so any
# transfer can be undone. Row counts of every table, and file counts/bytes of
# every directory, are compared source vs destination; a mismatch exits 1.
#
# NOT transferred: auth.db (production only), logs, the AnkiWeb password file,
# and the Mac's backup directories. See docs/deployment.md § "Moving data".
#
# Normally the laptop side is the dev checkout (backend/*.db, ~/.tunatale).
# switch.sh points it at the separate live instance instead (tunatale-qyw0):
#   TT_LAPTOP_ROOT          laptop data dir, laid out exactly like prod's volume
#   TT_LOCAL_ENV_FILE       the env file whose SYNC_ENABLED this script flips
#   TT_LOCAL_SERVER_PATTERN pgrep -f pattern for the server that must be stopped
#   TT_PARK_URL             to-dev parks prod with PARKED_AT=<this>; to-prod unparks
set -euo pipefail

HOST="${TT_DEPLOY_HOST:-tunatale}"
USER_AT="${TT_DEPLOY_USER:+${TT_DEPLOY_USER}@}"
SSH_KEY="${TT_DEPLOY_KEY:-$HOME/.ssh/google_compute_engine}"
REMOTE_DIR="${TT_DEPLOY_DIR:-/opt/tunatale}"
VOLUME="${TT_DATA_VOLUME:-tunatale_data}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$REPO/backend"
DEV_TT="$HOME/.tunatale"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
HELPER="$BACKEND/scripts/data_snapshot.py"

# name|dev path|prod path relative to the volume root. SQLite files go through
# the backup API; directories and plain files through rsync.
SQLITE=(
  "tunatale_no.db|$BACKEND/tunatale_no.db|tunatale_no.db"
  "tunatale_sl.db|$BACKEND/tunatale_sl.db|tunatale_sl.db"
  "tunatale_tl.db|$BACKEND/tunatale_tl.db|tunatale_tl.db"
  "tt_collection.anki2|$DEV_TT/tt_collection.anki2|.tunatale/tt_collection.anki2"
)
DIRS=(
  "media|$BACKEND/media|media"
  "audio|$BACKEND/output/audio|output/audio"
  "tts-cache|$DEV_TT/tts-cache|.tunatale/tts-cache"
  "alignment-cache|$DEV_TT/alignment-cache|.tunatale/alignment-cache"
)
# ⚠️ TO-PROD ONLY: Anki MEDIA-sync state. On the Mac, tt_collection.media is a
# SYMLINK to desktop Anki's own collection.media (sync_orchestrator.py
# _link_media_dir), so writing into it — let alone `rsync --delete` — would
# rewrite the user's real Anki media library. It moves UP only, and always as a
# PAIR with the media DB that describes it: a media DB listing files the folder
# lacks reads as DELETIONS on the next media sync, which would propagate to
# AnkiWeb and to every device. On the Mac, desktop Anki keeps that folder in
# sync itself, so to-dev leaves both alone.
SQLITE_UP=(
  "tt_collection.media.db2|$DEV_TT/tt_collection.media.db2|.tunatale/tt_collection.media.db2"
)
DIRS_UP=(
  "tt_collection.media|$DEV_TT/tt_collection.media|.tunatale/tt_collection.media"
)
FILES=(
  "azure_tts_usage.log|$DEV_TT/azure_tts_usage.log|.tunatale/azure_tts_usage.log"
  "llm_usage.log|$DEV_TT/llm_usage.log|.tunatale/llm_usage.log"
)

field() { echo "$1" | cut -d'|' -f"$2"; }
# Laptop-root mode: every laptop path becomes <root>/<the prod-relative path>,
# because the live instance's data dir mirrors the prod volume's layout.
LOCAL_ENV_FILE="${TT_LOCAL_ENV_FILE:-$BACKEND/.env}"
LOCAL_SERVER_PATTERN="${TT_LOCAL_SERVER_PATTERN:-uvicorn app.main}"
PARK_URL="${TT_PARK_URL:-}"
if [ -n "${TT_LAPTOP_ROOT:-}" ]; then
  # No namerefs: /bin/bash on macOS is 3.2, and nothing should depend on
  # which bash happens to be first on PATH.
  reroot() { echo "$(field "$1" 1)|$TT_LAPTOP_ROOT/$(field "$1" 3)|$(field "$1" 3)"; }
  _t=(); for e in "${SQLITE[@]}"; do _t+=("$(reroot "$e")"); done; SQLITE=("${_t[@]}")
  _t=(); for e in "${DIRS[@]}"; do _t+=("$(reroot "$e")"); done; DIRS=("${_t[@]}")
  _t=(); for e in "${SQLITE_UP[@]}"; do _t+=("$(reroot "$e")"); done; SQLITE_UP=("${_t[@]}")
  _t=(); for e in "${DIRS_UP[@]}"; do _t+=("$(reroot "$e")"); done; DIRS_UP=("${_t[@]}")
  _t=(); for e in "${FILES[@]}"; do _t+=("$(reroot "$e")"); done; FILES=("${_t[@]}")
  DEV_TT="$TT_LAPTOP_ROOT/.tunatale"
fi

die() { echo "data-transfer: $*" >&2; exit 1; }
ssh_box() { ssh -i "$SSH_KEY" -o BatchMode=yes -o ConnectTimeout=15 "${USER_AT}${HOST}" "$@"; }
compose() { ssh_box "cd $REMOTE_DIR && sudo docker compose $*"; }
RSYNC_REMOTE=(rsync -a --rsync-path="sudo rsync" -e "ssh -i $SSH_KEY -o BatchMode=yes")

usage() { sed -n '2,24p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

preflight() {
  [ -n "$USER_AT" ] || die "set TT_DEPLOY_USER (see docs/deployment.md § Connecting)"
  ssh_box true 2>/dev/null || die "cannot ssh to ${USER_AT}${HOST} — is Tailscale up? (tailscale status)"
  VOL="$(ssh_box "sudo docker volume inspect $VOLUME -f '{{.Mountpoint}}'")" || die "no volume $VOLUME on $HOST"
  OWNER="$(ssh_box "sudo stat -c %u:%g '$VOL'")"
  scp -q -i "$SSH_KEY" -o BatchMode=yes "$HELPER" "${USER_AT}${HOST}:$REMOTE_DIR/data_snapshot.py"
}

dev_server_running() { pgrep -f "$LOCAL_SERVER_PATTERN" >/dev/null 2>&1; }
# Desktop Anki, not a TunaTale process: its media folder is part of the source.
anki_running() { pgrep -x Anki >/dev/null 2>&1 || pgrep -f "Anki.app/Contents/MacOS" >/dev/null 2>&1; }

# Fills STATS_ARGS for one side. $1 = dev|prod, $2 = where the sqlite files are
# (a snapshot dir, or "live" for their real locations), $3 = up|down: whether
# the to-prod-only media-sync pair is part of this comparison.
stats_args() {
  local side="$1" where="$2" dir="${3:-up}" e p
  local sq=("${SQLITE[@]}") ds=("${DIRS[@]}")
  if [ "$dir" = up ]; then sq+=("${SQLITE_UP[@]}"); ds+=("${DIRS_UP[@]}"); fi
  STATS_ARGS=()
  for e in "${sq[@]}"; do
    if [ "$where" != live ]; then p="$where/$(field "$e" 1)"
    elif [ "$side" = dev ]; then p="$(field "$e" 2)"
    else p="$VOL/$(field "$e" 3)"; fi
    STATS_ARGS+=(--db "$(field "$e" 1)=$p")
  done
  for e in "${ds[@]}"; do
    if [ "$side" = dev ]; then STATS_ARGS+=(--dir "$(field "$e" 1)=$(field "$e" 2)")
    else STATS_ARGS+=(--dir "$(field "$e" 1)=$VOL/$(field "$e" 3)"); fi
  done
}

dev_stats() {  # $1 = out json, $2 = sqlite location, $3 = up|down
  stats_args dev "$2" "${3:-up}"
  (cd "$BACKEND" && uv run --quiet python "$HELPER" stats "${STATS_ARGS[@]}" --out "$1")
}

prod_stats() {  # $1 = local out json, $2 = sqlite location on the box, $3 = up|down
  stats_args prod "$2" "${3:-up}"
  ssh_box "sudo python3 $REMOTE_DIR/data_snapshot.py stats $(printf '%q ' "${STATS_ARGS[@]}") --out /tmp/tt-stats.json && cat /tmp/tt-stats.json" > "$1"
}

set_sync() {  # $1 = dev|prod, $2 = true|false — rewrites SYNC_ENABLED in that side's backend/.env
  local line="SYNC_ENABLED=$2"
  if [ "$1" = prod ]; then
    ssh_box "cd $REMOTE_DIR/backend && sed -i -E '/^[Ss][Yy][Nn][Cc]_[Ee][Nn][Aa][Bb][Ll][Ee][Dd]=/d' .env && echo '$line' >> .env"
  else
    touch "$LOCAL_ENV_FILE"
    sed -i '' -E '/^[Ss][Yy][Nn][Cc]_[Ee][Nn][Aa][Bb][Ll][Ee][Dd]=/d' "$LOCAL_ENV_FILE" && echo "$line" >> "$LOCAL_ENV_FILE"
  fi
  echo "    $1: $line"
}

set_park() {  # $1 = URL to park prod at, or "" to unpark. Takes effect at the next api start.
  if [ -n "$1" ]; then
    ssh_box "cd $REMOTE_DIR/backend && sed -i -E '/^PARKED_AT=/d' .env && echo 'PARKED_AT=$1' >> .env"
    echo "    prod: parked at $1"
  else
    ssh_box "cd $REMOTE_DIR/backend && sed -i -E '/^PARKED_AT=/d' .env"
    echo "    prod: unparked"
  fi
}

wait_healthy() {
  ssh_box "
    for i in \$(seq 1 60); do
      s=\$(sudo docker inspect -f '{{.State.Health.Status}}' \$(sudo docker compose -f $REMOTE_DIR/docker-compose.yml ps -q api) 2>/dev/null)
      [ \"\$s\" = healthy ] && { echo '    api healthy'; exit 0; }
      sleep 3
    done; echo '    api NOT healthy after 180s'; exit 1"
}

status() {
  local work; work="$(mktemp -d)"
  echo "==> counting dev (this Mac)"; dev_stats "$work/dev.json" live
  echo "==> counting prod ($HOST)";    prod_stats "$work/prod.json" live
  echo "==> dev vs prod (MISMATCH lines are what a transfer would change)"
  (cd "$BACKEND" && uv run --quiet python "$HELPER" compare "$work/dev.json" "$work/prod.json") || true
}

to_prod() {
  local stage="$DEV_TT/transfer/$TS" bk="$VOL/transfer-backups/$TS" e
  echo "==> snapshotting dev databases into $stage"
  for e in "${SQLITE[@]}" "${SQLITE_UP[@]}"; do
    (cd "$BACKEND" && uv run --quiet python "$HELPER" snapshot "$(field "$e" 2)" "$stage/$(field "$e" 1)")
  done
  dev_stats "$stage/source.json" "$stage"

  echo "==> stopping the prod api"; compose stop api
  echo "==> saving prod's current state to $bk"
  # As ROOT throughout: the volume's mountpoint is not traversable by the login
  # user, and the first live run's non-sudo `cd` failed while a trailing `true`
  # hid it — the "undo material" it then advertised did not exist. Now any
  # failure here stops the transfer before a single file is overwritten.
  ssh_box "sudo sh -c 'set -e; mkdir -p \"$bk\"; cd \"$VOL\"; n=0
    for f in tunatale_no.db* tunatale_sl.db* tunatale_tl.db* .tunatale/tt_collection.anki2* .tunatale/tt_collection.media.db2* .tunatale/*_usage.log; do
      if [ -e \"\$f\" ]; then cp -a --parents \"\$f\" \"$bk/\"; n=\$((n+1)); fi
    done; echo \"    saved \$n files\"'" || die "could not save prod's current state — nothing was overwritten"

  echo "==> copying databases"
  for e in "${SQLITE[@]}" "${SQLITE_UP[@]}"; do
    ssh_box "sudo rm -f '$VOL/$(field "$e" 3)-wal' '$VOL/$(field "$e" 3)-shm'"
    "${RSYNC_REMOTE[@]}" "$stage/$(field "$e" 1)" "${USER_AT}${HOST}:$VOL/$(field "$e" 3)"
  done
  echo "==> copying directories"
  for e in "${DIRS[@]}" "${DIRS_UP[@]}"; do
    local src; src="$(field "$e" 2)"
    [ -d "$src" ] || { echo "    skip $(field "$e" 1): not on dev"; continue; }
    ssh_box "sudo mkdir -p '$VOL/$(field "$e" 3)'"
    "${RSYNC_REMOTE[@]}" --delete --backup --backup-dir="$bk/$(field "$e" 1)" "$src/" "${USER_AT}${HOST}:$VOL/$(field "$e" 3)/"
    echo "    $(field "$e" 1)"
  done
  for e in "${FILES[@]}"; do
    [ -f "$(field "$e" 2)" ] && "${RSYNC_REMOTE[@]}" "$(field "$e" 2)" "${USER_AT}${HOST}:$VOL/$(field "$e" 3)"
  done
  ssh_box "sudo chown -R $OWNER '$VOL/tunatale_no.db' '$VOL/tunatale_sl.db' '$VOL/tunatale_tl.db' '$VOL/media' '$VOL/output' '$VOL/.tunatale'"

  echo "==> verifying (before the app touches anything)"
  prod_stats "$stage/dest.json" live
  (cd "$BACKEND" && uv run --quiet python "$HELPER" compare "$stage/source.json" "$stage/dest.json") \
    || die "verification FAILED — prod api left STOPPED; prod's previous state is in $bk"

  echo "==> handing AnkiWeb sync to prod"
  set_sync prod true; set_sync dev false; set_park ""
  echo "==> starting the prod api"; compose up -d api; wait_healthy
  echo "==> done. prod is the live TunaTale; dev has sync OFF. Undo material: $HOST:$bk"
}

to_dev() {
  local stage="$DEV_TT/transfer/$TS" out="$VOL/transfer-out/$TS" bk="$DEV_TT/transfer-backups/$TS" e
  echo "==> stopping the prod api (freezes the source)"; compose stop api
  echo "==> snapshotting prod databases"
  for e in "${SQLITE[@]}"; do
    ssh_box "sudo python3 $REMOTE_DIR/data_snapshot.py snapshot '$VOL/$(field "$e" 3)' '$out/$(field "$e" 1)'"
  done
  mkdir -p "$stage"
  prod_stats "$stage/source.json" "$out" down

  for e in "${SQLITE[@]}" "${DIRS[@]}" "${FILES[@]}"; do
    [ -L "$(field "$e" 2)" ] && die "refusing to write through a symlink: $(field "$e" 2)"
  done
  echo "==> saving dev's current state to $bk"
  mkdir -p "$bk"
  for e in "${SQLITE[@]}" "${FILES[@]}"; do
    local f; f="$(field "$e" 2)"
    for g in "$f" "$f-wal" "$f-shm"; do [ -e "$g" ] && cp -p "$g" "$bk/"; done
  done

  echo "==> copying databases"
  for e in "${SQLITE[@]}"; do
    rm -f "$(field "$e" 2)-wal" "$(field "$e" 2)-shm"
    "${RSYNC_REMOTE[@]}" "${USER_AT}${HOST}:$out/$(field "$e" 1)" "$(field "$e" 2)"
  done
  echo "==> copying directories"
  for e in "${DIRS[@]}"; do
    mkdir -p "$(field "$e" 2)"
    "${RSYNC_REMOTE[@]}" --delete --backup --backup-dir="$bk/$(field "$e" 1)" "${USER_AT}${HOST}:$VOL/$(field "$e" 3)/" "$(field "$e" 2)/"
    echo "    $(field "$e" 1)"
  done
  for e in "${FILES[@]}"; do
    "${RSYNC_REMOTE[@]}" "${USER_AT}${HOST}:$VOL/$(field "$e" 3)" "$(field "$e" 2)" 2>/dev/null || true
  done
  ssh_box "sudo rm -rf '$out'"

  echo "==> verifying"
  dev_stats "$stage/dest.json" live down
  (cd "$BACKEND" && uv run --quiet python "$HELPER" compare "$stage/source.json" "$stage/dest.json") \
    || die "verification FAILED — dev's previous state is in $bk; prod api left STOPPED"

  echo "==> handing AnkiWeb sync to dev"
  set_sync prod false; set_sync dev true
  if [ -n "$PARK_URL" ]; then set_park "$PARK_URL"
  else echo "    prod: NOT parked (no TT_PARK_URL) — it will still serve the stale copy"; fi
  echo "==> restarting the prod api (sync OFF — do not study there until you transfer back)"
  compose up -d api; wait_healthy
  echo "==> done. dev is the live TunaTale. Undo material: $bk"
}

case "${1:-}" in
  status) preflight; status ;;
  to-prod|to-dev)
    preflight
    if [ "${2:-}" != "--apply" ]; then
      status
      echo; echo "DRY RUN — nothing changed. Re-run with --apply to perform '$1'."
      exit 0
    fi
    dev_server_running && die "the dev server is running — stop it first (it would write after the snapshot, or on top of the copy)"
    anki_running && die "desktop Anki is running — quit it first (its media folder is part of what moves)"
    if [ "$1" = to-prod ]; then to_prod; else to_dev; fi
    ;;
  -h|--help|"") usage 0 ;;
  *) usage 1 ;;
esac
