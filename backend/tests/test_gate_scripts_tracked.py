"""Test that every ``scripts/*.py`` referenced in ``test.sh`` and ``ci.yml`` is git-tracked.

Regression guard for the ``db6fcf7`` incident (a checker wired into the gate
while gitignored → red on fresh checkouts for 7 commits).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT_PATTERN = re.compile(r"scripts/[A-Za-z0-9_/]+\.py")


def _get_git_tracked_scripts() -> set[str]:
    """Return set of ``scripts/...`` paths tracked by git under ``backend/``."""
    result = subprocess.run(
        ["git", "ls-files", "backend/scripts"],
        capture_output=True,
        text=True,
        check=True,
        cwd=_REPO_ROOT,
    )
    return set(result.stdout.strip().splitlines())


def _get_referenced_scripts_from(text: str) -> list[str]:
    """Extract ``scripts/*.py`` references from *text*."""
    return _SCRIPT_PATTERN.findall(text)


def test_all_referenced_scripts_are_tracked() -> None:
    tracked = _get_git_tracked_scripts()

    test_sh = (_REPO_ROOT / "test.sh").read_text()
    ci_yml = (_REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text()

    refs = _get_referenced_scripts_from(test_sh) + _get_referenced_scripts_from(ci_yml)
    assert refs, "No scripts/*.py references found — pattern may be wrong"

    for ref in refs:
        script_path = f"backend/{ref}"
        assert script_path in tracked, (
            f"{ref} referenced in gate but not git-tracked (is it listed in backend/scripts/.gitignore?)"
        )


def test_negative_missing_script_fails() -> None:
    """Confirm the check fails for a synthetic missing script reference."""
    fake_text = "uv run python scripts/nonexistent.py"
    refs = _get_referenced_scripts_from(fake_text)
    assert refs == ["scripts/nonexistent.py"]

    tracked = _get_git_tracked_scripts()
    script_path = f"backend/{refs[0]}"
    assert script_path not in tracked, "Test invariant broken: fake script must not exist"
