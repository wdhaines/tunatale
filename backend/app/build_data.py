"""Build, or verify, every data file the app needs that is built rather than committed.

    python -m app.build_data            # build what is missing or stale
    python -m app.build_data --check    # build nothing; exit 1 naming what is missing

The Dockerfile and ``switch.sh`` run the first; ``switch.sh start`` runs the
second before the laptop instance listens. Two kinds of artifact:

* each language's lemma table (``LanguageConfig.lemma_table_path``), built by
  ``app.srs.lemma_table``, which keeps its own staleness stamp;
* each plugin's ``BuiltData`` registrations — today the NST lexicon.

A ``BuiltData`` build is stamped with the sha256 of the extract it came from, in
a ``built_data_meta`` table, so a regenerated extract can never be served from
an old build. A build with no stamp (one made by a script before this module
existed) cannot be proven current and is rebuilt once.

Why ``--check`` exists (tunatale-ip8q): the NST lexicon was built by nothing
that ships, and its absence made ``lexicon_has_secondary_stress`` answer "no
signal" for every word, which silently re-enabled compound over-splitting on
prod and the laptop instance. A missing artifact must stop a start, not degrade
a lesson.
"""

from __future__ import annotations

import hashlib
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path

from app.languages import BuiltData, all_built_data, all_lemma_table_paths
from app.srs.lemma_table import db_path_for, ensure_lemma_table_db

_META = "built_data_meta"


def _source_id(extract: Path) -> str:
    return hashlib.sha256(extract.read_bytes()).hexdigest()


def is_current(art: BuiltData) -> bool:
    """Whether *art*'s build exists and is stamped with its extract's current bytes."""
    if not art.db.exists() or not art.extract.exists():
        return False
    conn = sqlite3.connect(f"file:{art.db}?mode=ro", uri=True)
    try:
        row = conn.execute(f"SELECT value FROM {_META} WHERE key = 'source_id'").fetchone()
    except sqlite3.DatabaseError:
        row = None
    finally:
        conn.close()
    return row is not None and row[0] == _source_id(art.extract)


def ensure(art: BuiltData) -> Path:
    """Return *art*'s build, (re)building and stamping it if missing or stale.

    Built into a temporary file and moved into place, so a reader never sees a
    half-written database and a failed build leaves nothing behind.
    """
    if not art.extract.exists():
        raise FileNotFoundError(f"built-data extract missing: {art.extract}")
    if is_current(art):
        return art.db
    tmp = art.db.with_name(art.db.name + ".build.tmp")
    try:
        art.build(art.extract, tmp)
        with sqlite3.connect(tmp) as conn:
            conn.execute(f"CREATE TABLE {_META} (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            conn.execute(f"INSERT INTO {_META} VALUES ('source_id', ?)", (_source_id(art.extract),))
        conn.close()
        tmp.replace(art.db)
    finally:
        tmp.unlink(missing_ok=True)
    return art.db


def main(
    argv: Sequence[str],
    *,
    artifacts: Sequence[BuiltData] | None = None,
    lemma_tables: Sequence[Path] | None = None,
) -> int:
    """Build (default) or verify (``--check``) every artifact; 1 if any is missing.

    *artifacts* / *lemma_tables* default to the registry and are injectable so a
    test can run both modes against fixtures instead of the 44 MB real build.
    """
    arts = list(all_built_data() if artifacts is None else artifacts)
    tables = list(all_lemma_table_paths() if lemma_tables is None else lemma_tables)
    if "--check" in argv:
        missing = [str(a.db) for a in arts if not is_current(a)]
        missing += [str(db_path_for(t)) for t in tables if not db_path_for(t).exists()]
        for path in missing:
            print(f"built data missing or stale: {path}", file=sys.stderr)
        if missing:
            print("run: python -m app.build_data", file=sys.stderr)
        return 1 if missing else 0
    for table in tables:
        print(f"lemma table: {ensure_lemma_table_db(table)}")
    for art in arts:
        print(f"built data: {ensure(art)}")
    return 0


if __name__ == "__main__":  # pragma: no cover — CLI entry; main() is tested directly
    sys.exit(main(sys.argv[1:]))
