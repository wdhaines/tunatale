"""Tests for plugin isolation: single-plugin installs must not crash.

Each test copies ``app/`` to a tmp dir, deletes plugin folder(s), and runs a
subprocess with ``PYTHONPATH`` pointing at the copy.  This proves the bug fix
works in a realistic "delete a plugin folder" scenario.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
APP_SRC = BACKEND / "app"


def _run_isolated(tmp_path: Path, script: str) -> subprocess.CompletedProcess[str]:
    """Run *script* in a subprocess with ``PYTHONPATH=tmp_path``."""
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env={"PYTHONPATH": str(tmp_path), "PATH": str(Path(sys.executable).parent)},
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.fixture()
def isolated_app(tmp_path: Path) -> Path:
    """Copy ``app/`` (sans ``__pycache__``) into *tmp_path*/app and return the copy."""
    dest = tmp_path / "app"
    shutil.copytree(
        APP_SRC,
        dest,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    return tmp_path


def _remove_if_exists(path: Path) -> None:
    """Remove *path* if it exists (directory or file)."""
    if path.is_dir():
        shutil.rmtree(path)
    elif path.is_file():
        path.unlink()


def test_sl_only_direct_import(isolated_app: Path) -> None:
    """(a) sl-only tree: importing the plugin first must not crash."""
    _remove_if_exists(isolated_app / "app" / "plugins" / "languages" / "no")

    result = _run_isolated(
        isolated_app,
        "import app.plugins.languages.sl; "
        "from app.languages import known_language_codes; "
        "print(sorted(known_language_codes()))",
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert result.stdout.strip() == str(["en", "sl"])


def test_no_only_direct_import(isolated_app: Path) -> None:
    """(b) no-only tree: importing the plugin first must not crash."""
    _remove_if_exists(isolated_app / "app" / "plugins" / "languages" / "sl")

    result = _run_isolated(
        isolated_app,
        "import app.plugins.languages.no; "
        "from app.languages import known_language_codes; "
        "print(sorted(known_language_codes()))",
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert result.stdout.strip() == str(["en", "no"])


def test_zero_plugins_hard_fail(isolated_app: Path) -> None:
    """(c) zero-plugin tree: discover() must raise RuntimeError."""
    _remove_if_exists(isolated_app / "app" / "plugins" / "languages" / "sl")
    _remove_if_exists(isolated_app / "app" / "plugins" / "languages" / "no")

    result = _run_isolated(
        isolated_app,
        "from app.languages import discover; discover()",
    )
    assert result.returncode != 0
    assert "No language plugin" in result.stderr
