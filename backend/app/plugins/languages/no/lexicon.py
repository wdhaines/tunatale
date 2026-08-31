"""Pronunciation lexicon backed by the NST pronunciation dictionary.

Stage 1 of lexicon adoption: this module is **called by nothing** — it ships the
facet, the data extract, and the resolver; wiring ``<phoneme>`` into the TTS
path is stage 2. The committed artifact is a lean gzipped 4-column extract
(``nst_lexicon.tsv.gz``); the indexed SQLite database is a BUILD ARTIFACT,
gitignored, produced by ``scripts/build_nst_lexicon.py build``. The source
lexicon is CC0 (the National Library of Norway).

Resolution rules (measured against the real data — do not re-derive):

- Candidate rows are reduced to the minimum certainty FIRST.
- If readings still differ and a UPOS tag is supplied, it selects among the
  survivors via :data:`UPOS_TO_NST` (compound ``PM|...`` proper-noun tags match
  by prefix).
- If they still differ, the outcome is ``AMBIGUOUS_*`` — never a guess: ``seg``
  is /sæi/ as a pronoun and /seːɡ/ as a verb, and guessing yields a different
  word.

The database is opened read-only and lazily; a missing build is raised loudly
(:class:`FileNotFoundError`) rather than answered with an empty result.
"""

from __future__ import annotations

import gzip
import logging
import sqlite3
from pathlib import Path

from app.languages import LexiconOutcome, LexiconResolution

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent / "data"
# Committed (CC0 extract, ~4.6 MB gzipped) vs build artifact (~44 MB, gitignored).
EXTRACT_PATH = DATA_DIR / "nst_lexicon.tsv.gz"
DB_PATH = DATA_DIR / "nst_lexicon.sqlite3"

BUILD_COMMAND = "uv run python scripts/build_nst_lexicon.py build"

# UPOS (stanza's UD tags) -> NST POS tag. ``None`` marks UD tags with NO
# confident NST equivalent: supplying them degrades to no-POS behaviour rather
# than guessing at a match. A tag absent from the dict entirely is a MAPPING
# GAP and logs a warning — an unmapped tag silently becoming ABSENT is the
# failure mode this table exists to prevent.
UPOS_TO_NST: dict[str, str | None] = {
    "NOUN": "NN",
    "PROPN": "PM",
    "VERB": "VB",
    "AUX": "VB",
    "ADJ": "JJ",
    "ADV": "AB",
    "PRON": "PN",
    "DET": "DT",
    "ADP": "PP",
    "CCONJ": "KN",
    "INTJ": "IN",
    # No confident NST equivalent (measured): NST has no subordinator,
    # particle, or untagged-class tags, and its numeral tags (RG cardinal /
    # RO ordinal) split UD NUM in a way that would guess.
    "SCONJ": None,
    "NUM": None,
    "PART": None,
    "X": None,
    "PUNCT": None,
    "SYM": None,
}

_INSERT_BATCH = 50_000


def nst_lexicon_installed(db_path: Path = DB_PATH) -> bool:
    """Whether the built lexicon database exists (a visible capability check).

    Mirrors ``app.audio.slicer.alignment_installed``: a cheap probe a caller
    gates on BEFORE asking for resolutions, so a missing build is a visible
    off-switch instead of a surprise raise deep in a render.
    """
    return db_path.exists()


def _upos_to_nst(upos: str) -> str | None:
    """Map *upos* to its NST tag, or ``None`` when no mapping holds.

    Known-but-unmapped tags (``SCONJ``, ``NUM``, …) degrade silently to the
    no-POS behaviour; a tag missing from the table entirely is logged as the
    mapping gap it is.
    """
    if upos in UPOS_TO_NST:
        return UPOS_TO_NST[upos]
    logger.warning("Unmapped UPOS tag %r: treating as no POS for lexicon lookup", upos)
    return None


def _pos_matches(entry_pos: str, nst: str) -> bool:
    """Whether an entry's NST tag matches the mapped target.

    Compound proper-noun tags (``PM|person|SUR``, ``PM|place|GEO``, …) all mean
    "name", so they match their ``PM`` prefix; everything else matches exactly.
    """
    return entry_pos == nst or (nst == "PM" and entry_pos.startswith("PM"))


def _syllables(transcription: str) -> tuple[str, ...]:
    """Split a transcription at its boundaries: ``$`` syllable, ``_`` word."""
    return tuple(s for s in transcription.replace("_", "$").split("$") if s)


class NstLexicon:
    """Read-only resolver over the built NST SQLite database.

    The connection opens lazily on first use — never on the import path — and
    is reused for the instance's lifetime.
    """

    def __init__(self, db_path: Path = DB_PATH) -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def _connect(self) -> sqlite3.Connection:
        if not self._db_path.exists():
            raise FileNotFoundError(
                f"Pronunciation lexicon not built: {self._db_path} is missing. "
                f"Build it from the committed extract with: {BUILD_COMMAND}"
            )
        self._conn = sqlite3.connect(self._db_path.as_uri() + "?mode=ro", uri=True)
        return self._conn

    def close(self) -> None:
        """Close the lazily-opened connection, if one was ever made.

        The connection is opened on first use and was previously released only
        by garbage collection, which emits a ResourceWarning and — at a call
        site that constructs a lexicon per word — can exhaust file descriptors
        before the collector runs (tunatale-a5p2).  Idempotent.
        """
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> NstLexicon:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _rows(self, word: str) -> list[tuple[str, str, int]]:
        conn = self._conn if self._conn is not None else self._connect()
        return list(
            conn.execute(
                "SELECT pos, transcription, certainty FROM entries WHERE word = ? "
                "ORDER BY pos, transcription, certainty",
                (word,),
            )
        )

    def _finalists(self, word: str) -> tuple[str, list[tuple[str, str, int]]]:
        """(normalised word, the minimum-certainty rows) — the shared front half.

        Certainty reduction happens FIRST, before any POS narrowing, and both
        :meth:`resolve` and :meth:`candidate_transcriptions` must apply it
        identically or they would disagree about what counts as ambiguous.
        """
        w = word.strip()
        rows = self._rows(w)
        if not rows and w.lower() != w:
            rows = self._rows(w.lower())
        if not rows:
            return w, []
        floor = min(certainty for _pos, _transcription, certainty in rows)
        return w, [row for row in rows if row[2] == floor]

    def candidate_transcriptions(self, word: str, upos: str | None = None) -> frozenset[str]:
        """Every reading :meth:`resolve` would have had to choose between.

        Empty when the word is absent. Exposed so a caller that needs only PART
        of a word can ask whether the ambiguity actually touches that part: two
        readings of ``sporet`` differ at the second syllable (``rə`` vs ``rət``)
        and agree at the first, so a chunk covering only the first is not
        ambiguous at all. Resolving the whole word remains a refusal to guess.

        When *upos* is provided, candidates are filtered to the matching NST POS
        — so AMBIGUOUS_POS_DIDNT_HELP callers get only the readings the POS tag
        pointed at, not the full pool.
        """
        _w, finalists = self._finalists(word)
        if upos:
            nst = _upos_to_nst(upos)
            if nst is not None:
                finalists = [(p, t, c) for p, t, c in finalists if _pos_matches(p, nst)]
        return frozenset(transcription for _pos, transcription, _certainty in finalists)

    def resolve(self, word: str, upos: str | None = None) -> LexiconResolution:
        """Resolve *word* (with optional UPOS hint) to ONE typed outcome."""
        w, finalists = self._finalists(word)
        if not finalists:
            return LexiconResolution(LexiconOutcome.ABSENT, w)
        rows = self._rows(w) or self._rows(w.lower())
        readings = {transcription for _pos, transcription, _certainty in finalists}
        if len(readings) == 1:
            transcription = next(iter(readings))
            return LexiconResolution(
                LexiconOutcome.RESOLVED,
                w,
                transcription=transcription,
                syllables=_syllables(transcription),
                pos=finalists[0][0],
                n_entries=len(rows),
                n_readings=1,
            )

        nst = _upos_to_nst(upos) if upos else None
        if nst is not None:
            hits = {t for p, t, _c in finalists if _pos_matches(p, nst)}
            if len(hits) == 1:
                transcription = next(iter(hits))
                return LexiconResolution(
                    LexiconOutcome.RESOLVED,
                    w,
                    transcription=transcription,
                    syllables=_syllables(transcription),
                    pos=nst,
                    n_entries=len(rows),
                    n_readings=len(readings),
                )
            return LexiconResolution(
                LexiconOutcome.AMBIGUOUS_POS_DIDNT_HELP, w, n_entries=len(rows), n_readings=len(readings)
            )
        return LexiconResolution(LexiconOutcome.AMBIGUOUS_NO_POS, w, n_entries=len(rows), n_readings=len(readings))


def create_nst_lexicon() -> NstLexicon:
    """Zero-arg factory registered on the plugin's ``LanguageConfig``."""
    return NstLexicon(DB_PATH)


def build_lexicon_db(
    extract_path: Path = EXTRACT_PATH,
    db_path: Path = DB_PATH,
    batch_size: int = _INSERT_BATCH,
) -> None:
    """Build the indexed SQLite database from the committed gzip extract.

    Overwrites any existing build. Used by ``scripts/build_nst_lexicon.py`` and
    by tests (against tiny fixture extracts — never the 44 MB real artifact).

    ``batch_size`` is injectable ONLY so the mid-loop flush is reachable from a
    fixture: at the real 50,000 it would need a 50k-row test file to execute,
    and a `# pragma: no cover` there would be hiding an untested write path,
    not marking an unreachable one. Callers should leave it at the default.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # Genuinely overwrite, as the docstring promises. Without this the CREATE
    # TABLE below raises "table entries already exists" on a rebuild, which
    # makes the build non-idempotent and unusable as a CI step.
    db_path.unlink(missing_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE entries ("
            "word TEXT NOT NULL, pos TEXT NOT NULL, "
            "transcription TEXT NOT NULL, certainty INTEGER NOT NULL)"
        )
        batch: list[tuple[str, str, str, int]] = []
        with gzip.open(extract_path, "rt", encoding="utf-8") as fh:
            for line in fh:
                fields = line.rstrip("\n").split("\t")
                if len(fields) != 4:
                    raise ValueError(f"Malformed line in {extract_path}: {line!r}")
                batch.append((fields[0], fields[1], fields[2], int(fields[3])))
                if len(batch) >= batch_size:
                    conn.executemany("INSERT INTO entries VALUES (?, ?, ?, ?)", batch)
                    batch.clear()
        if batch:
            conn.executemany("INSERT INTO entries VALUES (?, ?, ?, ?)", batch)
        conn.execute("CREATE INDEX idx_entries_word ON entries(word)")
        conn.commit()
    finally:
        conn.close()
