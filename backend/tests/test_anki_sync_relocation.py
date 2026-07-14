"""Guardrail tests for the Anki-sync plugin relocation (Stage 4).

Verifies that:
- ``app/audio/cloze_tts.py`` and ``app/api/admin.py`` — both core modules that
  must work whether or not the optional ``anki_sync`` plugin package is
  installed — have no module-level import reaching into it (``anki_sync``,
  ``import_seed``, or any ``.sync`` submodule). Anything they need from the
  plugin must be a lazy (inside-function) import.
- ``app/main.py``'s capability gate actually controls whether
  ``app.api.anki.router`` gets mounted, and ``/api/languages`` advertises the
  same decision via ``sync_available``: with ``settings.sync_enabled=False``,
  the router is absent (peer-sync 404s); with it ``True`` (and the plugin
  importable, as it is in this checkout), the route is reachable.
"""

import ast
import importlib
import importlib.util
from pathlib import Path

from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.main import _anki_sync_importable

_APP_DIR = Path(__file__).resolve().parent.parent / "app"

_FORBIDDEN_SUBSTRINGS = ("anki_sync", "import_seed", ".sync")


def _module_level_import_targets(path: Path) -> list[str]:
    """Every module string named by a module-level ``from X import Y`` in *path*."""
    tree = ast.parse(path.read_text())
    return [node.module for node in ast.iter_child_nodes(tree) if isinstance(node, ast.ImportFrom) and node.module]


def _assert_no_forbidden_module_level_import(path: Path) -> None:
    for module in _module_level_import_targets(path):
        for needle in _FORBIDDEN_SUBSTRINGS:
            assert needle not in module, (
                f"{path.name} has a module-level import from {module!r} "
                f"(matches forbidden substring {needle!r}); the anki_sync plugin "
                "is optional and must only be reached via a lazy import."
            )


# -- AST guard: core stays free of eager anki_sync imports -------------------


def test_cloze_tts_has_no_module_level_anki_sync_import():
    _assert_no_forbidden_module_level_import(_APP_DIR / "audio" / "cloze_tts.py")


def test_admin_has_no_module_level_anki_sync_import():
    _assert_no_forbidden_module_level_import(_APP_DIR / "api" / "admin.py")


# -- Capability guard: settings.sync_enabled actually gates the router -------


def _stub_run_driver(command, timeout=120):
    """Replace the anki subprocess boundary so peer_sync fails fast, deterministically,
    and without spawning a real driver. Fails at login, before any real I/O.

    Patch target is ``app.plugins.anki_sync.sync_orchestrator._run_driver`` — a
    permanent process boundary in ``tests/mock_allowlist.txt``, not a grandfathered
    internal mock.
    """
    from app.plugins.anki_sync.sync_orchestrator import PeerSyncError

    raise PeerSyncError("guardrail-stub: no real driver in this test")


def test_anki_sync_importable_returns_false_on_find_spec_failure(monkeypatch):
    """A broken/partial parent package raises out of find_spec, not just returns None."""

    def _boom(name):
        raise ModuleNotFoundError("simulated broken parent package")

    monkeypatch.setattr(importlib.util, "find_spec", _boom)
    assert _anki_sync_importable() is False


class TestCapabilityGate:
    """Reload app.main under each settings.sync_enabled value.

    The router-mount decision (``app.main`` module body) runs once at import
    time, so toggling ``settings.sync_enabled`` after the fact requires a
    fresh import to take effect — hence the explicit ``importlib.reload``
    rather than just monkeypatching the already-imported singleton ``app``.

    ``importlib.reload`` mutates the ``app.main`` module IN PLACE (same
    module object, same ``sys.modules`` entry) rather than replacing it, so
    other already-imported references to the pristine collection-time
    ``app.main.app`` singleton are unaffected by the reload itself. But other
    test files' fixtures (e.g. ``api_app_state``) do a *fresh*
    ``from app.main import app`` at fixture-call time, which would pick up
    whatever this test last left ``main_module.app`` pointing at. To avoid
    bleeding a reloaded (state-less) app into later tests, every test here
    restores ``main_module.app`` back to the exact pristine object afterward
    — not just a fresh, functionally-equivalent reload — so later fixtures
    that (re-)populate ``app.state`` on the object they import always agree
    with the object test clients elsewhere in the suite are actually using.
    """

    async def test_sync_disabled_unmounts_router_and_reports_unavailable(self, monkeypatch):
        import app.main as main_module

        original_app = main_module.app
        monkeypatch.setattr(settings, "sync_enabled", False)
        importlib.reload(main_module)
        try:
            async with AsyncClient(transport=ASGITransport(app=main_module.app), base_url="http://test") as client:
                langs = await client.get("/api/languages")
                assert langs.json()["sync_available"] is False

                resp = await client.post("/api/anki/peer-sync")
                assert resp.status_code == 404
        finally:
            monkeypatch.undo()
            main_module.app = original_app

    async def test_sync_enabled_mounts_router_and_reports_available(self, monkeypatch):
        import app.main as main_module

        original_app = main_module.app
        monkeypatch.setattr(settings, "sync_enabled", True)
        monkeypatch.setattr(
            "app.plugins.anki_sync.sync_orchestrator._run_driver",
            _stub_run_driver,
        )
        importlib.reload(main_module)
        try:
            async with AsyncClient(transport=ASGITransport(app=main_module.app), base_url="http://test") as client:
                langs = await client.get("/api/languages")
                assert langs.json()["sync_available"] is True

                resp = await client.post("/api/anki/peer-sync")
                assert resp.status_code != 404
        finally:
            monkeypatch.undo()
            main_module.app = original_app
