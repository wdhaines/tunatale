"""Table-backed lemmatizer: a torch-free engine that reproduces a sentence NLP model.

Built for the production host, which cannot carry the PyTorch pipeline (measured
on the e2-micro 2026-09-18, tunatale-kbb.18: 218 s to load the model, 453 ms per
sentence, and the live API paged out while it ran).

**Why a table can reproduce the model.** The model's lemmatizer is a function of
``(word, UPOS)`` alone; sentence context matters only through which UPOS the
tagger picks. A language plugin ships a gzipped table of
``surface, upos, lemma, is_default`` rows produced by the real model offline
(e.g. ``backend/scripts/build_stanza_lemma_table.py``). Each surface has exactly
one default row: the tag the model gives the bare word. :class:`TableLemmatizer`
serves the default unless the caller passes a context-chosen tag (see
:mod:`app.srs.lemma_resolver`).

**Cache compatibility.** The table records the model version it was built from
(first line ``#source_version=<v>``). Rows the REAL model cached under that
version are exact, so :func:`app.srs.lemmatizer.analyze_sentence_cached` reads
them first (``compatible_cache_versions``); the table's own rows are keyed
separately so a dev machine running the real model never mistakes them for its
own.

The committed artifact is the ``.tsv.gz``; the indexed SQLite beside it is a
build artifact (gitignored), built on first use or ahead of time with
``python -m app.srs.lemma_table`` (the Dockerfile does the latter).
"""

from __future__ import annotations

import gzip
import hashlib
import logging
import os
import re
import sqlite3
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from app.srs.lemmatizer import _ANALYSIS_SCHEMA_REV, TokenAnalysis

logger = logging.getLogger(__name__)

_HEADER_PREFIX = "#source_version="
_INSERT_BATCH = 50_000

# Word = letters/digits joined by internal hyphens or apostrophes; everything else
# that is not whitespace is its own token. Measured against the model's own
# tokenization on 1,589 cached lesson sentences: 1,587 identical (the two misses
# are a trailing "…" glued to a word and a dangling compound hyphen).
_TOKEN_RE = re.compile(r"\w+(?:[-‑‐'’]\w+)*|[^\w\s]", re.UNICODE)


def tokenize(sentence: str) -> list[str]:
    """Split *sentence* into the tokens the table engine analyzes."""
    return _TOKEN_RE.findall(sentence)


def db_path_for(extract_path: Path) -> Path:
    """The SQLite build artifact that sits beside a ``<name>.tsv.gz`` extract."""
    return extract_path.with_name(extract_path.name.removesuffix(".tsv.gz") + ".sqlite3")


def _source_id(extract_path: Path) -> str:
    return hashlib.sha256(extract_path.read_bytes()).hexdigest()[:12]


def build_lemma_table_db(extract_path: Path, db_path: Path) -> None:
    """Build the indexed SQLite table from the committed gzipped extract.

    Written to a temporary file and renamed into place, so a reader never opens a
    half-built database. Raises ``ValueError`` when the header is missing or a
    surface has no default row — a table that cannot answer must not ship.
    """
    tmp = db_path.with_name(f"{db_path.name}.{os.getpid()}.tmp")
    tmp.unlink(missing_ok=True)
    conn = sqlite3.connect(tmp)
    try:
        conn.execute(
            "CREATE TABLE lemmas (surface TEXT NOT NULL, upos TEXT NOT NULL, lemma TEXT NOT NULL, is_default INTEGER NOT NULL)"
        )
        conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        with gzip.open(extract_path, "rt", encoding="utf-8") as fh:
            header = fh.readline().rstrip("\n")
            if not header.startswith(_HEADER_PREFIX):
                raise ValueError(f"{extract_path}: first line must be '{_HEADER_PREFIX}<version>', got {header!r}")
            batch: list[tuple[str, str, str, int]] = []
            for line in fh:
                surface, upos, lemma, is_default = line.rstrip("\n").split("\t")
                batch.append((surface, upos, lemma, int(is_default)))
                if len(batch) >= _INSERT_BATCH:
                    conn.executemany("INSERT INTO lemmas VALUES (?, ?, ?, ?)", batch)
                    batch.clear()
            conn.executemany("INSERT INTO lemmas VALUES (?, ?, ?, ?)", batch)
        conn.execute("CREATE INDEX idx_lemmas_surface ON lemmas(surface)")
        bad = conn.execute("SELECT surface FROM lemmas GROUP BY surface HAVING SUM(is_default) != 1 LIMIT 1").fetchone()
        if bad is not None:
            raise ValueError(f"{extract_path}: surface {bad[0]!r} does not have exactly one default row")
        conn.executemany(
            "INSERT INTO meta VALUES (?, ?)",
            [("source_version", header.removeprefix(_HEADER_PREFIX)), ("source_id", _source_id(extract_path))],
        )
        conn.commit()
    except BaseException:
        conn.close()
        tmp.unlink(missing_ok=True)
        raise
    conn.close()
    tmp.replace(db_path)


def ensure_lemma_table_db(extract_path: Path) -> Path:
    """Return the built DB for *extract_path*, (re)building it if absent or stale.

    Stale = built from different extract bytes, so a regenerated table can never
    be served from an old build. Raises ``FileNotFoundError`` when the extract
    itself is missing: a missing table is an error, not an empty answer.
    """
    if not extract_path.exists():
        raise FileNotFoundError(f"lemma table extract missing: {extract_path}")
    db_path = db_path_for(extract_path)
    if db_path.exists():
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            row = conn.execute("SELECT value FROM meta WHERE key = 'source_id'").fetchone()
        except sqlite3.DatabaseError:
            row = None
        finally:
            conn.close()
        if row is not None and row[0] == _source_id(extract_path):
            return db_path
        logger.warning("Lemma table %s is stale or unreadable; rebuilding from %s", db_path, extract_path)
    build_lemma_table_db(extract_path, db_path)
    return db_path


@dataclass(frozen=True)
class Reading:
    """One ``(upos, lemma)`` reading of a surface; ``is_default`` marks the bare-word tag."""

    upos: str
    lemma: str
    is_default: bool


class LemmaTable:
    """Read-only lookup over a built lemma-table DB. Safe to share across threads."""

    def __init__(self, db_path: Path) -> None:
        self._conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, check_same_thread=False)
        self._lock = threading.Lock()
        meta = dict(self._conn.execute("SELECT key, value FROM meta").fetchall())
        self.source_version: str = meta["source_version"]
        self.source_id: str = meta["source_id"]

    def close(self) -> None:
        """Release the connection. The process-wide cached instance never needs
        this; short-lived owners (tests, scripts) do, or the handle leaks until
        GC and warns inside whatever runs next (tunatale-zcgs)."""
        with self._lock:
            self._conn.close()

    def readings(self, surface: str) -> list[Reading]:
        """Readings for *surface*: exact casing first, then lowercased. ``[]`` if unknown."""
        for key in dict.fromkeys((surface, surface.lower())):
            with self._lock:
                rows = self._conn.execute(
                    "SELECT upos, lemma, is_default FROM lemmas WHERE surface = ? ORDER BY rowid", (key,)
                ).fetchall()
            if rows:
                return [Reading(u, lem, bool(d)) for u, lem, d in rows]
        return []


def _analyze_token(token: str, readings: list[Reading], upos: str | None) -> TokenAnalysis:
    if not readings:
        if token.isdigit():
            return TokenAnalysis(surface=token, lemma=token, upos="NUM")
        if not any(ch.isalnum() for ch in token):
            # The model lemmatizes punctuation as "$" + the mark ("." -> "$.").
            return TokenAnalysis(surface=token, lemma=f"${token}", upos="PUNCT")
        return TokenAnalysis(surface=token, lemma=token.lower(), upos="")
    chosen = next((r for r in readings if r.upos == upos), None) if upos else None
    if chosen is None:
        chosen = next(r for r in readings if r.is_default)
    return TokenAnalysis(surface=token, lemma=chosen.lemma, upos=chosen.upos)


class TableLemmatizer:
    """The :class:`app.srs.lemmatizer.Lemmatizer` protocol, answered from a lemma table.

    Morphological features (case, number, …) are empty: the table holds lemma and
    UPOS only. That is what the old lowercase engine gave too, so nothing that
    consumes features regresses.
    """

    def __init__(self, language_code: str, extract_path: Path) -> None:
        self._language_code = language_code
        self._table = LemmaTable(ensure_lemma_table_db(extract_path))
        self._cache_version = f"table-{self._table.source_version}-{self._table.source_id}"
        # Rows the real model cached under the version this table was built from.
        self.compatible_cache_versions: tuple[str, ...] = (f"{self._table.source_version}+{_ANALYSIS_SCHEMA_REV}",)

    def close(self) -> None:
        self._table.close()

    def readings(self, surface: str) -> list[Reading]:
        return self._table.readings(surface)

    def lemmatize(self, word: str, language_code: str) -> str:
        return self.analyze(word, language_code)[0]

    def analyze(self, word: str, language_code: str) -> tuple[str, str, str]:
        if language_code != self._language_code:
            return word.lower(), "", ""
        return _analyze_token(word, self._table.readings(word), None).lemma, "", ""

    def analyze_sentence(self, sentence: str, language_code: str) -> list[TokenAnalysis]:
        return self.analyze_sentence_with_tags(sentence, language_code, {})

    def analyze_sentence_with_tags(
        self, sentence: str, language_code: str, upos_by_index: dict[int, str]
    ) -> list[TokenAnalysis]:
        """Analyze *sentence*, using ``upos_by_index[i]`` for token *i* when it is one of its readings.

        A tag that is not among the token's readings is ignored in favour of the
        default — a context resolver can pick between readings, never invent one.
        """
        tokens = tokenize(sentence)
        if language_code != self._language_code:
            return [TokenAnalysis(surface=t, lemma=t.lower()) for t in tokens]
        return [_analyze_token(t, self._table.readings(t), upos_by_index.get(i)) for i, t in enumerate(tokens)]


def main(argv: list[str]) -> int:
    """Build the lemma-table DB for every registered language that ships one."""
    from app.languages import all_lemma_table_paths

    for extract in all_lemma_table_paths():
        print(f"lemma table: {ensure_lemma_table_db(extract)}")
    return 0


if __name__ == "__main__":  # pragma: no cover — CLI entry; main() is tested directly
    sys.exit(main(sys.argv[1:]))
