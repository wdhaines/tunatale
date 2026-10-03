#!/bin/bash

# Serve this Mac's mkcert root CA so a phone can install it from one URL.
#
# A phone must trust the mkcert CA before the live instance
# (https://<mac>.ts.net:5273) or the dev server (:5173) loads without a
# certificate warning. Run this, open the URL it prints on the phone (which must
# be on the tailnet), follow the page, then Ctrl+C.
#
# Only the PUBLIC certificate is served. The CAROOT directory also holds
# rootCA-key.pem, the CA's private key; whoever holds it can mint certificates
# that every device trusting this CA accepts, for any site. The hint start-dev.sh
# used to print served that whole directory with `python3 -m http.server -d`,
# key included. stage_ca copies the one public file into a fresh directory, and
# only that directory is served.
#
# Plain HTTP on purpose: the phone cannot verify HTTPS from this Mac until it has
# installed the very certificate it is downloading. The page shows the SHA-256
# fingerprint to compare after installing.
#
#   PORT=8080      port to serve on
#   CAROOT=<dir>   mkcert's own override; defaults to `mkcert -CAROOT`
#   TS_HOST=<name> the name to print; set it to skip Tailscale detection
set -euo pipefail

die() { echo "serve-ca: $*" >&2; exit 1; }

stage_ca() {  # <caroot> <dest>: copy ONLY the public root cert, and write the page
  local src="$1" dest="$2" fp
  [ -f "$src/rootCA.pem" ] || die "no rootCA.pem in $src (run: mkcert -install)"
  # Two names for one file, because the phones need different content types:
  # serve_dir maps .crt and .pem to them.
  cp "$src/rootCA.pem" "$dest/tunatale-ca.crt"
  cp "$src/rootCA.pem" "$dest/tunatale-ca.pem"
  fp="$(openssl x509 -in "$src/rootCA.pem" -noout -fingerprint -sha256 | cut -d= -f2)"
  cat > "$dest/index.html" <<EOF
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Trust TunaTale</title>
<style>
  body { font: 17px/1.5 system-ui, sans-serif; max-width: 36rem; margin: 0 auto; padding: 1rem; }
  a.button { display: block; padding: .8rem 1rem; margin: .5rem 0 1rem; border-radius: .5rem;
             background: #1f6feb; color: #fff; text-align: center; text-decoration: none; }
  code { word-break: break-all; }
</style>
</head>
<body>
<h1>Trust TunaTale on this phone</h1>
<p>TunaTale runs on William's Mac. Install its certificate once, and the app
opens without a security warning.</p>

<h2>Android</h2>
<a class="button" href="tunatale-ca.pem">Download the certificate</a>
<ol>
  <li>Open <b>Settings</b> and go to <b>Security &amp; privacy</b>, then
  <b>More security settings</b>, then <b>Encryption &amp; credentials</b>.
  (Search Settings for "CA certificate" if the names differ.)</li>
  <li>Tap <b>Install a certificate</b>, then <b>CA certificate</b>, then
  <b>Install anyway</b>.</li>
  <li>Choose <b>tunatale-ca.pem</b> from Downloads. Android asks for a screen
  lock if the phone has none.</li>
</ol>

<h2>iPhone</h2>
<a class="button" href="tunatale-ca.crt">Install the certificate</a>
<ol>
  <li>Open this page in <b>Safari</b>, tap the button, then <b>Allow</b>.</li>
  <li>Open <b>Settings</b>, tap <b>Profile Downloaded</b>, then <b>Install</b>.</li>
  <li>Go to <b>General</b>, then <b>About</b>, then <b>Certificate Trust
  Settings</b>, and switch it on. Skipping this step looks exactly like a bad
  certificate.</li>
</ol>

<h2>Check it</h2>
<p>The installed certificate's SHA-256 fingerprint should be:</p>
<p><code>$fp</code></p>
</body>
</html>
EOF
}

serve_dir() {  # <dir> <port>: serve the staged directory, with per-phone content types
  python3 - "$1" "$2" <<'PY'
import functools, http.server, sys

class Handler(http.server.SimpleHTTPRequestHandler):
    # iOS Safari offers to install a profile only for application/x-x509-ca-cert.
    # Android 11+ Chrome hands that type to a system dialog that refuses CA certs
    # and saves nothing, so the Android link downloads as a plain file instead.
    extensions_map = {
        ".html": "text/html",
        ".crt": "application/x-x509-ca-cert",
        ".pem": "application/octet-stream",
    }

class Server(http.server.ThreadingHTTPServer):
    # Phones open a connection ahead of time and reset it unused, and the stock
    # server prints a full traceback for each one. That is the client hanging
    # up, not a failure; anything else still reports.
    def handle_error(self, request, client_address):
        if not isinstance(sys.exc_info()[1], ConnectionError):
            super().handle_error(request, client_address)

server = Server(("", int(sys.argv[2])), functools.partial(Handler, directory=sys.argv[1]))
try:
    server.serve_forever()
except KeyboardInterrupt:
    pass
PY
}

ts_host() {  # the Mac's Tailscale DNS name, as start-dev.sh finds it
  local bin
  bin="$(command -v tailscale 2>/dev/null || true)"
  [ -z "$bin" ] && [ -x /Applications/Tailscale.app/Contents/MacOS/Tailscale ] \
    && bin=/Applications/Tailscale.app/Contents/MacOS/Tailscale
  [ -n "$bin" ] || return 0
  "$bin" status --json 2>/dev/null \
    | python3 -c "import sys,json; print(json.load(sys.stdin).get('Self',{}).get('DNSName','').rstrip('.'))" 2>/dev/null \
    || true
}

PORT="${PORT:-8080}"
if [ -z "${CAROOT:-}" ]; then
  command -v mkcert >/dev/null || die "mkcert not installed (brew install mkcert)"
  CAROOT="$(mkcert -CAROOT)"
fi
[ -n "${TS_HOST+set}" ] || TS_HOST="$(ts_host)"

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
stage_ca "$CAROOT" "$STAGE"

echo "Serving the TunaTale CA certificate (public cert only) on port $PORT."
echo "On the phone, with Tailscale connected, open:"
echo "  http://${TS_HOST:-<this-mac>.<tailnet>.ts.net}:$PORT/"
echo "Ctrl+C when the phone has it."
echo ""
serve_dir "$STAGE" "$PORT"
