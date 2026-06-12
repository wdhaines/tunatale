#!/bin/bash

# Start both backend and frontend servers for local development

echo "Starting TunaTale..."
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
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000 \
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
NODE_TLS_REJECT_UNAUTHORIZED=0 bun run dev &
FRONTEND_PID=$!
cd ..

echo ""
echo "Application started!"
echo ""
echo "  Backend API:  https://localhost:8000"
echo "  API Docs:     https://localhost:8000/docs"
echo "  Frontend:     https://localhost:5173"

if [ -n "$TS_HOST" ]; then
    echo "  Phone (tailnet): https://${TS_HOST}:5173"
    echo ""
    echo "  To trust certs on Android:"
    CA_DIR="$(mkcert -CAROOT 2>/dev/null)"
    if [ -n "$CA_DIR" ] && [ -f "$CA_DIR/rootCA.pem" ]; then
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
