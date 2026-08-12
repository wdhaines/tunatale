#!/usr/bin/env python3
"""Validate a production env file against the prod profile, before it deploys.

``main.py::_assert_prod_profile`` refuses to boot a misconfigured prod process.
That is the backstop, and it fires on the box, after the image is built and
shipped. This checker is the same rules applied to a file on disk, so the
failure lands in ``./test.sh`` instead.

``./test.sh`` runs it with no argument, against the committed
``backend/.env.prod.example`` — the file a deployment copies. Keeping that
template *valid* is the point: a template that produces a box which won't start
is worse than no template, because it is trusted.

**Why the environ juggling below is load-bearing.** ``Settings(_env_file=path)``
does not read the file in isolation — pydantic-settings ranks ``os.environ``
ABOVE the env file, so whatever the developer happens to have exported wins.
Both directions are wrong and both are silent:

  * ``LLM_MODE=mock`` exported (the common case — it is in the dev ``.env``)
    fails a perfectly correct prod file.
  * ``LLM_MODE=live`` exported passes a prod file that never sets it, which is
    exactly the deployment that serves cassette replies and looks healthy.

So the file is evaluated with ``os.environ`` swapped for the file's own keys,
and restored afterwards. ``tests/test_check_prod_env.py`` pins both directions
plus the restore.

Usage::

    uv run python scripts/check_prod_env.py [path/to/.env.prod]
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import dotenv_values

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings, prod_profile_problems  # noqa: E402

DEFAULT_ENV_PATH = Path(__file__).resolve().parents[1] / ".env.prod.example"


def _settings_from_file(path: Path) -> Settings:
    """Build a Settings from *path* alone, with the ambient environ excluded."""
    values = {k: v for k, v in dotenv_values(path).items() if v is not None}
    saved = dict(os.environ)
    os.environ.clear()
    os.environ.update(values)
    try:
        return Settings(_env_file=None)
    finally:
        os.environ.clear()
        os.environ.update(saved)


def check_env_file(path: Path) -> tuple[int, list[str]]:
    """Return ``(exit_code, messages)`` for *path* read as a prod profile."""
    if not path.exists():
        return 1, [f"FAIL: {path} not found"]

    settings = _settings_from_file(path)

    # Checked here rather than in prod_profile_problems, which is deliberately
    # profile-agnostic. Without it a file missing TT_ENV would produce an empty
    # problem list — a clean negative indistinguishable from a valid file.
    problems = [] if settings.tt_env == "prod" else [f"tt_env is {settings.tt_env!r}, not 'prod' (set TT_ENV=prod)"]
    problems += prod_profile_problems(settings)

    return (1 if problems else 0), [f"FAIL: {path.name}: {p}" for p in problems]


def do_check(argv: list[str]) -> int:
    """Scan, evaluate, print. Returns exit code."""
    path = Path(argv[0]) if argv else DEFAULT_ENV_PATH
    exit_code, messages = check_env_file(path)
    for msg in messages:
        print(msg)
    return exit_code


if __name__ == "__main__":  # pragma: no cover - CLI guard
    sys.exit(do_check(sys.argv[1:]))
