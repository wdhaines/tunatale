"""serve-ca.sh hands a phone the mkcert root CA from one URL, and nothing else.

The phone-setup hint start-dev.sh used to print was
``python3 -m http.server 8080 -d "$(mkcert -CAROOT)"``. That directory also holds
rootCA-key.pem, the CA's private key, so the hint served the key to anything that
could reach the port — and whoever holds it can mint certificates that every
device trusting this CA accepts, for any site.

serve-ca.sh copies the public certificate into a fresh directory and serves only
that. These tests run the real script against a throwaway CAROOT that holds a
key, and fetch over HTTP the way the phone does.
"""

import os
import signal
import socket
import struct
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "serve-ca.sh"


@pytest.fixture
def caroot(tmp_path: Path) -> Path:
    root = tmp_path / "caroot"
    root.mkdir()
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=test-ca",
            "-keyout",
            str(root / "rootCA-key.pem"),
            "-out",
            str(root / "rootCA.pem"),
        ],
        capture_output=True,
        check=True,
    )
    assert b"PRIVATE KEY" in (root / "rootCA-key.pem").read_bytes()
    return root


def _fingerprint(pem: Path) -> str:
    out = subprocess.run(
        ["openssl", "x509", "-in", str(pem), "-noout", "-fingerprint", "-sha256"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return out.strip().split("=", 1)[1]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def served(caroot: Path):
    port = _free_port()
    env = {**os.environ, "CAROOT": str(caroot), "PORT": str(port), "TS_HOST": "mac.tailnet.ts.net"}
    proc = subprocess.Popen(
        ["/bin/bash", str(SCRIPT)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
        # A job started in the background inherits SIGINT as ignored, and an
        # ignored signal survives exec, so under ./test.sh Ctrl+C would never
        # arrive. Restore the default a terminal's foreground job has.
        preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_DFL),
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 10
    while True:
        try:
            urllib.request.urlopen(base + "/", timeout=1).close()
            break
        except OSError:
            if proc.poll() is not None or time.monotonic() > deadline:
                os.killpg(proc.pid, signal.SIGKILL)
                pytest.fail(f"serve-ca.sh never served: {proc.communicate()[0]}")
            time.sleep(0.1)
    yield base, proc
    if proc.poll() is None:
        os.killpg(proc.pid, signal.SIGKILL)
    proc.communicate()


def _get(url: str) -> tuple[int, str, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status, r.headers.get_content_type(), r.read()
    except urllib.error.HTTPError as e:
        with e:
            return e.code, "", e.read()


def test_the_private_key_is_unreachable(served):
    base, _ = served
    for path in ("/rootCA-key.pem", "/rootCA.pem", "/caroot/rootCA-key.pem", "/../caroot/rootCA-key.pem"):
        status, _, body = _get(base + path)
        assert status == 404, (path, status)
        assert b"PRIVATE KEY" not in body


def test_nothing_served_contains_key_material(served, caroot):
    base, _ = served
    status, _, index = _get(base + "/")
    assert status == 200
    for name in ("tunatale-ca.crt", "tunatale-ca.pem"):
        status, _, body = _get(f"{base}/{name}")
        assert status == 200, name
        assert body == (caroot / "rootCA.pem").read_bytes(), name
        assert b"PRIVATE KEY" not in body
    assert b"PRIVATE KEY" not in index


def test_each_phone_gets_the_content_type_it_needs(served):
    # iOS Safari offers to install a profile only for application/x-x509-ca-cert.
    # Android 11+ Chrome hands that type to a system dialog that refuses CA certs
    # and saves nothing, so the Android link must download as a plain file.
    base, _ = served
    assert _get(base + "/tunatale-ca.crt")[1] == "application/x-x509-ca-cert"
    assert _get(base + "/tunatale-ca.pem")[1] == "application/octet-stream"
    assert _get(base + "/")[1] == "text/html"


def test_the_page_links_both_files_and_shows_the_fingerprint(served, caroot):
    base, _ = served
    page = _get(base + "/")[2].decode()
    assert 'href="tunatale-ca.pem"' in page
    assert 'href="tunatale-ca.crt"' in page
    assert _fingerprint(caroot / "rootCA.pem") in page


def test_it_prints_the_url_to_open_on_the_phone(served):
    _, proc = served
    os.killpg(proc.pid, signal.SIGINT)
    out = proc.communicate(timeout=10)[0]
    assert "http://mac.tailnet.ts.net:" in out
    assert "Traceback" not in out


def test_a_phone_dropping_a_spare_connection_prints_no_traceback(served):
    # Phones open a connection ahead of time and reset it unused. Measured
    # 2026-10-02 from an Android phone: Python's server printed a full
    # ConnectionResetError traceback for it, which reads as a failure.
    base, proc = served
    port = int(base.rsplit(":", 1)[1])
    with socket.create_connection(("127.0.0.1", port)) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        time.sleep(0.5)  # let the server accept it and block reading, as it was for the phone
    # The reset is sent at close, and the handler thread reports it a moment later;
    # SIGINT any sooner and the server exits first, hiding it (measured: clean with
    # no wait, the traceback 3/3 at 0.3 s).
    time.sleep(1)
    assert _get(base + "/")[0] == 200  # still serving after the reset
    os.killpg(proc.pid, signal.SIGINT)
    out = proc.communicate(timeout=10)[0]
    assert "Traceback" not in out, out
    assert "ConnectionResetError" not in out, out


def test_a_caroot_without_a_cert_is_refused(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    env = {**os.environ, "CAROOT": str(empty), "PORT": str(_free_port()), "TS_HOST": "x"}
    r = subprocess.run(["/bin/bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=10)
    assert r.returncode != 0
    assert "rootCA.pem" in r.stderr


def test_start_dev_no_longer_serves_the_caroot():
    text = (REPO / "start-dev.sh").read_text()
    assert "http.server" not in text
    assert "serve-ca.sh" in text
