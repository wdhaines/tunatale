"""Self-host anki.syncserver subprocess fixture for peer-sync integration tests.

Exports a session-scoped ``selfhost_sync_server`` fixture that either reuses an
already-running server (when ``settings.sync_endpoint`` answers ``/sync/ping``)
or spawns a throwaway ``anki.syncserver`` via the same isolated ``uv run``
pattern as the driver / oracle subprocesses.

Usage in tests::

    @pytest.mark.peer_sync
    def test_foo(selfhost_sync_server):
        endpoint, username, password = selfhost_sync_server
        ...

Under ``--run-peer-sync``, an unstartable server **fails loudly** (never skips)
— same philosophy as ``tests/anki_oracle/harness_fixtures.py``.
"""

from __future__ import annotations

import os
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.config import settings
from app.plugins.anki_sync.sync_orchestrator import _anki_with_spec

_SYNC_USER1 = "tt-test:tt-test-pw"
_SYNC_USERNAME = "tt-test"
_SYNC_PASSWORD = "tt-test-pw"
_SERVER_POLL_INTERVAL_S = 0.5
_SERVER_PING_TIMEOUT_S = 5
# The first isolated ``uv run --with anki`` may build the ephemeral env from a
# cold cache; give the subprocess headroom. Matches the driver and oracle retry
# windows (t/anki_oracle/harness_fixtures.py _ORACLE_MAX_ATTEMPTS × _ORACLE_RETRY_SLEEP).
_COLD_BUILD_TIMEOUT_S = 180


def find_free_port() -> int:
    """Return an ephemeral port that is free right now."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def server_cmd() -> list[str]:
    """Build the ``uv run … python -m anki.syncserver`` command.

    Uses the same isolated subprocess pattern and ``--with`` spec as the
    driver subprocess (``sync_orchestrator._driver_cmd``).
    """
    return [
        "uv",
        "run",
        "--isolated",
        "--no-project",
        "--python",
        settings.anki_subprocess_python,
        "--with",
        _anki_with_spec(),
        "python",
        "-m",
        "anki.syncserver",
    ]


def server_env(port: int, base: Path) -> dict[str, str]:
    """Build environment for the sync server subprocess."""
    return {
        **os.environ,
        "SYNC_USER1": _SYNC_USER1,
        "SYNC_HOST": "127.0.0.1",
        "SYNC_PORT": str(port),
        "SYNC_BASE": str(base),
        "QT_QPA_PLATFORM": "offscreen",
    }


def ping(endpoint: str) -> bool:
    """Check if the sync server at *endpoint* answers ``/sync/ping``."""
    url = f"{endpoint.rstrip('/')}/sync/ping"
    try:
        r = httpx.get(url, timeout=_SERVER_PING_TIMEOUT_S)
        return r.status_code < 500
    except httpx.ConnectError, httpx.TimeoutException:
        return False


@pytest.fixture(scope="session")
def selfhost_sync_server(tmp_path_factory: pytest.TempPathFactory) -> Any:  # noqa: ANN401
    """Session-scoped fixture: auto-start a throwaway anki.syncserver.

    Precedence:
    1. If **settings.sync_endpoint** is already set AND ``/sync/ping``
       answers → reuse it (preserves the manual two-terminal workflow).
    2. Otherwise, spawn a fresh ``anki.syncserver`` subprocess on a free
       port with throwaway credentials ``tt-test`` / ``tt-test-pw`` and a
       temporary ``SYNC_BASE``.

    Yields ``(sync_endpoint, sync_username, sync_password)``.

    Under ``--run-peer-sync``, an unstartable server fails loudly via
    ``pytest.fail`` — never a silent skip (same philosophy as the oracle
    harness at ``tests/anki_oracle/harness_fixtures.py``).

    Teardown: ``terminate()`` → ``wait(10)`` → ``kill()``.
    """
    # --- Reuse decision ---
    if settings.sync_endpoint and ping(settings.sync_endpoint):
        yield (settings.sync_endpoint, settings.sync_username, settings.sync_password)
        return

    # --- Spawn ---
    port = find_free_port()
    base = tmp_path_factory.mktemp("sync-base")
    endpoint = f"http://127.0.0.1:{port}"

    stderr_path = base / "server.log"
    stderr_fh = open(stderr_path, "w")  # noqa: SIM115 — held by Popen for session duration
    proc = subprocess.Popen(
        server_cmd(),
        env=server_env(port, base),
        stdout=subprocess.DEVNULL,
        stderr=stderr_fh,
    )

    deadline = time.monotonic() + _COLD_BUILD_TIMEOUT_S
    while time.monotonic() < deadline:
        if ping(endpoint):
            break
        time.sleep(_SERVER_POLL_INTERVAL_S)
    else:
        proc.kill()
        proc.wait()
        stderr_fh.close()
        pytest.fail(
            f"Sync server did not answer at {endpoint} within "
            f"{_COLD_BUILD_TIMEOUT_S}s.\nServer stderr:\n{stderr_path.read_text()}"
        )

    try:
        yield (endpoint, _SYNC_USERNAME, _SYNC_PASSWORD)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        stderr_fh.close()
