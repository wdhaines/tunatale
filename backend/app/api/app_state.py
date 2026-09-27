"""The one module that reads ``app.state`` defensively (tunatale-ux66.4 item 3).

Every OPTIONAL resource is read through an accessor named after its attribute,
returning ``None`` when it is absent. ``None`` means "not configured" and callers
fail soft on it — ``llm(request) is None`` is "no LLM", not an error.

Three pieces of state are CREATED on first use instead: ``idempotent_writes``,
``review_renders`` and ``background_work``. Lazy rather than lifespan-initialised
because the tests set ``app.state.*`` by hand and never run the lifespan; a
lifespan-only initialiser would ``AttributeError`` under test.

``app/main.py`` still reads ``app.state`` directly for session lookup and the
language middleware's fallback; that is out of this module's scope.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import Request

if TYPE_CHECKING:
    from app.api.idempotency import _Entry
    from app.audio.renderer import LessonRenderer
    from app.auth.database import AuthDatabase
    from app.common.background_work import BackgroundWork
    from app.generation.pipeline import LessonPipeline
    from app.llm.client import LLMClient
    from app.srs.lemmatizer import Lemmatizer


def _optional(request: Request, name: str) -> Any:
    return getattr(request.app.state, name, None)


def activity_log(request: Request) -> Any:
    return _optional(request, "activity_log")


def audio_dir(request: Request) -> Path | None:
    return _optional(request, "audio_dir")


def auth_db(request: Request) -> AuthDatabase | None:
    return _optional(request, "auth_db")


def content_stores(request: Request) -> dict[str, Any] | None:
    return _optional(request, "content_stores")


def language(request: Request) -> Any:
    return _optional(request, "language")


def languages(request: Request) -> dict[str, Any] | None:
    return _optional(request, "languages")


def lemmatizer(request: Request) -> Lemmatizer | None:
    return _optional(request, "lemmatizer")


def llm(request: Request) -> LLMClient | None:
    return _optional(request, "llm")


def model_version(request: Request) -> str | None:
    return _optional(request, "model_version")


def pipeline(request: Request) -> LessonPipeline | None:
    return _optional(request, "pipeline")


def renderer(request: Request) -> LessonRenderer | None:
    return _optional(request, "renderer")


def user_dbs(request: Request) -> Any:
    return _optional(request, "user_dbs")


def idempotent_writes(app: Any) -> dict[tuple[str, int | None, str, str], _Entry]:
    """The per-process idempotency-key registry (see ``app.api.idempotency``)."""
    registry = getattr(app.state, "idempotent_writes", None)
    if registry is None:
        registry = {}
        app.state.idempotent_writes = registry
    return registry


def review_renders(app: Any) -> set[str]:
    """The ids of review sessions currently rendering.

    ⚠️ In memory, and deliberately NOT a table. The marker's honest lifetime is
    the render's, and the render lives in this process's event loop. A restart
    kills the render, so a marker that survived the restart would be a lie
    needing exactly the reconciliation ``app.api.review_sessions``' docstring
    says a session does not have.
    """
    renders = getattr(app.state, "review_renders", None)
    if renders is None:
        renders = set()
        app.state.review_renders = renders
    return renders


def background_work(app: Any) -> BackgroundWork:
    """The app's background-work tracker (see ``app.common.background_work``)."""
    from app.common.background_work import BackgroundWork

    work = getattr(app.state, "background_work", None)
    if work is None:
        work = BackgroundWork()
        app.state.background_work = work
    return work
