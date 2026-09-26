"""A new learner's copy of an existing deck: same cards, never studied (tunatale-98zf.1).

The oracles come from the bead and the user's three decisions (2026-09-26):
studied cards to the FRONT in first-review order, suspended stays suspended,
FSRS params back to defaults. The queue claim is checked through the real
queue code, not a SQL count.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from app.models.syntactic_unit import SyntacticUnit
from app.srs.anki_mirror.queue_engine import assemble_review_queue
from app.srs.database import SRSDatabase
from app.srs.user_deck_seed import (
    CLEAR_TABLES,
    CONFIG_KEYS,
    KEEP_TABLES,
    TRANSFORM_TABLES,
    SeedRefused,
    seed_user_deck,
)
from app.storage.store import ContentStore
from app.storage.user_dbs import UserDatabases

LESSON_MARKER = "SOURCE-LEARNER-LESSON-TEXT"

# text -> (state, first review id in tt_revlog or None, anki_due position/day)
CARDS = {
    "hus": ("new", None, 10),
    "bil": ("new", None, 11),
    "katt": ("review", 2_000, 900),  # studied second
    "hund": ("review", 1_000, 950),  # studied first
    "sol": ("review", None, 800),  # a seeded head start, never reviewed
    "fisk": ("suspended", 3_000, 700),
}


def _set(conn: sqlite3.Connection, text: str, **cols) -> None:
    sets = ", ".join(f"{k} = ?" for k in cols)
    conn.execute(
        f"UPDATE collocation_directions SET {sets} WHERE collocation_id = (SELECT id FROM collocations WHERE text = ?)",
        [*cols.values(), text],
    )


def _build_source(path: Path, *, gather: int | None = None, positions: dict[str, int] | None = None) -> Path:
    db = SRSDatabase(str(path))
    ContentStore(str(path))
    for text in CARDS:
        db.add_collocation(
            SyntacticUnit(text=text, translation=text + "-en", word_count=1, difficulty=1, source="corpus"),
            language_code="no",
        )
    db.set_anki_state_cache("daily_new_cap", "3")
    db.set_anki_state_cache("fsrs_params", "[0.4, 0.6]")
    db.set_anki_state_cache("learning_cutoff", "2026-09-26T10:00:00")
    if gather is not None:
        db.set_anki_state_cache("new_card_gather_priority", str(gather))
    conn = sqlite3.connect(path)
    with conn:
        for i, (text, (state, first_review, due)) in enumerate(CARDS.items()):
            due = (positions or {}).get(text, due)
            _set(conn, text, state=state, anki_due=due, anki_card_id=100 + i, dirty_fsrs=1, anki_card_mod=5)
            if state != "new":
                _set(conn, text, reps=4, lapses=1, stability=30.0, last_review="2026-09-20", introduced_at="2026-09-01")
            if first_review is not None:
                cid = conn.execute("SELECT id FROM collocations WHERE text = ?", (text,)).fetchone()[0]
                for offset in (0, 500):
                    conn.execute(
                        "INSERT INTO tt_revlog (id, collocation_id, direction, button_chosen, interval, last_interval,"
                        " factor, taken_millis, review_kind) VALUES (?, ?, 'recognition', 3, 1, 0, 0, 1000, 1)",
                        (first_review + offset, cid),
                    )
        conn.execute("UPDATE collocations SET anki_note_id = id + 500, dirty_fields = 'text', source_lesson_id = 'L1'")
        conn.execute(
            "INSERT INTO curricula (id, data_json, created_at) VALUES ('c1', '{}', '2026-09-01')",
        )
        conn.execute(
            "INSERT INTO lessons (id, curriculum_id, day, data_json, created_at) VALUES ('L1', 'c1', 1, ?, '2026-09-01')",
            (f'{{"text": "{LESSON_MARKER}"}}',),
        )
        conn.execute(
            "INSERT INTO lesson_listens (lesson_id, listened_at, source) VALUES ('L1', '2026-09-02', 'listen')"
        )
    conn.close()
    return path


@pytest.fixture
def source(tmp_path):
    return _build_source(tmp_path / "src" / "tunatale_no.db")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _positions(dest: Path) -> dict[str, int]:
    conn = sqlite3.connect(dest)
    rows = conn.execute(
        "SELECT c.text, d.anki_due FROM collocations c JOIN collocation_directions d ON d.collocation_id = c.id"
        " WHERE d.direction = 'recognition'"
    ).fetchall()
    conn.close()
    return dict(rows)


class TestTheCopy:
    def test_the_source_is_never_written(self, source, tmp_path):
        before = _sha(source)
        seed_user_deck(source, tmp_path / "users" / "2" / "tunatale_no.db")
        assert _sha(source) == before

    def test_same_cards_by_guid_and_every_direction_unstudied(self, source, tmp_path):
        dest = tmp_path / "out.db"
        report = seed_user_deck(source, dest)
        src, out = sqlite3.connect(source), sqlite3.connect(dest)
        guids = "SELECT guid FROM collocations ORDER BY guid"
        assert out.execute(guids).fetchall() == src.execute(guids).fetchall()
        assert report.cards == len(CARDS) and report.directions == 2 * len(CARDS)
        states = dict(
            out.execute(
                "SELECT c.text, d.state FROM collocations c JOIN collocation_directions d ON d.collocation_id = c.id"
            ).fetchall()
        )
        assert states == {text: "suspended" if text == "fisk" else "new" for text in CARDS}
        assert out.execute(
            "SELECT MAX(reps), MAX(lapses), MAX(stability), COUNT(last_review), COUNT(introduced_at),"
            " COUNT(anki_card_id), COUNT(anki_card_mod), MAX(dirty_fsrs) FROM collocation_directions"
        ).fetchone() == (0, 0, 1.0, 0, 0, 0, 0, 0)
        assert out.execute(
            "SELECT COUNT(anki_note_id), COUNT(source_lesson_id), MAX(dirty_fields) FROM collocations"
        ).fetchone() == (0, 0, "")

    def test_history_lessons_and_sync_state_are_gone(self, source, tmp_path):
        dest = tmp_path / "out.db"
        report = seed_user_deck(source, dest)
        out = sqlite3.connect(dest)
        for table in sorted(CLEAR_TABLES):
            assert out.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table
        assert (report.revlog_dropped, report.lessons_dropped, report.curricula_dropped) == (6, 1, 1)

    def test_deleted_history_does_not_survive_in_free_pages(self, source, tmp_path):
        dest = tmp_path / "out.db"
        seed_user_deck(source, dest)
        assert LESSON_MARKER.encode() in source.read_bytes(), "control: the marker is findable in the source"
        assert LESSON_MARKER.encode() not in dest.read_bytes()

    def test_deck_config_kept_fsrs_weights_and_runtime_state_dropped(self, source, tmp_path):
        dest = tmp_path / "out.db"
        seed_user_deck(source, dest)
        keys = {row[0] for row in sqlite3.connect(dest).execute("SELECT key FROM anki_state_cache")}
        assert "daily_new_cap" in keys
        assert keys <= CONFIG_KEYS
        assert "fsrs_params" not in CONFIG_KEYS and "learning_cutoff" not in CONFIG_KEYS

    def test_suspended_card_unsuspends_as_new(self, source, tmp_path):
        dest = tmp_path / "out.db"
        seed_user_deck(source, dest)
        db = SRSDatabase(str(dest))
        row_id = db.get_collocation_by_lemma_with_id("fisk")[0]
        db.set_suspended(row_id, False)
        assert {d.state.value for d in db.get_collocation_by_id(row_id)[1].directions.values()} == {"new"}


class TestNewCardOrder:
    def test_studied_cards_go_in_front_in_first_review_order(self, source, tmp_path):
        dest = tmp_path / "out.db"
        report = seed_user_deck(source, dest)
        pos = _positions(dest)
        assert sorted(pos, key=pos.get) == ["hund", "katt", "fisk", "sol", "hus", "bil"]
        assert pos["hus"] == 10 and pos["bil"] == 11, "cards still new at the source keep their position"
        assert report.moved_to_front == 4 and not report.gather_descending

    def test_the_real_queue_serves_them_in_that_order_under_the_daily_cap(self, source, tmp_path):
        dest = tmp_path / "out.db"
        seed_user_deck(source, dest)
        queue = assemble_review_queue(SRSDatabase(str(dest)), session_start=True)
        assert [item.syntactic_unit.text for _rid, item, _lang, _dir in queue] == ["hund", "katt", "sol"]

    def test_front_is_the_high_end_when_the_deck_gathers_descending(self, tmp_path):
        source = _build_source(tmp_path / "src.db", gather=2)
        dest = tmp_path / "out.db"
        report = seed_user_deck(source, dest)
        pos = _positions(dest)
        assert sorted(pos, key=pos.get, reverse=True) == ["hund", "katt", "fisk", "sol", "bil", "hus"]
        assert report.gather_descending
        queue = assemble_review_queue(SRSDatabase(str(dest)), session_start=True)
        assert [item.syntactic_unit.text for _rid, item, _lang, _dir in queue] == ["hund", "katt", "sol"]

    def test_kept_cards_shift_up_rather_than_go_negative(self, tmp_path):
        source = _build_source(tmp_path / "src.db", positions={"hus": 0, "bil": 1})
        dest = tmp_path / "out.db"
        seed_user_deck(source, dest)
        pos = _positions(dest)
        assert pos == {"hund": 0, "katt": 1, "fisk": 2, "sol": 3, "hus": 4, "bil": 5}

    @pytest.mark.parametrize(("gather", "expected"), [(None, [0, 1, 2]), (2, [3, 2, 1])])
    def test_a_deck_with_nothing_left_new(self, tmp_path, gather, expected):
        path = tmp_path / "src.db"
        db = SRSDatabase(str(path))
        if gather is not None:
            db.set_anki_state_cache("new_card_gather_priority", str(gather))
        for text in ["a", "b", "c"]:
            db.add_collocation(
                SyntacticUnit(text=text, translation="x", word_count=1, difficulty=1, source="corpus"),
                language_code="no",
            )
        conn = sqlite3.connect(path)
        with conn:
            for i, text in enumerate(["a", "b", "c"]):
                _set(conn, text, state="review", anki_due=50 + i)
        dest = tmp_path / "out.db"
        seed_user_deck(path, dest)
        pos = _positions(dest)
        assert [pos[t] for t in ["a", "b", "c"]] == expected

    def test_a_deck_the_source_never_studied_keeps_every_position(self, tmp_path):
        path = tmp_path / "src.db"
        db = SRSDatabase(str(path))
        db.add_collocation(
            SyntacticUnit(text="hus", translation="x", word_count=1, difficulty=1, source="corpus"), language_code="no"
        )
        conn = sqlite3.connect(path)
        with conn:
            _set(conn, "hus", anki_due=42)
        dest = tmp_path / "out.db"
        assert seed_user_deck(path, dest).moved_to_front == 0
        assert _positions(dest) == {"hus": 42}

    def test_cards_without_a_position_are_counted_not_hidden(self, source, tmp_path):
        conn = sqlite3.connect(source)
        with conn:
            _set(conn, "hus", anki_due=None)
        assert seed_user_deck(source, tmp_path / "out.db").new_without_position == 2


class TestRefusals:
    def test_refuses_to_overwrite(self, source, tmp_path):
        dest = tmp_path / "out.db"
        dest.write_bytes(b"someone's deck")
        with pytest.raises(SeedRefused, match="already exists"):
            seed_user_deck(source, dest)
        assert dest.read_bytes() == b"someone's deck"

    def test_refuses_a_missing_source(self, tmp_path):
        with pytest.raises(SeedRefused, match="no source"):
            seed_user_deck(tmp_path / "nope.db", tmp_path / "out.db")
        assert not (tmp_path / "out.db").exists()

    def test_refuses_an_unclassified_table(self, source, tmp_path):
        conn = sqlite3.connect(source)
        conn.execute("CREATE TABLE learner_diary (entry TEXT)")
        conn.commit()
        with pytest.raises(SeedRefused, match="learner_diary"):
            seed_user_deck(source, tmp_path / "out.db")
        assert not (tmp_path / "out.db").exists()

    @pytest.mark.parametrize("table", ["collocations", "collocation_directions"])
    def test_refuses_an_unclassified_column(self, source, tmp_path, table):
        conn = sqlite3.connect(source)
        conn.execute(f"ALTER TABLE {table} ADD COLUMN learner_secret TEXT")
        conn.commit()
        with pytest.raises(SeedRefused, match="learner_secret"):
            seed_user_deck(source, tmp_path / "out.db")

    def test_every_table_of_the_current_schema_is_classified(self, tmp_path):
        """A new table must be decided in user_deck_seed, not discovered by a refusal on the live deck."""
        path = tmp_path / "fresh.db"
        SRSDatabase(str(path))
        ContentStore(str(path))
        tables = {r[0] for r in sqlite3.connect(path).execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert tables <= KEEP_TABLES | CLEAR_TABLES | TRANSFORM_TABLES


def test_the_seeded_deck_is_what_the_routing_serves(source, tmp_path):
    """The seed's output is exactly the file UserDatabases opens for that learner."""
    dbs = UserDatabases(tmp_path / "users", ["no"])
    seed_user_deck(source, dbs.path_for(2, "no"))
    assert dbs.languages_for(2) == ["no"]
    srs_db, _store = dbs.get(2, "no")
    assert srs_db.count_new_available() == 2 * len(CARDS) - 2
