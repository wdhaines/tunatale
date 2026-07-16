"""Tests for the core→plugin import boundary checker."""

from __future__ import annotations

import textwrap
from pathlib import Path

from scripts.check_plugin_imports import _check_file, do_check


def _make_file(tmp_path: Path, name: str, code: str) -> Path:
    """Write *code* to *tmp_path*/app/*name* (creating dirs as needed).

    Returns the filepath.  Pass ``tmp_path / "app"`` as *app_dir* to
    ``_check_file`` so that ``rel`` resolves to ``app/<name>``.
    """
    filepath = tmp_path / "app" / name
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(textwrap.dedent(code), encoding="utf-8")
    return filepath


_APP_DIR = Path("app")  # production default; tests pass tmp_path / "app"


def _check(filepath: Path, tmp_path: Path) -> list[tuple[int, str]]:
    """Run the checker on *filepath* with *app_dir* = tmp_path / \"app\"."""
    return _check_file(filepath, tmp_path / "app")


class TestCoreModuleLevelPluginImportFails:
    def test_import_statement(self, tmp_path: Path) -> None:
        f = _make_file(tmp_path, "foo.py", "import app.plugins.languages.sl\n")
        violations = _check(f, tmp_path)
        assert len(violations) == 1
        assert violations[0][1] == "app.plugins.languages.sl"

    def test_from_import_statement(self, tmp_path: Path) -> None:
        f = _make_file(
            tmp_path,
            "bar.py",
            "from app.plugins.languages.no import preprocessor\n",
        )
        violations = _check(f, tmp_path)
        assert len(violations) == 1
        assert violations[0][1] == "app.plugins.languages.no"


class TestPluginInternalImportPasses:
    def test_import_inside_plugins(self, tmp_path: Path) -> None:
        f = _make_file(
            tmp_path,
            "plugins/languages/sl/__init__.py",
            "from app.languages import LanguageConfig, register\n",
        )
        violations = _check(f, tmp_path)
        assert violations == []


class TestLanguagesNamespaceDiscoveryPasses:
    def test_languages_imports_namespace(self, tmp_path: Path) -> None:
        f = _make_file(
            tmp_path,
            "languages.py",
            "import app.plugins.languages as _plugins_pkg\n",
        )
        violations = _check(f, tmp_path)
        assert violations == []


class TestLazyAnkiSyncInAllowlistedFilePasses:
    def test_function_level_import_in_anki(self, tmp_path: Path) -> None:
        f = _make_file(
            tmp_path,
            "api/anki.py",
            "def foo():\n    from app.plugins.anki_sync.sync import sync_collection\n    sync_collection()\n",
        )
        violations = _check(f, tmp_path)
        assert violations == []


class TestModuleLevelAnkiSyncInAllowlistedFileFails:
    def test_module_level_import_in_anki(self, tmp_path: Path) -> None:
        f = _make_file(
            tmp_path,
            "api/anki.py",
            "from app.plugins.anki_sync.sync import sync_collection\n",
        )
        violations = _check(f, tmp_path)
        assert len(violations) == 1
        assert violations[0][1] == "app.plugins.anki_sync.sync"


class TestLazyAnkiSyncInNonAllowlistedFileFails:
    def test_function_level_import_in_other(self, tmp_path: Path) -> None:
        f = _make_file(
            tmp_path,
            "some_core.py",
            "def foo():\n    from app.plugins.anki_sync.sync import sync_collection\n    sync_collection()\n",
        )
        violations = _check(f, tmp_path)
        assert len(violations) == 1


class TestDoCheckCatchesInitPyViolation:
    """do_check must not skip __init__.py files in core."""

    def test_init_py_with_plugin_import_fails(self, tmp_path: Path, capsys) -> None:
        _make_file(tmp_path, "generation/__init__.py", "from app.plugins.languages.sl import preprocessor\n")
        _make_file(tmp_path, "ok.py", "import os\n")
        rc = do_check(tmp_path / "app")
        assert rc == 1
        out = capsys.readouterr().out
        assert "app/generation/__init__.py" in out

    def test_clean_tree_passes(self, tmp_path: Path) -> None:
        _make_file(tmp_path, "ok.py", "import os\n")
        rc = do_check(tmp_path / "app")
        assert rc == 0
