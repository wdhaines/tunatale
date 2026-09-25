"""Every INSERT into ``collocations`` must name ``language_code``.

The schema still declares ``language_code TEXT NOT NULL DEFAULT 'sl'``
(``db_base.py``, ``migrations.py``) — a leftover from when Slovene was the only
language. An INSERT that leaves the column out does not fail: it silently files
a Norwegian or Tagalog word as Slovene. Dropping the default needs a table
rebuild, so this guard is the cheaper fix (tunatale-w4m7.2, seam 4).
"""

import re
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app"

# `INSERT [OR …] INTO collocations (<columns>)` — spans lines inside a
# triple-quoted SQL string. `\b` keeps `collocations_foo` tables out.
_INSERT = re.compile(r"INSERT(?:\s+OR\s+\w+)?\s+INTO\s+collocations\b\s*(\()?([^)]*)", re.IGNORECASE)


def _inserts() -> list[tuple[str, str | None]]:
    found = []
    for path in sorted(APP.rglob("*.py")):
        for m in _INSERT.finditer(path.read_text()):
            columns = m.group(2) if m.group(1) else None
            found.append((f"{path.relative_to(APP.parent)}:{m.start()}", columns))
    return found


def test_the_scan_finds_the_known_inserts():
    # Control: a regex that matches nothing would pass the real test vacuously.
    # Three INSERTs exist today (two in db_collocations, one in migrations).
    assert len(_inserts()) >= 3


def test_every_collocation_insert_names_language_code():
    offenders = [where for where, columns in _inserts() if columns is None or "language_code" not in columns]
    assert offenders == [], f"INSERT INTO collocations without language_code (defaults to 'sl'): {offenders}"
