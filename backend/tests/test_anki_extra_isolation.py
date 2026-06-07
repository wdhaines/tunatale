"""Test that the `anki` package is NOT transitively imported by core modules.

This is a standing regression guard: the `anki` package must remain a sync-only
optional extra. Any core-module import chain that reaches `anki` would break
the default (anki-free) install. Tests launch a subprocess with a clean
sys.modules to avoid the current process's cached imports.
"""

import subprocess
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent


def test_anki_not_imported_by_core():
    """Verify anki is not in sys.modules after importing core modules."""
    code = """if True:
        import sys

        import app.main
        import app.api.srs
        import app.srs.fsrs
        import app.srs.queue_stats
        import app.anki.sync

        assert "anki" not in sys.modules, (
            f"anki was transitively imported: {sys.modules.get('anki')}"
        )
        print("PASS: anki not imported by core modules")
    """
    result = subprocess.run(
        ["uv", "run", "python", "-c", code],
        capture_output=True,
        text=True,
        cwd=str(BACKEND),
    )
    if result.returncode != 0:
        pytest.fail(f"anki import isolation violated:\nstdout: {result.stdout}\nstderr: {result.stderr}")
