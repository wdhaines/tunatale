"""``test.sh`` must never build a path under ``$ROOT/.git/``.

In a linked worktree ``.git`` is a FILE (a gitdir pointer), so every such path
is impossible there: ``tee`` and the history append fail with "Not a directory"
and each group ends red although its tests passed (tunatale-17a4). Paths must
come from ``git rev-parse`` (``TT_GIT_DIR`` / ``TT_COMMON_DIR``) instead.
"""

from __future__ import annotations

from pathlib import Path

_TEST_SH = Path(__file__).resolve().parents[2] / "test.sh"


def _code_lines() -> list[tuple[int, str]]:
    return [
        (n, line) for n, line in enumerate(_TEST_SH.read_text().splitlines(), 1) if not line.lstrip().startswith("#")
    ]


def test_no_code_line_builds_a_path_under_root_dot_git() -> None:
    offenders = [(n, line.strip()) for n, line in _code_lines() if "$ROOT/.git/" in line and "|| echo" not in line]
    assert offenders == []


def test_the_scanner_sees_the_gate_log_paths() -> None:
    # Control: the scan above is vacuous if it cannot see the lines it guards.
    code = "\n".join(line for _, line in _code_lines())
    for name in ("tt-test-last.log", "tt-test-history.log", "tt-e2e-failures"):
        assert name in code
