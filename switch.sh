#!/usr/bin/env bash
# Switch where you LEARN between the production box and this laptop (tunatale-qyw0).
#
#   ./switch.sh status              where TunaTale is live, and the laptop instance's state
#   ./switch.sh to-laptop [--apply] prod -> laptop: copy data down, park prod, start the laptop instance
#   ./switch.sh to-prod   [--apply] laptop -> prod: stop the laptop instance, copy data up, unpark prod
#   ./switch.sh start | stop        the laptop instance alone (e.g. after a reboot)
#   ./switch.sh update              after a deploy, while learning is ON THE LAPTOP: rebuild
#                                   the laptop instance at prod's new commit; moves no data
#   ./switch.sh prepare             check out and build prod's commit now, moving no data
#                                   (the first build takes minutes; to-laptop does it anyway)
#
# Without --apply the two switches are dry runs: they show what would move.
#
# The laptop instance is NOT the dev server. It is a separate checkout of the
# exact commit prod runs (~/TunaTaleLive/app), on its own data
# (~/TunaTaleLive/data, laid out like prod's volume) and its own ports
# (API 8100, web 5273), so learning never runs code that is mid-edit and the
# dev server's databases are never touched. The dev server can keep running.
#
# Needs prod to run a commit with TT_HOME support (tunatale-qyw0 or later):
# the laptop instance runs prod's commit, and build() refuses an older one.
#
# Before --apply: quit desktop Anki (the transfer refuses otherwise). Moving the
# data and handing AnkiWeb sync over is data-transfer.sh's job; this script
# decides WHERE the laptop side lives and parks the side you are leaving.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${TT_LIVE_ROOT:-$HOME/TunaTaleLive}"
APP="$ROOT/app"
DATA="$ROOT/data"
RUN="$ROOT/run"
LIVE_ENV="$ROOT/live.env"
API_PORT=8100
WEB_PORT=5273

HOST="${TT_DEPLOY_HOST:-tunatale}"
USER_AT="${TT_DEPLOY_USER:+${TT_DEPLOY_USER}@}"
SSH_KEY="${TT_DEPLOY_KEY:-$HOME/.ssh/google_compute_engine}"
REMOTE_DIR="${TT_DEPLOY_DIR:-/opt/tunatale}"

die() { echo "switch: $*" >&2; exit 1; }
ssh_box() { ssh -i "$SSH_KEY" -o BatchMode=yes -o ConnectTimeout=15 "${USER_AT}${HOST}" "$@"; }

ts_host() {  # the Mac's Tailscale DNS name, as start-dev.sh finds it
  local bin
  bin="$(command -v tailscale 2>/dev/null || true)"
  [ -z "$bin" ] && [ -x /Applications/Tailscale.app/Contents/MacOS/Tailscale ] \
    && bin=/Applications/Tailscale.app/Contents/MacOS/Tailscale
  [ -n "$bin" ] || die "Tailscale not found — the phone reaches the laptop over the tailnet"
  "$bin" status --json 2>/dev/null \
    | python3 -c "import sys,json; print(json.load(sys.stdin).get('Self',{}).get('DNSName','').rstrip('.'))"
}
laptop_url() { echo "https://$(ts_host):$WEB_PORT"; }

prod_ref() {  # the commit prod runs: the last tag deploy.sh recorded
  # TT_LIVE_REF overrides it, for testing on a throwaway TT_LIVE_ROOT only.
  if [ -n "${TT_LIVE_REF:-}" ]; then echo "$TT_LIVE_REF"; return 0; fi
  [ -n "$USER_AT" ] || die "set TT_DEPLOY_USER (see docs/deployment.md § Connecting)"
  local ref
  ref="$(ssh_box "awk -F'\t' 'NF>1 {tag=\$2} END {print tag}' $REMOTE_DIR/deploy-history.log")" \
    || die "cannot read prod's deployed commit — is Tailscale up?"
  [ -n "$ref" ] || die "prod's deploy-history.log names no commit"
  echo "$ref"
}

# The laptop is live when the last handover gave IT the AnkiWeb sync.
laptop_live() { grep -qx 'SYNC_ENABLED=true' "$LIVE_ENV" 2>/dev/null; }

pid_alive() { [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null; }
running() { pid_alive "$RUN/api.pid" || pid_alive "$RUN/web.pid"; }

build() {  # $1 = commit. Check out and build it in the instance's worktree, once per commit.
  local ref="$1"
  mkdir -p "$ROOT" "$RUN" "$DATA"
  git -C "$REPO" fetch -q origin
  if [ ! -d "$APP" ]; then
    echo "==> creating the laptop checkout at ${ref:0:10}"
    git -C "$REPO" worktree add -q --detach "$APP" "$ref"
  elif [ "$(git -C "$APP" rev-parse HEAD)" != "$(git -C "$REPO" rev-parse "$ref^{commit}")" ]; then
    echo "==> moving the laptop checkout to ${ref:0:10}"
    git -C "$APP" checkout -q --detach "$ref"
  fi
  # A commit from before TT_HOME would IGNORE it and write into the dev
  # server's ~/.tunatale, the one thing this instance exists to keep apart.
  grep -q "def tt_home" "$APP/backend/app/config.py" \
    || die "commit ${ref:0:10} predates TT_HOME, so it would share ~/.tunatale with the dev server — deploy a newer commit to prod first"
  local head; head="$(git -C "$APP" rev-parse HEAD)"
  if [ "$(cat "$RUN/built" 2>/dev/null)" = "$head" ]; then return 0; fi
  echo "==> building ${head:0:10} (once per commit)"
  # The keys (Groq, Azure, Pixabay) come from the dev .env; everything this
  # instance must NOT share with dev is overridden in start().
  ln -sfn "$REPO/backend/.env" "$APP/backend/.env"
  ln -sfn "$REPO/certs" "$APP/certs"
  # The same dependency set as the prod image (Dockerfile), plus its lemma table.
  (cd "$APP/backend" && uv sync -q --frozen --no-dev --no-group slovene --no-group norwegian --no-group alignment \
    && .venv/bin/python -m app.srs.lemma_table >/dev/null)
  (cd "$APP/frontend" && bun install --frozen-lockfile >/dev/null && bun run build >/dev/null)
  echo "$head" > "$RUN/built"
}

start() {
  [ -f "$LIVE_ENV" ] || die "no $LIVE_ENV — run ./switch.sh to-laptop --apply first"
  running && { echo "laptop instance already running"; return 0; }
  [ "$(cat "$RUN/built" 2>/dev/null)" = "$(git -C "$APP" rev-parse HEAD 2>/dev/null)" ] \
    || die "the laptop checkout is not built — run ./switch.sh to-laptop"
  # shellcheck disable=SC1090
  set -a; . "$LIVE_ENV"; set +a
  local port
  for port in "$API_PORT" "$WEB_PORT"; do
    # A taken port is a refusal, not a detail: vite would quietly move to the
    # next one and the phone's bookmark would reach whatever holds this one.
    if lsof -tiTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
      die "port $port is in use by pid $(lsof -tiTCP:"$port" -sTCP:LISTEN | head -1) — ./switch.sh stop, or find what holds it"
    fi
  done
  echo "==> starting the laptop instance (sync ${SYNC_ENABLED:-false})"
  # Half a start is worse than none: if anything below dies, take down what did start.
  trap 'stop >/dev/null 2>&1 || true' ERR EXIT
  # Each server is the backgrounded command ITSELF (env and nohup exec
  # through), so $! is the server's pid. Backgrounding a `cd && ...` list
  # instead records a wrapper shell, and `stop` then leaves the server running.
  cd "$APP/backend"
  env TT_HOME="$DATA/.tunatale" MEDIA_DIR="$DATA/media" AUDIO_DIR="$DATA/output/audio" \
    DATABASE_URL="sqlite:///$DATA/tunatale_sl.db" \
    DATABASE_URLS="{\"sl\": \"sqlite:///$DATA/tunatale_sl.db\", \"no\": \"sqlite:///$DATA/tunatale_no.db\"}" \
    AUTH_DATABASE_URL="sqlite:///$DATA/auth.db" LEMMATIZER_TYPE=table \
    SYNC_ENABLED="${SYNC_ENABLED:-false}" PARKED_AT="${PARKED_AT:-}" \
    nohup .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port "$API_PORT" \
      --ssl-keyfile ../certs/localhost-key.pem --ssl-certfile ../certs/localhost.pem \
      < /dev/null > "$RUN/api.log" 2>&1 &
  echo $! > "$RUN/api.pid"
  cd "$APP/frontend"
  env VITE_SSL_ENABLED=true API_PORT="$API_PORT" \
    nohup bun scripts/preview.mjs --port "$WEB_PORT" < /dev/null > "$RUN/web.log" 2>&1 &
  echo $! > "$RUN/web.pid"
  cd "$REPO"
  local i
  for i in $(seq 1 60); do
    curl -skf -o /dev/null "https://localhost:$API_PORT/api/health" && break
    pid_alive "$RUN/api.pid" || die "the API exited — see $RUN/api.log"
    sleep 1
  done
  curl -skf -o /dev/null "https://localhost:$API_PORT/api/health" || die "the API never became healthy — see $RUN/api.log"
  # And the page itself, on THIS port: preview.mjs runs with strictPort, so a
  # taken port kills it here instead of it drifting to the next one.
  for i in $(seq 1 30); do
    curl -skf -o /dev/null "https://localhost:$WEB_PORT/" && break
    pid_alive "$RUN/web.pid" || die "the web server exited — see $RUN/web.log"
    sleep 1
  done
  curl -skf -o /dev/null "https://localhost:$WEB_PORT/" || die "the web server never answered on $WEB_PORT — see $RUN/web.log"
  trap - ERR EXIT
  echo "    live at $(laptop_url)"
}

stop() {
  local f port i
  for f in "$RUN/web.pid" "$RUN/api.pid"; do
    if pid_alive "$f"; then kill "$(cat "$f")"; fi
    rm -f "$f"
  done
  # Stopped means the PORTS are free, not that a kill was sent: a leaked
  # server keeps answering the phone with whatever data it last had.
  for port in "$API_PORT" "$WEB_PORT"; do
    for i in $(seq 1 20); do
      lsof -tiTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1 || break
      sleep 0.5
    done
    if lsof -tiTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
      die "port $port is still held by pid $(lsof -tiTCP:"$port" -sTCP:LISTEN | head -1) after stop"
    fi
  done
  echo "==> laptop instance stopped"
}

seed_media_db() {
  # to-dev leaves the Anki media pair alone on the Mac (desktop Anki keeps the
  # FOLDER current; the app symlinks to it). The laptop instance still needs the
  # pair's DATABASE, so its first run starts from the one the dev side already
  # had, exactly as a plain to-dev would. Via SQLite's backup API, never cp.
  local src="$HOME/.tunatale/tt_collection.media.db2" dst="$DATA/.tunatale/tt_collection.media.db2"
  if [ -e "$dst" ] || [ ! -e "$src" ]; then return 0; fi
  mkdir -p "$(dirname "$dst")"
  (cd "$REPO/backend" && uv run --quiet python scripts/data_snapshot.py snapshot "$src" "$dst")
  echo "    seeded the Anki media database from ~/.tunatale"
}

transfer() {  # $1 = to-dev | to-prod, $2 = --apply or empty
  TT_LAPTOP_ROOT="$DATA" TT_LOCAL_ENV_FILE="$LIVE_ENV" \
    TT_LOCAL_SERVER_PATTERN="uvicorn app.main:app --host 0.0.0.0 --port $API_PORT" \
    TT_PARK_URL="$(laptop_url)" \
    "$REPO/data-transfer.sh" "$1" ${2:+"$2"}
}

status() {
  echo "laptop instance: $( running && echo "RUNNING at $(laptop_url)" || echo stopped )"
  [ -f "$LIVE_ENV" ] && echo "laptop sync:     $(grep -E '^SYNC_ENABLED=' "$LIVE_ENV" || echo unset)"
  [ -d "$APP" ] && echo "laptop commit:   $(git -C "$APP" rev-parse --short=10 HEAD)"
  if [ -n "$USER_AT" ] && ssh_box true 2>/dev/null; then
    echo "prod commit:     $(prod_ref | cut -c1-10)"
    echo "prod parked at:  $(ssh_box "grep -E '^PARKED_AT=' $REMOTE_DIR/backend/.env | cut -d= -f2-" || true)"
  else
    echo "prod:            unreachable (TT_DEPLOY_USER set? Tailscale up?)"
  fi
}

case "${1:-}" in
  status) status ;;
  prepare) build "$(prod_ref)" ;;
  update)
    laptop_live || die "learning is not on the laptop — nothing to update (switch with to-laptop)"
    ref="$(prod_ref)"
    running && stop
    build "$ref"
    start
    echo "==> the laptop instance now runs ${ref:0:10}; no data moved"
    ;;
  start) start ;;
  stop) stop ;;
  to-laptop)
    ref="$(prod_ref)"
    # Learning already on the laptop means prod holds the OLDER copy, and this
    # would copy it DOWN over everything studied since the last switch. After a
    # deploy, what is wanted is `update`. (Written the wrong way round in the
    # 2026-09-21 handoff and caught before it was run.)
    laptop_live && die "learning is already on the laptop — to-laptop would overwrite it with prod's older copy. After a deploy use: ./switch.sh update"
    [ "${2:-}" = --apply ] || { transfer to-dev; exit 0; }
    running && stop
    build "$ref"          # the SAME commit as prod, so the data's schema matches the code
    touch "$LIVE_ENV"
    seed_media_db
    transfer to-dev --apply
    start
    echo "==> learning is on the laptop: $(laptop_url)  (prod is parked and points there)"
    ;;
  to-prod)
    [ "${2:-}" = --apply ] || { transfer to-prod; exit 0; }
    running && stop
    transfer to-prod --apply
    echo "==> learning is on prod again (the laptop instance is stopped)"
    ;;
  -h|--help|"") sed -n '2,26p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' ;;
  *) die "unknown command: $1 (try --help)" ;;
esac
