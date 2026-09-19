"""Single-flight for the routes that MINT content.

bd tunatale-rwkz.1. A create route that mints an id has no natural way to refuse
a duplicate: every call is a valid request for a new thing. So the caller says
which calls are the SAME intent, with an ``Idempotency-Key`` header, and this
module makes the second one join the first instead of starting another.

⚠️ THE SHIELD IS THE POINT, not an optimisation. The work runs in its own task
and every caller awaits it through :func:`asyncio.shield`, so a client that
disconnects mid-request — the 2026-09-19 case, where a phone dropped a 2.5-minute
import — neither cancels the work nor loses it. The result stays here, and the
retry that arrives afterwards is handed the session the first attempt created
rather than creating a second one.

⚠️ IN MEMORY AND DELIBERATELY NOT A TABLE, the same reasoning
``app.api.review_sessions._renders_in_flight`` records: the marker's honest
lifetime is the window in which a retry can still arrive, and a restart ends
every in-flight request anyway. A marker that outlived the process would promise
a result no one can still produce.

⚠️ FAILURES ARE NOT MEMOISED. The entry is dropped as soon as its task fails, so
a learner whose generation died on a 429 can retry with the same key and really
retry. Memoising the failure would hand them that 429 forever — a wedged button
is a worse bug than the duplicate this module exists to prevent.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Request

# Long enough to cover a slow generation plus a learner noticing nothing happened
# and tapping again; short enough that a key reused hours later is a new intent.
# The 2026-09-19 duplicate arrived 75 s after its twin.
_TTL_SECONDS = 15 * 60


class _Entry:
    """One in-flight or recently-finished attempt."""

    __slots__ = ("created", "task")

    def __init__(self, task: asyncio.Task, created: float) -> None:
        self.task = task
        self.created = created


def _registry(app) -> dict[tuple[str, str, str], _Entry]:
    """The per-process key registry, created lazily on ``app.state``.

    Lazy rather than lifespan-initialised because the tests set ``app.state.*``
    by hand and never run the lifespan — the same reason ``_renders_in_flight``
    gives, and the same AttributeError it avoids.
    """
    registry = getattr(app.state, "idempotent_writes", None)
    if registry is None:
        registry = {}
        app.state.idempotent_writes = registry
    return registry


def _evict_expired(registry: dict[tuple[str, str, str], _Entry], now: float) -> None:
    """Drop finished entries past the TTL. In-flight entries are never evicted:
    a key whose work is still running is exactly the one a retry needs."""
    for key, entry in list(registry.items()):
        if entry.task.done() and now - entry.created > _TTL_SECONDS:
            del registry[key]


def _forget_if_it_failed(registry: dict[tuple[str, str, str], _Entry], key: tuple[str, str, str], task) -> None:
    # `task.exception()` itself raises on a cancelled task, so cancellation is
    # short-circuited first. Both outcomes mean the same thing here: nothing was
    # produced, so the key must not be held.
    if task.cancelled() or task.exception() is not None:
        registry.pop(key, None)


async def once(
    request: Request,
    scope: str,
    key: str | None,
    work: Callable[[], Awaitable[Any]],
) -> Any:
    """Run *work*, or join the attempt already running under the same key.

    *scope* separates routes that could legitimately share a key — an import and
    a generation are two different intents, and collapsing them would hand the
    paster a generated story. The language is part of the key for the same
    reason ``list_review_sessions`` is scoped by it: the wrong-deck failure has
    no visible symptom.

    No key means no deduplication. That is not a gap: a caller that sends none is
    asking for the behaviour every one of these routes had before, and inventing
    a key here (from the body, say) would collapse two genuine imports of the
    same text.
    """
    if not key:
        return await work()

    registry = _registry(request.app)
    now = time.monotonic()
    _evict_expired(registry, now)

    registry_key = (scope, getattr(request.state, "language_code", ""), key)
    entry = registry.get(registry_key)
    if entry is None:
        task = asyncio.create_task(work())
        registry[registry_key] = _Entry(task, now)
        task.add_done_callback(lambda t: _forget_if_it_failed(registry, registry_key, t))
        entry = registry[registry_key]

    return await asyncio.shield(entry.task)
