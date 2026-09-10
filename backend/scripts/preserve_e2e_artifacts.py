"""Copy a failing Playwright run's artifacts somewhere the next run cannot destroy.

``frontend/test-results/`` is cleared at the START of every Playwright run, so
the standard response to a red e2e step — run it again and see whether it
reproduces — is also what destroys the only evidence of the failure. That is how
a ``card-image.spec.ts:96`` failure went undiagnosed on 2026-09-09: the trace,
the screenshot and the error context were gone before anyone opened them, and
all that survived was "it passed the second time", which is not a diagnosis.

Called by ``test.sh`` only when the e2e step FAILS. Prints the directory it
wrote, or nothing when there was nothing to keep. Never fails the gate: the
caller has a real failure to report and this must not mask it.

Usage:  preserve_e2e_artifacts.py <results-dir> <destination-root>
"""

from __future__ import annotations

import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path


def preserve(src: Path, dest_root: Path) -> Path | None:
    """Copy *src* under *dest_root* in a fresh timestamped directory.

    Returns the directory written, or ``None`` when *src* holds nothing — a
    green run leaves no artifacts, and a directory that means "nothing went
    wrong" is noise in a place people go looking for signal.

    A COPY, not a move: Playwright owns that directory and clears it itself, and
    a half-moved tree is worse than no tree at all.
    """
    if not src.is_dir() or not any(src.iterdir()):
        return None

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    dest = dest_root / stamp
    # Two failures inside one second is rare and exactly when it matters — the
    # second red is what turns one incident into a pattern.
    suffix = 1
    while dest.exists():
        suffix += 1
        dest = dest_root / f"{stamp}-{suffix}"

    shutil.copytree(src, dest)
    return dest


def main(argv: list[str]) -> int:
    src, dest_root = Path(argv[1]), Path(argv[2])
    kept = preserve(src, dest_root)
    if kept is not None:
        print(kept)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
