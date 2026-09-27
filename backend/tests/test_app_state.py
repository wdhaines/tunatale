"""app.api.app_state: the one module that reads app.state defensively (tunatale-ux66.4)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.api import app_state
from app.common.background_work import BackgroundWork

_OPTIONAL = [
    "activity_log",
    "audio_dir",
    "auth_db",
    "content_stores",
    "language",
    "languages",
    "lemmatizer",
    "llm",
    "model_version",
    "pipeline",
    "renderer",
    "user_dbs",
]


def _request(**state) -> SimpleNamespace:
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(**state)))


@pytest.mark.parametrize("name", _OPTIONAL)
def test_optional_accessor_is_none_when_absent(name):
    assert getattr(app_state, name)(_request()) is None


@pytest.mark.parametrize("name", _OPTIONAL)
def test_optional_accessor_returns_the_seeded_object(name):
    sentinel = object()
    assert getattr(app_state, name)(_request(**{name: sentinel})) is sentinel


@pytest.mark.parametrize(
    ("name", "kind"),
    [("idempotent_writes", dict), ("review_renders", set), ("background_work", BackgroundWork)],
)
def test_lazy_creator_creates_once_and_stores_it_on_app_state(name, kind):
    app = SimpleNamespace(state=SimpleNamespace())
    first = getattr(app_state, name)(app)
    assert isinstance(first, kind)
    assert getattr(app.state, name) is first
    assert getattr(app_state, name)(app) is first


@pytest.mark.parametrize("name", ["idempotent_writes", "review_renders", "background_work"])
def test_lazy_creator_returns_a_seeded_object_untouched(name):
    sentinel = object()
    app = SimpleNamespace(state=SimpleNamespace(**{name: sentinel}))
    assert getattr(app_state, name)(app) is sentinel
