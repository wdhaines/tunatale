"""Dependency checks behind ``GET /api/health``.

Separate from the route on purpose: every check takes its dependencies as
*arguments*, so the tests break a real connection or a real directory instead of
patching one. That is not stylistic — ``scripts/check_mock_boundaries.py``
polices ``patch("app.…")`` and ``monkeypatch.setattr("app.…", …)`` alike, and an
endpoint whose whole job is "fail when a real dependency is broken" would prove
nothing if its failure paths were mocked into existence.

Two invariants worth not re-deriving:

- **The body carries status only.** No paths, no versions, no counts, no
  language names. This route stays unauthenticated after Phase 1, and the
  obvious "helpful" addition — the exception message from a failed open — is
  exactly what leaks the filesystem layout.
- **An absent dependency is a failure, not a skip.** An app whose lifespan never
  ran has no connections; reporting that as healthy is the precise bug this
  endpoint exists to remove (an unmounted volume would read green).
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

OK = "ok"
FAIL = "fail"

STATUS_OK = "ok"
STATUS_UNHEALTHY = "unhealthy"

#: Per-check budget. The container healthcheck and the uptime monitor both poll
#: this route, so a hung dependency must report ``fail`` rather than hang the
#: caller. Overridable per call so tests can drive a real timeout quickly.
CHECK_TIMEOUT_S = 2.0

#: A key chosen to match nothing. Both probes are indexed lookups that return
#: ``None``, so their cost stays flat as the tables grow — a health check that
#: got slower with use would eventually trip the very monitor it feeds.
_PROBE_KEY = "__tunatale_healthcheck__"


def _probe_dir(path: Path) -> None:
    """Raise unless *path* is a directory that can actually be written to.

    ``os.access`` is not enough: it answers from the permission bits and reports
    success on a read-only mount. Creating and removing a real file is what
    distinguishes "the volume is mounted rw" from "the path exists".
    """
    if not path.is_dir():
        raise OSError("not a directory")
    fd, name = tempfile.mkstemp(dir=path, prefix=".tt-health-")
    os.close(fd)
    os.unlink(name)


async def _run(fn: Callable[[], Any], timeout: float) -> str:
    """Run a blocking probe off-loop under a bounded timeout; any failure is ``fail``.

    The probe goes through ``to_thread`` because every one of them is blocking
    (sqlite, filesystem). ``wait_for`` then cancels *the await*, not the thread —
    a genuinely wedged dependency leaks its worker thread while the endpoint
    still answers. That trade is deliberate: answering is what the monitor needs.
    """
    try:
        await asyncio.wait_for(asyncio.to_thread(fn), timeout=timeout)
    except Exception:
        # Deliberately broad, and deliberately silent: the exception's message
        # is the leak (it carries the DB path). asyncio.CancelledError is a
        # BaseException, so real shutdown still propagates.
        return FAIL
    return OK


async def _aggregate(targets: list[Any], probe: Callable[[Any], Any], timeout: float) -> str:
    """``ok`` only if there is at least one target and every one of them answers.

    Aggregated rather than reported per-language on purpose — a per-language key
    would publish the deployment's configured languages to an unauthenticated
    caller, and ``scripts/check_language_literals.py`` would reject the literals
    needed to name them anyway.
    """
    if not targets:
        return FAIL
    for target in targets:
        if await _run(lambda t=target: probe(t), timeout) == FAIL:
            return FAIL
    return OK


async def check_health(
    *,
    srs_dbs: list[Any],
    content_stores: list[Any],
    audio_dir: Path,
    media_dir: Path,
    timeout: float = CHECK_TIMEOUT_S,
) -> tuple[str, dict[str, str]]:
    """Return ``(status, checks)`` for the four fixed dependency names."""
    checks = {
        "database": await _aggregate(srs_dbs, lambda db: db.get_collocation_by_guid(_PROBE_KEY), timeout),
        "content_store": await _aggregate(content_stores, lambda s: s.get_curriculum(_PROBE_KEY), timeout),
        "audio_dir": await _run(lambda: _probe_dir(audio_dir), timeout),
        "media_dir": await _run(lambda: _probe_dir(media_dir), timeout),
    }
    status = STATUS_OK if all(v == OK for v in checks.values()) else STATUS_UNHEALTHY
    return status, checks
