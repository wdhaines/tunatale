"""Copy one learner's deck into a new learner's file: same cards, never studied.

For a second account learning a language the owner already has a deck in
(tunatale-98zf.1; the first case is Cebuano, 2026-09-26). The new learner has no
Anki, no lessons and no history, so the copy keeps the CARDS and what the queue
needs to run standalone, and drops or resets everything that records the source
learner — their reviews, their schedule, their lessons, their Anki pairing.

Decided by the user, 2026-09-26:

- Cards the source already studied go IN FRONT of the new-card queue, in the
  order the source first reviewed them. That order is the curriculum the source
  was actually given (cognate head starts, function-word clozes, then the rest),
  and the source's position for them is gone — ``anki_due`` holds a due day once
  a card leaves NEW.
- Suspended cards stay suspended (with their schedule reset, so unsuspending
  makes them new).
- FSRS parameters are the defaults, not the source's weights: those were trained
  on the source's memory.

Every table and every column is classified below. A table or column the seed
does not know is a REFUSAL, not a silent copy: new schema has to be decided
here, because the failure it prevents — one learner's history travelling inside
another learner's file — has no visible symptom.

The source is opened read-only and never written. The copy is VACUUMed before it
is published, because deleted rows otherwise survive in free pages, and those
rows are the source learner's review history.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from app.srs.anki_mirror.cache_registry import REGISTRY, CacheSource
from app.srs.anki_mirror.queue_stats import new_cards_gather_descending
from app.srs.anki_mirror.rollover import anki_today, due_at_rollover_utc
from app.srs.database import SRSDatabase
from app.storage.store import ContentStore

#: Copied as-is: card content and derived caches that record nothing about a learner.
KEEP_TABLES = frozenset(
    {
        "collocation_tags",
        "media",
        "ignored_lemmas",
        "cloze_sentence_cache",
        "image_query_cache",
        "lemma_analysis_cache",
        "sqlite_sequence",
    }
)
#: Emptied: the source learner's history, lessons and sync bookkeeping.
CLEAR_TABLES = frozenset(
    {
        "tt_revlog",
        "lesson_listens",
        "lesson_reviews",
        "pending_listen_grades",
        "review_sessions",
        "curricula",
        "lessons",
        "audio_files",
        "violations",
        "sync_conflicts",
    }
)
#: Rewritten row by row below.
TRANSFORM_TABLES = frozenset({"collocations", "collocation_directions", "anki_state_cache"})

#: collocations: content, kept.
COLLOCATION_KEEP = frozenset(
    {
        "id",
        "text",
        "translation",
        "language_code",
        "word_count",
        "unit_difficulty",
        "source",
        "corpus_frequency",
        "lemma",
        "guid",
        "disambig_key",
        "created_at",
        "updated_at",
        "grammar",
        "note",
        "source_sentence",
        "card_type",
        "sentence_translation",
        "lemma_key",
        "article",
        "extras",
        "base_collocation_id",
        "image_unavailable_at",
    }
)
#: collocations: the Anki pairing and pointers into the source's lessons.
COLLOCATION_RESET = {
    "anki_note_id": None,
    "dirty_fields": "",
    "last_synced_at": None,
    "source_lesson_id": None,
    "source_line_index": None,
}

#: collocation_directions: identity, and the new-card position (rewritten separately).
DIRECTION_KEEP = frozenset({"collocation_id", "direction", "anki_due"})
#: collocation_directions: the schedule and the Anki pairing, back to a NEW card's
#: values. ``state`` and ``due_at`` are set separately (suspended stays suspended).
DIRECTION_RESET = {
    "stability": 1.0,
    "fsrs_difficulty": 5.0,
    "reps": 0,
    "lapses": 0,
    "last_review": None,
    "last_review_time_ms": 0,
    "anki_card_id": None,
    "anki_card_mod": None,
    "dirty_fsrs": 0,
    "last_synced_at": None,
    "last_rating": None,
    "left": None,
    "prior_state": None,
    "prior_left": None,
    "prior_stability": None,
    "introduced_at": None,
    "bury_kind": None,
    "known_prior_state": None,
    "known_prior_stability": None,
    "known_prior_due_at": None,
    "fsrs_force_next": 0,
}
DIRECTION_SET_SEPARATELY = frozenset({"state", "due_at"})

#: anki_state_cache keys that are deck CONFIG, which a sync-less deck can only
#: get from here — minus the source's trained FSRS weights.
CONFIG_KEYS = frozenset(
    name for name, spec in REGISTRY.items() if spec.source is CacheSource.ANKI_CONFIG and name != "fsrs_params"
)


class SeedRefused(RuntimeError):
    """The seed will not run: an existing destination, or schema it does not know."""


@dataclass(frozen=True)
class SeedReport:
    cards: int
    directions: int
    moved_to_front: int
    suspended: int
    revlog_dropped: int
    lessons_dropped: int
    curricula_dropped: int
    new_without_position: int
    gather_descending: bool


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def _check_schema(conn: sqlite3.Connection) -> None:
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    unknown = tables - KEEP_TABLES - CLEAR_TABLES - TRANSFORM_TABLES
    if unknown:
        raise SeedRefused(f"tables this seed has not classified: {sorted(unknown)}")
    for table, known in (
        ("collocations", COLLOCATION_KEEP | set(COLLOCATION_RESET)),
        ("collocation_directions", DIRECTION_KEEP | set(DIRECTION_RESET) | DIRECTION_SET_SEPARATELY),
    ):
        extra = _columns(conn, table) - known
        if extra:
            raise SeedRefused(f"{table} columns this seed has not classified: {sorted(extra)}")


def _front_positions(conn: sqlite3.Connection, descending: bool) -> int:
    """Give every card the source took out of NEW a position at the front.

    Ordered by the source's first review of the card (either direction); cards
    that left NEW without one — a seeded head start never yet reviewed — follow,
    by id. Siblings share a position, as in Anki. "Front" is the end the deck
    gathers from. Returns how many cards moved.
    """
    moved = [
        row[0]
        for row in conn.execute(
            """
            SELECT c.id FROM collocations c
            LEFT JOIN (SELECT collocation_id, MIN(id) AS first FROM tt_revlog GROUP BY collocation_id) r
                   ON r.collocation_id = c.id
            WHERE EXISTS (SELECT 1 FROM collocation_directions d WHERE d.collocation_id = c.id AND d.state != 'new')
            ORDER BY r.first IS NULL, r.first, c.id
            """
        )
    ]
    if not moved:
        return 0
    marks = ",".join("?" * len(moved))
    lo, hi = conn.execute(
        f"SELECT MIN(anki_due), MAX(anki_due) FROM collocation_directions WHERE collocation_id NOT IN ({marks})",
        moved,
    ).fetchone()
    count = len(moved)
    if descending:
        top = (hi if hi is not None else 0) + count
        positions = [top - rank for rank in range(count)]
    else:
        lo = lo if lo is not None else count
        if lo < count:
            # Make room below the kept cards rather than go negative.
            conn.execute(
                f"UPDATE collocation_directions SET anki_due = anki_due + ? "
                f"WHERE anki_due IS NOT NULL AND collocation_id NOT IN ({marks})",
                [count - lo, *moved],
            )
            lo = count
        positions = [lo - count + rank for rank in range(count)]
    conn.executemany(
        "UPDATE collocation_directions SET anki_due = ? WHERE collocation_id = ?",
        list(zip(positions, moved, strict=True)),
    )
    return count


def seed_user_deck(source: Path, dest: Path) -> SeedReport:
    """Write ``dest``: ``source``'s cards as a never-studied deck. See the module docstring."""
    source, dest = Path(source), Path(dest)
    if dest.exists():
        raise SeedRefused(f"{dest} already exists — refusing to overwrite a learner's deck")
    if not source.is_file():
        raise SeedRefused(f"no source deck at {source}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    work = dest.with_name(dest.name + ".seeding")
    work.unlink(missing_ok=True)

    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        out = sqlite3.connect(work)
        src.backup(out)
        out.close()
    finally:
        src.close()

    # The source may be a schema behind this code; bring the copy current first
    # (both halves: SRS and lessons share the file), so the classification below
    # is checked against what the app will open.
    db = SRSDatabase(str(work))
    ContentStore(str(work))
    descending = new_cards_gather_descending(db)

    conn = sqlite3.connect(work)
    try:
        _check_schema(conn)

        def count(table: str, where: str = "") -> int:
            return conn.execute(f"SELECT COUNT(*) FROM {table} {where}").fetchone()[0]

        revlog, lessons, curricula = count("tt_revlog"), count("lessons"), count("curricula")
        with conn:
            moved = _front_positions(conn, descending)
            for table in sorted(CLEAR_TABLES):
                conn.execute(f"DELETE FROM {table}")
            sets = ", ".join(f"{col} = ?" for col in DIRECTION_RESET)
            conn.execute(
                f"UPDATE collocation_directions SET {sets}, due_at = ?, "
                "state = CASE WHEN state = 'suspended' THEN 'suspended' ELSE 'new' END",
                [*DIRECTION_RESET.values(), due_at_rollover_utc(anki_today()).isoformat()],
            )
            sets = ", ".join(f"{col} = ?" for col in COLLOCATION_RESET)
            conn.execute(f"UPDATE collocations SET {sets}", list(COLLOCATION_RESET.values()))
            marks = ",".join("?" * len(CONFIG_KEYS))
            conn.execute(f"DELETE FROM anki_state_cache WHERE key NOT IN ({marks})", sorted(CONFIG_KEYS))
        conn.execute("VACUUM")
        report = SeedReport(
            cards=count("collocations"),
            directions=count("collocation_directions"),
            moved_to_front=moved,
            suspended=count("collocation_directions", "WHERE state = 'suspended'"),
            revlog_dropped=revlog,
            lessons_dropped=lessons,
            curricula_dropped=curricula,
            new_without_position=count("collocation_directions", "WHERE state = 'new' AND anki_due IS NULL"),
            gather_descending=descending,
        )
    finally:
        conn.close()
    # A hard link, not a rename: it fails if ``dest`` appeared meanwhile, where
    # os.replace would silently overwrite another learner's deck.
    try:
        os.link(work, dest)
    finally:
        work.unlink()
    return report
