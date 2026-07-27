#!/bin/bash

# Start both backend and frontend servers for local development

# Frontend mode: "dev" (vite dev + HMR, default) or "prod" (vite build + preview).
# Prod mode is required for the offline-audio service worker to activate — HMR and
# service workers conflict, so the SW only registers against a production build.
# Use it when testing offline playback on the phone:  ./start-dev.sh --prod
FRONTEND_MODE="dev"
if [ "$1" = "--prod" ]; then
    FRONTEND_MODE="prod"
fi

echo "Starting TunaTale (frontend: $FRONTEND_MODE)..."
echo ""

# Check if uv is installed
if ! command -v uv &> /dev/null; then
    echo "Error: uv not found. Install it: https://docs.astral.sh/uv/getting-started/installation/"
    exit 1
fi

# Check if frontend dependencies are installed
if [ ! -d "frontend/node_modules" ]; then
    echo "Error: Frontend dependencies not installed. Please run:"
    echo "  cd frontend && bun install"
    exit 1
fi

BACKEND_PID=""
FRONTEND_PID=""

# ── Detect Tailscale hostname ──────────────────────────────────────────────
TS_BIN="$(command -v tailscale 2>/dev/null)"
[ -z "$TS_BIN" ] && [ -x "/Applications/Tailscale.app/Contents/MacOS/Tailscale" ] \
    && TS_BIN="/Applications/Tailscale.app/Contents/MacOS/Tailscale"
TS_HOST=""
if [ -n "$TS_BIN" ]; then
    TS_HOST="$("$TS_BIN" status --json 2>/dev/null \
        | python3 -c "import sys,json; print(json.load(sys.stdin).get('Self',{}).get('DNSName','').rstrip('.'))" 2>/dev/null)"
    [ -z "$TS_HOST" ] && TS_HOST="$("$TS_BIN" ip -4 2>/dev/null | head -1)"
fi

# ── Generate / validate TLS certs ──────────────────────────────────────────
# (Re-)generate certs if the Tailscale hostname isn't already listed as a SAN.
# The keyfile is mode 600; this is fast (< 1 s) and ensures the cert always
# covers localhost + the current Tailscale MagicDNS name so phones on the
# tailnet can connect without a hostname mismatch.
CERT_SAN_HASH_FILE="certs/.generated_sans"
NEED_REGEN=1
if [ -f "$CERT_SAN_HASH_FILE" ] && [ -f certs/localhost.pem ]; then
    PREV="$(cat "$CERT_SAN_HASH_FILE")"
    [ "$PREV" = "$TS_HOST" ] && NEED_REGEN=0
fi
if [ "$NEED_REGEN" = "1" ] && command -v mkcert &>/dev/null; then
    mkcert -key-file certs/localhost-key.pem -cert-file certs/localhost.pem \
        localhost 127.0.0.1 ::1 '*.ts.net' ${TS_HOST:+$TS_HOST} \
        2>/dev/null
    printf '%s' "$TS_HOST" > "$CERT_SAN_HASH_FILE"
    echo "✓ TLS cert regenerated for: localhost, *.ts.net${TS_HOST:+, $TS_HOST}"
fi

# Attempt to install the mkcert CA into the system trust store.
# This is needed for Node.js SSR fetches and browsers to trust the self-signed certs.
# If this fails (e.g. no sudo available), HTTPS still works — you'll just need to
# accept the security warning in your browser once.
if command -v mkcert &>/dev/null && ! security find-certificate -c "mkcert" /Library/Keychains/System.keychain &>/dev/null 2>&1; then
    osascript -e 'do shell script "mkcert -install 2>/dev/null" with administrator privileges' \
        2>/dev/null && echo "✓ mkcert CA installed in system trust store" \
        || echo "ℹ mkcert CA not added to system trust store (run 'sudo mkcert -install' manually if you want browsers to trust the cert)"
fi

# Node doesn't read the macOS keychain, so the system-trust install above isn't
# enough for SvelteKit's SSR fetches to the HTTPS backend. Point Node at the
# mkcert root CA instead — keeps TLS verification ON (unlike the
# NODE_TLS_REJECT_UNAUTHORIZED=0 hammer, which disables it for ALL requests).
MKCERT_CA="$(mkcert -CAROOT 2>/dev/null)/rootCA.pem"
if [ -f "$MKCERT_CA" ]; then
    export NODE_EXTRA_CA_CERTS="$MKCERT_CA"
fi

cleanup() {
    echo ""
    echo "Shutting down..."
    # Frontend: kill Vite child, then bun parent
    if [ -n "$FRONTEND_PID" ]; then
        pkill -P "$FRONTEND_PID" 2>/dev/null
        kill "$FRONTEND_PID" 2>/dev/null
    fi
    # Backend: SIGINT for clean Python shutdown (avoids semaphore leaks)
    if [ -n "$BACKEND_PID" ]; then
        pkill -INT -P "$BACKEND_PID" 2>/dev/null
        kill -INT "$BACKEND_PID" 2>/dev/null
    fi
    wait 2>/dev/null
    exit 0
}
trap cleanup INT TERM

# Start backend in background
echo "Starting backend API on https://localhost:8000..."
cd backend
# --reload-dir app: scope the file watcher to source only.
#
# History (2026-07-26): this dev server idled at ~60% of a core, one instance
# having burned 213 CPU-minutes. Cause was NOT the app — it was the reloader.
# We depend on plain `uvicorn`, and without `watchfiles` installed uvicorn
# silently falls back to StatReload, which os.stat()s every watched file every
# 0.25s instead of using FSEvents. Bare --reload watches the whole cwd, so that
# poll loop was stat-ing backend/media (331M, ~7.5k files) and backend/output
# (105M) four times a second, forever.
#
# Fixed on two axes, both measured as 60s CPU-time deltas at idle:
#   bare --reload,       StatReload  →  ~59%  of a core
#   --reload-dir app,    StatReload  →  ~2.7%
#   --reload-dir app,    watchfiles  →  ~0.0%  (0.03s CPU over 60s)
# watchfiles is now a dev dep (see backend/pyproject.toml), so the scoping below
# is belt-and-braces: it keeps the fallback cheap if watchfiles ever goes missing.
#
# Diagnostic signature if this regresses: the reload log line reads "StatReload
# detected changes" rather than "WatchFiles detected changes" — that means the
# dev group didn't install and you're back on the 0.25s polling path.
#
# Nothing is lost by scoping: uvicorn's default --reload-include is *.py, so only
# Python files ever triggered a reload anyway. tests/ and scripts/ edits no
# longer bounce the server, which is also what you want.
uv run uvicorn app.main:app --reload --reload-dir app --host 0.0.0.0 --port 8000 \
    --ssl-keyfile ../certs/localhost-key.pem \
    --ssl-certfile ../certs/localhost.pem \
    --log-level warning &
BACKEND_PID=$!
cd ..

# Give backend time to start
sleep 2

# Start frontend in background
echo "Starting frontend on https://localhost:5173..."
cd frontend
if [ "$FRONTEND_MODE" = "prod" ]; then
    # Build then serve the production bundle so the service worker activates.
    # Use the robust launcher (scripts/preview.mjs): plain `vite preview` crashes
    # when a stale service-worker client requests a hashed asset a newer build no
    # longer contains — common in this rebuild-often on-device loop.
    echo "Building production frontend (service worker enabled)..."
    VITE_SSL_ENABLED=true bun run build
    VITE_SSL_ENABLED=true bun run preview:robust --port 5173 &
else
    VITE_SSL_ENABLED=true bun run dev &
fi
FRONTEND_PID=$!
cd ..

# Wait for Vite to answer before printing the URL banner, so it lands AFTER
# (not buried under) the localhost/IP listing Vite prints asynchronously.
for _ in $(seq 1 40); do
    curl -sk -o /dev/null "https://localhost:5173/" 2>/dev/null && break
    sleep 0.5
done

echo ""
echo "Application started!"
echo ""
if [ -n "$TS_HOST" ]; then
    echo "  ➜ Phone (tailnet): https://${TS_HOST}:5173"
fi
echo "  Frontend:     https://localhost:5173"
echo "  Backend API:  https://localhost:8000"
echo "  API Docs:     https://localhost:8000/docs"

if [ -n "$TS_HOST" ]; then
    CA_DIR="$(mkcert -CAROOT 2>/dev/null)"
    if [ -n "$CA_DIR" ] && [ -f "$CA_DIR/rootCA.pem" ]; then
        echo ""
        echo "  To trust certs on Android:"
        echo "    Copy $CA_DIR/rootCA.pem to your phone, then:"
        echo "    Settings → Security → Install certificate → CA certificate"
        echo "    To serve the CA for download:"
        echo "    python3 -m http.server 8080 -d \"$CA_DIR\""
        echo "    Then visit http://${TS_HOST}:8080/rootCA.pem on your phone"
    fi
fi
echo ""
echo "Press Ctrl+C to stop all servers"
echo ""

# Wait for both processes (suppress job-death messages)
wait 2>/dev/null
