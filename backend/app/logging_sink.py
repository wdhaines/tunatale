"""A durable file sink for WARNING, and a mirror that sends LLM failures to it.

bd tunatale-y0bk.6. Until this, every diagnostic signal on the generation path was
volatile. ``logger.warning`` went to the dev server's stdout on a tty —
``start-dev.sh`` runs uvicorn ``--log-level warning`` with no redirect — and
``GET /api/llm/activity`` is a 300-event in-memory ring that a ``--reload``
restart empties. On 2026-09-08 two review sessions were stored with zero hover
translations and the only trace was a warning nobody could read afterwards; by the
time the ring was queried it had already lost everything before 14:56.

What survived that day, and was decisive, was ``~/.tunatale/llm_usage.log``: file
backed, one line per request, kept precisely so it outlives a reload. This is the
same shape for warnings.

⚠️ TIMESTAMPS ARE UTC AND SAY SO. ``sync.log`` is local time and is the odd one
out — ``review_sessions.created_at``, ``media.created_at`` and the usage ledger
are all UTC. Reading a local-time line as UTC cost real confusion on 2026-09-08
(the box is EDT, so a 4-hour error lands you in the wrong burst of LLM calls
entirely), so every line here ends its timestamp with a literal ``Z``.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from logging.handlers import RotatingFileHandler
from pathlib import Path

#: Marks our handler on the root logger so a second install is a no-op.
#: ``uvicorn --reload`` re-runs the lifespan, and a duplicate handler would write
#: every line twice AND halve the effective rotation budget.
_HANDLER_NAME = "tunatale-warning-sink"

_MAX_BYTES = 5 * 1024 * 1024
_BACKUP_COUNT = 3

logger = logging.getLogger(__name__)


class _UtcFormatter(logging.Formatter):
    converter = time.gmtime

    def formatTime(self, record, datefmt=None):  # noqa: N802 — stdlib's spelling
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", self.converter(record.created))


def install_warning_file_handler(path: Path) -> RotatingFileHandler | None:
    """Attach a rotating WARNING sink at *path*; return it, or ``None`` on failure.

    Idempotent: installing twice returns the handler already attached.

    ⚠️ FAILS OPEN. A logging problem must never stop the app booting — the same
    contract ``rotate_db_backups`` has, and for the same reason: the thing that
    records trouble is not worth becoming trouble.

    WARNING and above only. ``main.py`` sets ``basicConfig(level=INFO)``, so a
    sink that took INFO would rotate away the warnings it exists to keep.
    """
    root = logging.getLogger()
    for existing in root.handlers:
        if getattr(existing, "name", None) != _HANDLER_NAME:
            continue
        # Same path → idempotent, which is the --reload case. DIFFERENT path →
        # replace, because "install at path" that quietly kept writing somewhere
        # else would be a lie. It is also what makes this testable: a suite that
        # has booted the real lifespan would otherwise pin every later caller to
        # the production log file.
        if Path(getattr(existing, "baseFilename", "")) == path.resolve():
            return existing  # type: ignore[return-value]
        root.removeHandler(existing)
        existing.close()
        break

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(path, maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8")
    except OSError as e:
        logger.warning("Warning-log sink unavailable at %s (%s); warnings stay in-process only", path, e)
        return None

    handler.name = _HANDLER_NAME
    handler.setLevel(logging.WARNING)
    handler.setFormatter(_UtcFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)
    return handler


def llm_failure_mirror(record_llm_call: Callable[[dict], None]) -> Callable[[dict], None]:
    """Wrap the activity-ring callback so a failed LLM call also reaches the sink.

    ⚠️ Wrapping the callback rather than adding log lines at each raise site is
    deliberate. ``LLMClient`` warns on SOME failure paths and not others — a 429
    retry and a fallback attempt log, but a hard failure with
    ``allow_fallback=False`` raises ``LLMError`` with nothing written. Every
    outcome passes through this one callback, so covering it here needs no audit
    of the raise sites and cannot drift out of date when one is added.

    The wrapped callback always runs: the ring buffer is the live UI's feed and
    must not lose events to this.
    """

    def _mirror(info: dict) -> None:
        record_llm_call(info)
        status = info.get("status")
        if status != "success":
            logger.warning(
                "LLM call failed: provider=%s status=%s latency_ms=%s error=%s",
                info.get("provider"),
                status,
                info.get("latency_ms"),
                info.get("error"),
            )

    return _mirror
