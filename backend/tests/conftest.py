"""Pytest configuration for TunaTale test suite."""

import contextlib
import json
import os
import sqlite3
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import pytest

from app.anki.sync import CardRecord, NoteRecord

pytest.register_assert_rewrite("tests.conftest")


def anki_day_anchor(today: date) -> datetime:
    """A UTC instant guaranteed INSIDE the current Anki-day window — its 4 AM
    rollover start (the count queries treat the start as inclusive).

    Date-sensitive badge/count fixtures must stamp ``last_review`` /
    ``introduced_at`` here, NOT at a naive ``today 12:00``. In the
    ``[midnight, 4 AM)`` local rollover band ``_anki_day_bounds_utc`` shifts the
    active window back a day, so an afternoon ``today`` timestamp lands *outside*
    it and reviewed-/introduced-today counts read 0 — which flaked these tests
    whenever CI ran in that band (~00:00–04:00 UTC). The window start is always
    ``<= now`` and inside ``[start, end)``, so it counts in every wall-clock hour.
    """
    from app.srs.database import _anki_day_bounds_utc

    start_iso, _ = _anki_day_bounds_utc(today)
    return datetime.fromisoformat(start_iso)


def anki_prev_day_anchor(today: date) -> datetime:
    """A UTC instant guaranteed BEFORE the current Anki-day window (one second
    before its 4 AM start) — the robust "reviewed yesterday" stamp.

    A naive ``yesterday 12:00`` is unsafe: in the ``[midnight, 4 AM)`` rollover
    band the active window is ``[yesterday 4 AM, today 4 AM)``, so yesterday noon
    falls *inside* it and an "excludes yesterday" assertion flips.
    """
    return anki_day_anchor(today) - timedelta(seconds=1)


@pytest.fixture(autouse=True)
def _settings_overrides(monkeypatch, tmp_path):
    """Override settings that touch user data to tmp_path so tests never write to ~/.tunatale.

    Why it exists: a full-suite run on a checkout *without* this fixture wrote
    ~30 synthetic-collection backups into the real ``~/.tunatale/anki-backups``
    and the keep-30 retention cap pruned every real 17 MB snapshot.

    Also pins the lemmatizer to the deterministic ``lowercase`` default so the
    suite never depends on the developer's ``.env`` ``lemmatizer_type`` (a local
    ``classla`` flag would change computed lemmas and break lemma-sensitive tests).
    The module-level ``app.api.srs._lemmatizer`` is bound once at import, so it is
    re-bound here too; tests that want a stub still monkeypatch it themselves.
    """
    from app.config import settings
    from app.srs.lemmatizer import get_lemmatizer

    monkeypatch.setattr(settings, "anki_backup_dir", tmp_path / "anki-backups")
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_path / 'tunatale.db'}")
    # Peer-sync touches TT's own collection and reads the user's real collection (for
    # curDeck mirroring); pin both to tmp_path so no test reads or mutates the real
    # ~/.tunatale/tt_collection.anki2 or ~/Library/.../collection.anki2.
    monkeypatch.setattr(settings, "tt_collection_path", tmp_path / "tt_collection.anki2")
    monkeypatch.setattr(settings, "anki_collection_path", tmp_path / "collection.anki2")
    monkeypatch.setattr(settings, "sync_log", tmp_path / "logs" / "sync.log")
    # Non-empty so _resolve_sync_password short-circuits and tests never shell out to
    # the real macOS Keychain. Tests of the Keychain path override this to "". The
    # gated --run-peer-sync integration test provides a real throwaway password via
    # the environment, so honour that when present rather than clobbering it.
    # `... or "test-sync-pw"`, not a default arg: a blanked `.env` puts `sync_password=`
    # (empty) into os.environ, and an empty value must still fall back to the dummy so
    # _resolve_sync_password short-circuits (an empty value would shell out to the mocked
    # `security` and consume a driver side-effect). Lowercase matches the Pydantic field
    # and the other peer-sync env vars, so SIM112's uppercase rule doesn't fit.
    monkeypatch.setattr(settings, "sync_password", os.environ.get("sync_password") or "test-sync-pw")  # noqa: SIM112
    monkeypatch.setattr(settings, "lemmatizer_type", "lowercase")
    get_lemmatizer.cache_clear()
    monkeypatch.setattr("app.api.srs._lemmatizer", get_lemmatizer())


@pytest.fixture(autouse=True)
def _autoclose_sqlite_connections(monkeypatch):
    """Track and close every sqlite3.Connection opened during a test.

    Belt-and-suspenders safety net: individual tests should still wrap
    sqlite3.connect calls in `with closing(...)`. This fixture catches any
    that slip through and prevents ResourceWarning noise from hiding real
    warnings.
    """
    real_connect = sqlite3.connect
    opened: list[sqlite3.Connection] = []

    def tracking_connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        opened.append(conn)
        return conn

    monkeypatch.setattr(sqlite3, "connect", tracking_connect)
    yield
    for conn in opened:
        with contextlib.suppress(sqlite3.ProgrammingError):
            conn.close()


@pytest.fixture
def language():
    """Slovene language configuration."""
    from app.models.language import Language

    return Language.slovene()


@pytest.fixture
def srs_db():
    """In-memory SRS database, empty at test start."""
    from app.srs.database import SRSDatabase

    with SRSDatabase(":memory:") as db:
        yield db


def make_card_record(
    *,
    anki_card_id: int = 90010,
    ord: int = 0,
    queue: int = 2,
    reps: int = 5,
    lapses: int = 0,
    stability: float = 5.0,
    difficulty: float = 4.5,
    due_date: date | None = None,
    due_at: datetime | None = None,
    fsrs_known: bool = True,
    card_type: int = 0,  # Anki's cards.type (0=New, 1=Learn, 2=Review, 3=Relearn)
    **overrides,
) -> CardRecord:
    """Create a CardRecord with sensible defaults for sync tests."""
    if due_at is None:
        if due_date is None:
            due_date = date.today()
        due_at = datetime.combine(due_date, time(4, 0), tzinfo=UTC)
    return CardRecord(
        anki_card_id=anki_card_id,
        ord=ord,
        queue=queue,
        reps=reps,
        lapses=lapses,
        card_type=card_type,
        stability=stability,
        difficulty=difficulty,
        due_at=due_at,
        fsrs_known=fsrs_known,
        **overrides,
    )


def make_note_record(
    *,
    anki_note_id: int = 9001,
    anki_guid: str | None = None,
    l2_text: str = "banka",
    translation: str = "bank",
    sentence_translation: str = "",
    disambig_key: str = "",
    mod: int = 0,
    cards: list | None = None,
    **overrides,
) -> NoteRecord:
    """Create a NoteRecord with sensible defaults for sync tests."""
    from app.common.guid import compute_guid

    if anki_guid is None:
        anki_guid = compute_guid(l2_text, "sl", disambig_key)
    if cards is None:
        cards = [make_card_record()]
    return NoteRecord(
        anki_note_id=anki_note_id,
        anki_guid=anki_guid,
        l2_text=l2_text,
        translation=translation,
        sentence_translation=sentence_translation,
        note="",
        disambig_key=disambig_key,
        mod=mod,
        cards=cards,
        **overrides,
    )


def build_minimal_anki_db(
    tmp_path: Path,
    deck_name: str = "0. Slovene",
    deck_id: int = 12345,
    use_decks_table: bool = False,
    col_crt: int | None = None,
    decks_table_with_real_common: bool = False,
) -> Path:
    """Create a minimal collection.anki2 for testing.

    Contains 5 notes with 2 cards each (recognition ord=0, production ord=1).
    Note 5 (knjiga) has empty cards.data to trigger the fallback log.
    Production card of note 3 (miza) is suspended (queue=-1).
    col_crt defaults to 1704067200 (2024-01-01 UTC) so tests can compute expected due_dates.

    When *decks_table_with_real_common* is True, the decks row gets a real
    protobuf ``common`` blob (field 3=4513, field 7=37803) instead of ``'{}'``.
    Implies ``use_decks_table=True``.
    """
    if col_crt is None:
        col_crt = 1704067200  # 2024-01-01 00:00:00 UTC
    db_path = tmp_path / "collection.anki2"
    conn = sqlite3.connect(str(db_path))

    conn.execute("""CREATE TABLE col (
        id INTEGER, crt INTEGER, mod INTEGER, scm INTEGER, ver INTEGER,
        dty INTEGER, usn INTEGER, ls INTEGER, conf TEXT, models TEXT,
        decks TEXT, dconf TEXT, tags TEXT)""")
    conn.execute("""CREATE TABLE notes (
        id INTEGER, guid TEXT, mid INTEGER, mod INTEGER, usn INTEGER,
        tags TEXT, flds TEXT, sfld TEXT, csum INTEGER, flags INTEGER, data TEXT)""")
    conn.execute("""CREATE TABLE cards (
        id INTEGER, nid INTEGER, did INTEGER, ord INTEGER, mod INTEGER,
        usn INTEGER, type INTEGER, queue INTEGER, due INTEGER, ivl INTEGER,
        factor INTEGER, reps INTEGER, lapses INTEGER, left INTEGER,
        odue INTEGER, odid INTEGER, flags INTEGER, data TEXT)""")
    conn.execute("""CREATE TABLE revlog (
        id INTEGER, cid INTEGER, usn INTEGER, ease INTEGER, ivl INTEGER,
        lastIvl INTEGER, factor INTEGER, time INTEGER, type INTEGER)""")

    if use_decks_table or decks_table_with_real_common:
        conn.execute("CREATE TABLE decks (id INTEGER, name TEXT, mtime_secs INTEGER, usn INTEGER, common BLOB)")
        if decks_table_with_real_common:
            _REAL_BLOB = bytes.fromhex("18A12338ABA702")
            conn.execute("INSERT INTO decks VALUES (?, ?, 0, 0, ?)", (deck_id, deck_name, _REAL_BLOB))
        else:
            conn.execute("INSERT INTO decks VALUES (?, ?, 0, 0, '{}')", (deck_id, deck_name))
        decks_json = json.dumps({})
    else:
        decks_json = json.dumps(
            {
                "1": {"id": 1, "name": "Default"},
                str(deck_id): {"id": deck_id, "name": deck_name},
            }
        )

    conn.execute(
        "INSERT INTO col VALUES (1,?,0,0,11,0,0,0,'{}','{}',?,'{}','{}')",
        (col_crt, decks_json),
    )

    # 5 notes: plain text, HTML class="slovene", plain, plain, plain
    notes = [
        (1001, "anki_guid_1", 1000001, 0, 0, "", "banka\x1fbank", "banka", 0, 0, ""),
        (1002, "anki_guid_2", 1000001, 0, 0, "", '<span class="slovene">hiša</span>\x1fhouse', "hiša", 0, 0, ""),
        (1003, "anki_guid_3", 1000001, 0, 0, "", "miza\x1ftable", "miza", 0, 0, ""),
        (1004, "anki_guid_4", 1000001, 0, 0, "", "stol\x1fchair", "stol", 0, 0, ""),
        (1005, "anki_guid_5", 1000001, 0, 0, "", "knjiga\x1fbook", "knjiga", 0, 0, ""),
    ]
    conn.executemany("INSERT INTO notes VALUES (?,?,?,?,?,?,?,?,?,?,?)", notes)

    # 10 cards (2 per note). Note 1005 has empty data. Note 1003 production is suspended.
    cards = []
    for note_id, _, _, _, _, _, _, _, _, _, _ in notes:
        if note_id == 1005:
            rec_data = ""
            prod_data = ""
        else:
            rec_data = json.dumps({"s": 10.5, "d": 4.8})
            prod_data = json.dumps({"s": 5.2, "d": 5.1})
        prod_queue = -1 if note_id == 1003 else 2
        cards.append((note_id * 10, note_id, deck_id, 0, 0, 0, 2, 2, 10, 21, 2500, 5, 0, 0, 0, 0, 0, rec_data))
        cards.append(
            (note_id * 10 + 1, note_id, deck_id, 1, 0, 0, 2, prod_queue, 20, 14, 2500, 3, 0, 0, 0, 0, 0, prod_data)
        )

    conn.executemany("INSERT INTO cards VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", cards)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def fake_anki_db(tmp_path):
    """Minimal collection.anki2 with 5 notes (deck '0. Slovene'), legacy format."""
    return build_minimal_anki_db(tmp_path)


@pytest.fixture
def fake_anki_db_modern(tmp_path):
    """Minimal collection.anki2 with separate decks table (modern Anki format)."""
    return build_minimal_anki_db(tmp_path, use_decks_table=True)


# Basic notetype id the real user's collection uses (kept for tests that need a
# matching pre-migration mid).
BASIC_NOTETYPE_MID = 1519651961633

# ``fake_anki_db_slovene_pairs`` note ids, exposed so assertions can reference them
# without repeating magic numbers. 2001/2002 are the ``jabolko`` pair, 2007..2010
# are the four homonym ``barva`` notes, etc. See ``build_slovene_pairs_anki_db``.
SLOVENE_PAIRS_IDS = {
    "jabolko_recognition": 2001,
    "jabolko_production": 2002,
    "pes_recognition": 2003,
    "pes_production": 2004,
    "macka_recognition": 2005,
    "macka_production": 2006,
    "barva_color_recognition": 2007,
    "barva_color_production": 2008,
    "barva_paint_recognition": 2009,
    "barva_paint_production": 2010,
    "hisa_recognition_only": 2011,
    "okno_production_only": 2012,
    "zmeda_unknown": 2013,
}


def _recognition_fields(
    slovene: str, english: str, audio_base: str, image: str, grammar: str = "", note: str = ""
) -> str:
    front = f'[sound:{audio_base}.mp3]<div class="slovene">{slovene}</div>'
    back = f'<img src="{image}"><div class="english">{english}</div>'
    if grammar:
        back += f'<div class="gram">{grammar}</div>'
    if note:
        back += f'<div class="note">{note}</div>'
    return front + "\x1f" + back


def _production_fields(
    slovene: str, english: str, audio_base: str, image: str, grammar: str = "", note: str = ""
) -> str:
    front = f'<div class="img"><img src="{image}"></div>'
    back = f'[sound:{audio_base}.mp3]<div class="slovene">{slovene}</div><div class="english">{english}</div>'
    if grammar:
        back += f'<div class="gram">{grammar}</div>'
    if note:
        back += f'<div class="note">{note}</div>'
    return front + "\x1f" + back


def _unknown_fields(slovene: str, english: str) -> str:
    # Bare text in fields[0] with no [sound:] prefix and no <div class="img">
    # wrapper — gives ``parse_notes`` no direction hint.
    return f'<div class="slovene">{slovene}</div>' + "\x1f" + f'<div class="english">{english}</div>'


def build_slovene_pairs_anki_db(tmp_path: Path) -> Path:
    """Build a modern-format Anki DB with 13 notes modeling the real user's 0. Slovene deck.

    Layout:
      - 3 paired words (jabolko, pes, mačka)                 — 6 notes, 6 cards
      - 1 homonym "barva" (2 meanings × 2 directions)         — 4 notes, 4 cards
      - 1 recognition-only singleton (hiša)                   — 1 note,  1 card
      - 1 production-only singleton (okno)                    — 1 note,  1 card
      - 1 unknown-direction note (zmeda)                      — 1 note,  1 card

    Cards from each pair-partner get distinct ``revlog`` rows so the merge-apply
    tests can assert that reparenting a card does not drop review history.
    """
    import sqlite3

    db_path = tmp_path / "collection.anki2"
    deck_id = 12345
    basic_mid = BASIC_NOTETYPE_MID

    conn = sqlite3.connect(str(db_path))
    conn.execute("""CREATE TABLE col (
        id INTEGER, crt INTEGER, mod INTEGER, scm INTEGER, ver INTEGER,
        dty INTEGER, usn INTEGER, ls INTEGER, conf TEXT, models TEXT,
        decks TEXT, dconf TEXT, tags TEXT)""")
    conn.execute("""CREATE TABLE notes (
        id INTEGER PRIMARY KEY, guid TEXT, mid INTEGER, mod INTEGER, usn INTEGER,
        tags TEXT, flds TEXT, sfld TEXT, csum INTEGER, flags INTEGER, data TEXT)""")
    conn.execute("""CREATE TABLE cards (
        id INTEGER PRIMARY KEY, nid INTEGER, did INTEGER, ord INTEGER, mod INTEGER,
        usn INTEGER, type INTEGER, queue INTEGER, due INTEGER, ivl INTEGER,
        factor INTEGER, reps INTEGER, lapses INTEGER, left INTEGER,
        odue INTEGER, odid INTEGER, flags INTEGER, data TEXT)""")
    conn.execute("""CREATE TABLE revlog (
        id INTEGER PRIMARY KEY, cid INTEGER, usn INTEGER, ease INTEGER, ivl INTEGER,
        lastIvl INTEGER, factor INTEGER, time INTEGER, type INTEGER)""")
    conn.execute("""CREATE TABLE decks (
        id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER, usn INTEGER, common BLOB, kind BLOB)""")
    conn.execute("""CREATE TABLE notetypes (
        id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER, usn INTEGER, config BLOB)""")
    conn.execute("""CREATE TABLE fields (
        ntid INTEGER, ord INTEGER, name TEXT, config BLOB, PRIMARY KEY (ntid, ord))""")
    conn.execute("""CREATE TABLE templates (
        ntid INTEGER, ord INTEGER, name TEXT, mtime_secs INTEGER, usn INTEGER, config BLOB, PRIMARY KEY (ntid, ord))""")

    col_crt = 1704067200  # 2024-01-01 UTC
    conn.execute(
        "INSERT INTO col VALUES (1,?,0,0,18,0,0,0,'{}','{}','{}','{}','{}')",
        (col_crt,),
    )
    conn.executemany(
        "INSERT INTO decks VALUES (?, ?, 0, 0, x'', x'')",
        [(1, "Default"), (deck_id, "0. Slovene")],
    )

    # Pre-migration Basic notetype (1 template). Minimal configs — tests don't
    # exercise Anki's renderer so opaque bytes are fine.
    conn.execute("INSERT INTO notetypes VALUES (?, 'Basic', 0, 0, x'')", (basic_mid,))
    conn.executemany(
        "INSERT INTO fields VALUES (?, ?, ?, x'')",
        [(basic_mid, 0, "Front"), (basic_mid, 1, "Back")],
    )
    conn.execute(
        "INSERT INTO templates VALUES (?, 0, 'Card 1', 0, 0, x'')",
        (basic_mid,),
    )

    notes = [
        # id, guid, fields, sfld
        (2001, "guid_jab_rec", _recognition_fields("jabolko", "apple", "sl_jabolko", "jabolko.jpg", "n."), "jabolko"),
        (2002, "guid_jab_pro", _production_fields("jabolko", "apple", "sl_jabolko", "jabolko.jpg", "n."), "jabolko"),
        (2003, "guid_pes_rec", _recognition_fields("pes", "dog", "sl_pes", "dog.jpg"), "pes"),
        (2004, "guid_pes_pro", _production_fields("pes", "dog", "sl_pes", "dog.jpg"), "pes"),
        (2005, "guid_mac_rec", _recognition_fields("mačka", "cat", "sl_macka", "cat.jpg"), "mačka"),
        (2006, "guid_mac_pro", _production_fields("mačka", "cat", "sl_macka", "cat.jpg"), "mačka"),
        # Homonym: same slovene "barva" maps to two distinct english meanings.
        # User flags these in the note field.
        (
            2007,
            "guid_barva_col_rec",
            _recognition_fields("barva", "color", "sl_barva_color", "color.jpg", note="⚠ same word as paint"),
            "barva",
        ),
        (
            2008,
            "guid_barva_col_pro",
            _production_fields("barva", "color", "sl_barva_color", "color.jpg", note="⚠ same word as paint"),
            "barva",
        ),
        (
            2009,
            "guid_barva_pai_rec",
            _recognition_fields("barva", "paint", "sl_barva_paint", "paint.jpg", note="⚠ same word as color"),
            "barva",
        ),
        (
            2010,
            "guid_barva_pai_pro",
            _production_fields("barva", "paint", "sl_barva_paint", "paint.jpg", note="⚠ same word as color"),
            "barva",
        ),
        # Singletons
        (2011, "guid_hisa_rec", _recognition_fields("hiša", "house", "sl_hisa", "house.jpg"), "hiša"),
        (2012, "guid_okno_pro", _production_fields("okno", "window", "sl_okno", "window.jpg"), "okno"),
        # Unknown-direction: no [sound:] in field[0], no <div class="img"> wrapper
        (2013, "guid_zmeda_unk", _unknown_fields("zmeda", "confusion"), "zmeda"),
    ]
    conn.executemany(
        "INSERT INTO notes VALUES (?, ?, ?, 0, 0, '', ?, ?, 0, 0, '')",
        [(nid, guid, basic_mid, fields, sfld) for (nid, guid, fields, sfld) in notes],
    )

    # Each note gets exactly 1 card (ord=0, Basic has 1 template). Recognition
    # cards have reps=5 so tests can verify FSRS state preservation. Production
    # partner cards have reps=1 where present — real data shows production cards
    # are almost entirely new but a few have received review.
    card_rows: list[tuple] = []
    # card_id = note_id * 10 keeps the correspondence readable in failures.
    reviewed_reps = {
        2001: 5,
        2003: 5,
        2005: 5,
        2007: 5,
        2009: 5,
        2011: 5,  # recognition
        2002: 1,
        2004: 1,
        2006: 1,
        2008: 1,
        2010: 1,  # production w/ reps
        2012: 0,
        2013: 0,
    }
    due_by_note = {
        2001: 10,
        2002: 20,
        2003: 11,
        2004: 21,
        2005: 12,
        2006: 22,
        2007: 13,
        2008: 23,
        2009: 14,
        2010: 24,
        2011: 15,
        2012: 25,
        2013: 0,
    }
    for nid, _guid, _flds, _sfld in notes:
        reps = reviewed_reps[nid]
        queue = 0 if reps == 0 else 2
        data = json.dumps({"s": 7.5, "d": 4.8}) if reps > 0 else ""
        card_rows.append(
            (
                nid * 10,
                nid,
                deck_id,
                0,
                0,
                0,
                2 if reps > 0 else 0,
                queue,
                due_by_note[nid],
                21 if reps > 0 else 0,
                2500 if reps > 0 else 0,
                reps,
                0,
                0,
                0,
                0,
                0,
                data,
            )
        )
    conn.executemany(
        "INSERT INTO cards VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        card_rows,
    )

    # Revlog: pair partners each get one entry; homonym partners get one; singletons
    # and unknown-direction notes have none. Gives us 10 revlog rows.
    revlog_rows: list[tuple] = []
    revlog_id = 1_700_000_000_000
    for nid in (2001, 2002, 2003, 2004, 2005, 2006, 2007, 2008, 2009, 2010):
        revlog_rows.append((revlog_id, nid * 10, 0, 3, 21, 10, 2500, 1200, 1))
        revlog_id += 1
    conn.executemany(
        "INSERT INTO revlog VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        revlog_rows,
    )
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def fake_anki_db_slovene_pairs(tmp_path):
    """Realistic Slovene-deck fixture with pairs, homonyms, singletons, unknowns."""
    return build_slovene_pairs_anki_db(tmp_path)


def seed_direction(
    db,
    *,
    text: str,
    translation: str = "t",
    direction=None,
    state=None,
    due_date: date | None = None,
    due_at: datetime | None = None,
    stability: float = 1.0,
    difficulty: float = 5.0,
    reps: int = 0,
    lapses: int = 0,
    anki_card_id: int = 0,
    **extra_dstate,
) -> int:
    """Create one collocation + one direction in TT's DB for tests.

    Returns the row_id so tests can pass it to API routes or
    db.update_direction_by_id().
    """
    from app.models.srs_item import Direction, DirectionState, SRSState
    from app.models.syntactic_unit import SyntacticUnit

    _direction = Direction.RECOGNITION if direction is None else direction
    _state = SRSState.REVIEW if state is None else state

    unit = SyntacticUnit(text=text, translation=translation, word_count=1, difficulty=1, source="test")
    db.add_collocation(unit, language_code="sl")
    item = db.get_collocation(text)
    assert item is not None, f"seed_direction: collocation '{text}' not found after add_collocation"
    with db._get_conn() as conn:
        row_id = conn.execute("SELECT id FROM collocations WHERE text = ?", (text,)).fetchone()
    assert row_id is not None, f"seed_direction: id for '{text}' not found"
    if due_at is None:
        due_at = datetime.combine(due_date or date.today(), time(4, 0), tzinfo=UTC)
    dstate = DirectionState(
        direction=_direction,
        state=_state,
        due_at=due_at,
        stability=stability,
        difficulty=difficulty,
        reps=reps,
        lapses=lapses,
        anki_card_id=anki_card_id,
        **extra_dstate,
    )
    db.update_direction(item.guid, _direction, dstate)
    return row_id["id"]


_CASSETTES_DIR = Path(__file__).parent / "cassettes"


# Re-export Anki oracle fixtures so tests anywhere in the suite can request them.
from tests.anki_oracle.harness_fixtures import anki_queue, synthetic_collection  # noqa: E402, F401


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--llm-mode",
        choices=["mock", "live", "record", "patch"],
        default="mock",
        help="LLM mode for cassette fixtures: mock (replay), live, record, or patch.",
    )
    parser.addoption(
        "--run-oracle",
        action="store_true",
        default=False,
        help="Run @pytest.mark.oracle tests (spawns `uv run --with anki` subprocesses).",
    )
    parser.addoption(
        "--run-classla",
        action="store_true",
        default=False,
        help="Run @pytest.mark.classla tests (exercises the real classla Slovene pipeline).",
    )
    parser.addoption(
        "--run-peer-sync",
        action="store_true",
        default=False,
        help="Run @pytest.mark.peer_sync tests (requires self-host sync server).",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "oracle: requires --run-oracle (drives Anki's scheduler via subprocess).",
    )
    config.addinivalue_line(
        "markers",
        "classla: requires --run-classla (exercises the real classla Slovene pipeline).",
    )
    config.addinivalue_line(
        "markers",
        "peer_sync: requires --run-peer-sync (drives Anki sync subprocess against a self-host server).",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if not config.getoption("--run-classla"):
        skip_classla = pytest.mark.skip(reason="--run-classla not specified")
        for item in items:
            if "classla" in item.keywords:
                item.add_marker(skip_classla)
    if not config.getoption("--run-peer-sync"):
        skip_peer_sync = pytest.mark.skip(reason="--run-peer-sync not specified")
        for item in items:
            if "peer_sync" in item.keywords:
                item.add_marker(skip_peer_sync)
    if not config.getoption("--run-oracle"):
        skip_oracle = pytest.mark.skip(reason="--run-oracle not specified")
        for item in items:
            if "oracle" in item.keywords:
                item.add_marker(skip_oracle)


@pytest.fixture
def llm_mode(request: pytest.FixtureRequest) -> str:
    return request.config.getoption("--llm-mode")  # type: ignore[return-value]


@pytest.fixture
def api_app_state():
    """Seed app.state with fresh in-memory SRSDatabase, ContentStore, and Language.

    Use in API tests that exercise routes reading from app.state. Yields the
    SRSDatabase for direct seeding inside the test, then tears down all four
    state attributes.
    """
    from app.main import app
    from app.models.language import Language
    from app.srs.database import SRSDatabase
    from app.storage.store import ContentStore

    db = SRSDatabase(":memory:")
    store = ContentStore(":memory:")
    app.state.srs_db = db
    app.state.content_store = store
    app.state.language = Language.slovene()
    try:
        yield db
    finally:
        db.close()
        store.close()
        for attr in ("srs_db", "content_store", "language", "llm", "llm_client"):
            if hasattr(app.state, attr):
                delattr(app.state, attr)


@pytest.fixture
async def cassette_llm(request: pytest.FixtureRequest, llm_mode: str):
    """Yield a CassetteLLMClient configured for the current --llm-mode."""
    from app.llm.cassette import CassetteLLMClient

    cls_name = request.node.cls.__name__ if request.node.cls else "_noclass"
    test_name = request.node.name
    cassette_path = _CASSETTES_DIR / f"{cls_name}__{test_name}.json"

    if llm_mode == "mock":
        if not cassette_path.exists():
            pytest.skip(f"No cassette at {cassette_path} — run with --llm-mode=record first.")
        client = CassetteLLMClient(mode="mock", cassette_path=cassette_path)
        yield client
        return

    if llm_mode == "patch" and not cassette_path.exists():
        pytest.skip(f"No cassette at {cassette_path} — run with --llm-mode=record first.")

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        pytest.skip("GROQ_API_KEY not set — cannot run in live/record/patch mode.")

    from app.llm.client import LLMClient

    real_client = LLMClient(groq_api_key=api_key)
    client = CassetteLLMClient(mode=llm_mode, cassette_path=cassette_path, real_client=real_client)
    yield client
    client.save()
