"""The gate must keep a failing e2e run's evidence (tunatale-xw6s).

Written 2026-09-09 after a `card-image.spec.ts:96` failure could not be
diagnosed at all: Playwright wipes ``frontend/test-results/`` at the start of
every run, and the next run — the one confirming it was a flake — destroyed the
trace, the screenshot and the error context before anyone read them. Re-running
to see whether a flake reproduces is the FIRST thing anybody does, so the
evidence is destroyed by the standard response to it.

The oracle is behavioural, not textual: these run the script.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "preserve_e2e_artifacts.py"


def _run(src: Path, dest_root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_SCRIPT), str(src), str(dest_root)],
        capture_output=True,
        text=True,
    )


class TestPreserveE2EArtifacts:
    def test_a_failing_runs_artifacts_are_copied_out_of_harms_way(self, tmp_path) -> None:
        src = tmp_path / "test-results"
        (src / "some-spec-chromium").mkdir(parents=True)
        (src / "some-spec-chromium" / "trace.zip").write_bytes(b"PK\x03\x04trace")
        (src / "some-spec-chromium" / "error-context.md").write_text("the assertion that failed")
        dest_root = tmp_path / "kept"

        result = _run(src, dest_root)

        assert result.returncode == 0, result.stderr
        kept = Path(result.stdout.strip())
        assert kept.is_dir(), result.stdout
        assert kept.parent == dest_root
        assert (kept / "some-spec-chromium" / "trace.zip").read_bytes() == b"PK\x03\x04trace"
        assert (kept / "some-spec-chromium" / "error-context.md").read_text() == "the assertion that failed"

    def test_the_original_is_left_alone_because_the_next_run_owns_it(self, tmp_path) -> None:
        """A copy, not a move. Playwright clears its own directory; we must not
        race it, and a half-moved tree is worse than none."""
        src = tmp_path / "test-results"
        src.mkdir()
        (src / "trace.zip").write_bytes(b"x")

        _run(src, tmp_path / "kept")

        assert (src / "trace.zip").exists()

    def test_two_failures_do_not_overwrite_each_other(self, tmp_path) -> None:
        """The second flake is the one that makes a pattern visible."""
        src = tmp_path / "test-results"
        src.mkdir()
        (src / "a.txt").write_text("first")
        dest_root = tmp_path / "kept"

        first = Path(_run(src, dest_root).stdout.strip())
        (src / "a.txt").write_text("second")
        second = Path(_run(src, dest_root).stdout.strip())

        assert first != second
        assert (first / "a.txt").read_text() == "first"
        assert (second / "a.txt").read_text() == "second"

    def test_a_green_run_leaves_no_directory_behind(self, tmp_path) -> None:
        """Playwright writes nothing when everything passes. Preserving an empty
        tree would fill .git with directories that mean 'nothing went wrong'."""
        dest_root = tmp_path / "kept"

        result = _run(tmp_path / "test-results", dest_root)

        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == ""
        assert not dest_root.exists()

    def test_an_empty_results_directory_is_also_nothing_to_keep(self, tmp_path) -> None:
        """Playwright can leave the directory present but empty."""
        src = tmp_path / "test-results"
        src.mkdir()
        dest_root = tmp_path / "kept"

        result = _run(src, dest_root)

        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == ""
        assert not dest_root.exists()
