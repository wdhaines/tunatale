"""Re-mint h0q3.42's separate sentence Cloze notes as one "TT Sentence" note per line (tunatale-h0q3.55 phase 3).

The 52 Wolff 1 sentence rows predate sibling groups: each sits on its own c1
Cloze note, with ``sibling_group`` NULL. The re-mint graves those notes and hands
the rows back to ``sync_create_new`` as one unlinked line, which mints the line's
TT Sentence note. Review history resets (owner accepted).

The order is the subject. ``detect_and_reset_orphans`` honours a NOTE grave by
DELETING every TT row still linked to that note, so the TT rows must be unlinked
before any sync reads the graves; and ``get_unpushed_revlog_rows`` pushes every
``tt_revlog`` row above the new card's (empty) Anki revlog, so the old history
must leave TT or the first grade replays it onto the new card.

Real ``OfflineWriter``/``OfflineReader`` on a collection file carrying both the
built-in Cloze notetype and the TT Sentence notetype; the script runs through
``safe_open`` on that file exactly as it will on ``tt_collection``.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.cards.sentence_notetype import NAME
from app.models.srs_item import Direction
from app.plugins.anki_sync import remint_sentence_notes as remint
from app.plugins.anki_sync.add_sentence_notetype import add_sentence_notetype
from app.plugins.anki_sync.sync import AnkiSync, OfflineReader, OfflineWriter
from app.srs.database import SRSDatabase
from app.srs.sentence_card import Cue, Gloss, SentenceCard, blank_span
from tests._helpers.anki_sync_create_new import _make_dual_collection_conn

LANG = "ceb"
DECK = "0. Slovene"
_BASE = SentenceCard(
    lesson_id="wolff-01b",
    line_index=4,
    speaker="Ínting",
    text="Nâa ba si Místir Abáya?",
    spoken="Nâa ba si Místir Abáya?",
    english="Is Mr. Abaya there?",
    glosses=(Gloss("ba", "question particle"),),
    cue=Cue(speaker="Inday", text="Únsa may átò?", spoken="Únsa may átò?", english="What can I do for you?"),
)
#: TT row order on the live deck: blanks first, the whole line last.
LINE4 = [
    _BASE.with_blank(blank_span(_BASE.text, "nâa"), "there (near you)"),
    _BASE.with_blank(blank_span(_BASE.text, "ba"), "yes/no question particle"),
    _BASE,
]
LINE5 = [
    SentenceCard(
        lesson_id="wolff-01b",
        line_index=5,
        speaker="Inday",
        text="Nîa man.",
        spoken="Nîa man.",
        english="Yes, he is.",
        glosses=(),
        cue=Cue(speaker="Ínting", text=_BASE.text, spoken=_BASE.spoken, english=_BASE.english),
    )
]
KEY4, KEY5 = _BASE.line_key, LINE5[0].line_key
#: A grade ten days back: history, not "graded today".
OLD_MS = int((datetime.now(UTC) - timedelta(days=10)).timestamp() * 1000)


@pytest.fixture
def no_tts(monkeypatch):
    """The TTS boundary: neither the re-mint nor the mint may synthesize."""
    import app.audio.cloze_tts as cloze_tts_mod

    async def _boom(text, voice=None):
        raise AssertionError(f"re-mint synthesized {text!r}")

    monkeypatch.setattr(cloze_tts_mod, "generate_tts_audio", _boom)


class World:
    """A TT database and a collection FILE holding h0q3.42's legacy shape."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path
        self.db_url = f"sqlite:///{tmp_path / 'tt.db'}"
        self.db = SRSDatabase(self.db_url)
        self.collection = tmp_path / "collection.anki2"
        mem = _make_dual_collection_conn()
        mem.execute(
            "CREATE TABLE graves (oid INTEGER NOT NULL, type INTEGER NOT NULL, usn INTEGER NOT NULL, "
            "PRIMARY KEY (oid, type))"
        )
        assert add_sentence_notetype(mem) == "created"
        mem.execute("UPDATE col SET usn = 2754, mod = 1")
        mem.commit()
        with sqlite3.connect(self.collection) as disk:
            mem.backup(disk)
        mem.close()
        self.capture = tmp_path / "capture.json"
        self.backups = tmp_path / "backups"

    def anki(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.collection)
        conn.row_factory = sqlite3.Row
        return conn

    def legacy_line(self, cards: list[SentenceCard], *, reps: int = 3) -> list[int]:
        """Rows on separate c1 Cloze notes, reviewed, with history: the live Wolff 1 shape."""
        ids = []
        conn = self.anki()
        writer = OfflineWriter(conn)
        for card in cards:
            unit = card.unit()
            assert self.db.add_collocation(unit, LANG)
            with self.db._get_conn() as tt:
                row = tt.execute("SELECT id, guid FROM collocations WHERE disambig_key = ?", (card.key,)).fetchone()
                tt.execute("UPDATE collocations SET sibling_group = NULL WHERE id = ?", (row["id"],))
                tt.commit()
            note_id = writer.create_cloze_note(
                DECK, card.cloze(), "", ["tunatale", "cloze", "sentence"], language_code=LANG, course=True
            )
            [card_id] = writer.get_cards_for_note(note_id).values()
            self.db.set_anki_ids(row["guid"], note_id, {Direction.PRODUCTION: card_id})
            conn.execute(
                "UPDATE cards SET type = 2, queue = 2, ivl = 5, reps = ?, usn = 2754 WHERE id = ?", (reps, card_id)
            )
            conn.execute("UPDATE notes SET usn = 2754 WHERE id = ?", (note_id,))
            with self.db._get_conn() as tt:
                tt.execute(
                    "UPDATE collocation_directions SET state = 'review', reps = ?, stability = 7.5, "
                    "fsrs_difficulty = 4.2, last_review = '2026-10-30T10:00:00+00:00', introduced_at = "
                    "'2026-10-20T10:00:00+00:00', prior_state = 'new', last_rating = 3, anki_due = 900, "
                    "anki_card_mod = 1790000000, last_synced_at = '2026-11-08T10:00:00+00:00' "
                    "WHERE collocation_id = ?",
                    (reps, row["id"]),
                )
                for n in range(reps):
                    tt.execute(
                        "INSERT INTO tt_revlog (id, collocation_id, direction, button_chosen, interval, "
                        "last_interval, factor, taken_millis, review_kind, anki_card_id) "
                        "VALUES (?, ?, 'production', 3, 5, 1, 2500, 4000, 1, ?)",
                        (OLD_MS + row["id"] * 1000 + n, row["id"], card_id),
                    )
                tt.commit()
            ids.append(row["id"])
        conn.commit()
        conn.close()
        return ids

    def run(self, *lines: str, apply: bool = True, lessons: tuple[str, ...] = ()) -> int:
        return remint.run(
            self.db,
            collection_path=self.collection,
            backup_dir=self.backups,
            capture_path=self.capture,
            lines=list(lines),
            lessons=list(lessons),
            apply=apply,
        )

    def row(self, row_id: int) -> sqlite3.Row:
        with self.db._get_conn() as tt:
            return tt.execute("SELECT * FROM collocations WHERE id = ?", (row_id,)).fetchone()

    def direction(self, row_id: int) -> dict:
        with self.db._get_conn() as tt:
            r = tt.execute(
                "SELECT * FROM collocation_directions WHERE collocation_id = ? AND direction = 'production'", (row_id,)
            ).fetchone()
            return dict(r)

    def revlog_count(self, row_ids: list[int]) -> int:
        with self.db._get_conn() as tt:
            ph = ",".join("?" * len(row_ids))
            return tt.execute(f"SELECT COUNT(*) FROM tt_revlog WHERE collocation_id IN ({ph})", row_ids).fetchone()[0]

    def snapshot(self) -> tuple:
        """Everything the script may write, for 'nothing changed' assertions."""
        conn = self.anki()
        anki = (
            [tuple(r) for r in conn.execute("SELECT * FROM notes ORDER BY id")],
            [tuple(r) for r in conn.execute("SELECT * FROM cards ORDER BY id")],
            [tuple(r) for r in conn.execute("SELECT * FROM graves ORDER BY oid")],
            tuple(conn.execute("SELECT mod, usn FROM col").fetchone()),
        )
        conn.close()
        with self.db._get_conn() as tt:
            t = (
                [tuple(r) for r in tt.execute("SELECT * FROM collocations ORDER BY id")],
                [tuple(r) for r in tt.execute("SELECT * FROM collocation_directions ORDER BY collocation_id")],
                [tuple(r) for r in tt.execute("SELECT * FROM tt_revlog ORDER BY id")],
            )
        return anki, t


@pytest.fixture
def world(tmp_path) -> World:
    return World(tmp_path)


def _old_ids(world: World, row_ids: list[int]) -> tuple[list[int], list[int]]:
    with world.db._get_conn() as tt:
        rows = [
            tt.execute(
                "SELECT c.anki_note_id, d.anki_card_id FROM collocations c JOIN collocation_directions d "
                "ON d.collocation_id = c.id WHERE c.id = ?",
                (i,),
            ).fetchone()
            for i in row_ids
        ]
    return [r[0] for r in rows], [r[1] for r in rows]


class TestApply:
    def test_graves_each_old_note_and_its_card(self, world):
        ids = world.legacy_line(LINE4)
        notes, cards = _old_ids(world, ids)

        assert world.run(KEY4) == 0

        conn = world.anki()
        assert conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0] == 0
        graves = {(r["oid"], r["type"]): r["usn"] for r in conn.execute("SELECT * FROM graves")}
        assert graves == {**{(n, 1): -1 for n in notes}, **{(c, 0): -1 for c in cards}}
        mod, usn = conn.execute("SELECT mod, usn FROM col").fetchone()
        assert usn == 2754, "col.usn is the sync anchor: never touched (Layer 61)"
        assert mod > 1

    def test_unlinks_and_groups_the_rows(self, world):
        ids = world.legacy_line(LINE4)

        world.run(KEY4)

        for i in ids:
            row = world.row(i)
            assert row["anki_note_id"] is None and row["last_synced_at"] is None
            assert row["sibling_group"] == KEY4

    def test_each_direction_becomes_the_row_a_fresh_card_gets(self, world):
        """The oracle is the insert path itself: the reset equals a never-reviewed row, column by column."""
        ids = world.legacy_line(LINE4)
        fresh = SentenceCard(**{**LINE5[0].__dict__, "line_index": 99}).unit()
        assert world.db.add_collocation(fresh, LANG)
        with world.db._get_conn() as tt:
            fresh_id = tt.execute(
                "SELECT id FROM collocations WHERE disambig_key = ?", (fresh.disambig_key,)
            ).fetchone()[0]

        world.run(KEY4)

        expected = {k: v for k, v in world.direction(fresh_id).items() if k != "collocation_id"}
        for i in ids:
            got = {k: v for k, v in world.direction(i).items() if k != "collocation_id"}
            assert got == expected

    def test_the_old_history_leaves_tt(self, world):
        """Kept, the first grade on the new card would push every old row onto it (``get_unpushed_revlog_rows``)."""
        ids = world.legacy_line(LINE4)

        world.run(KEY4)

        assert world.revlog_count(ids) == 0
        for i in ids:
            assert world.db.get_unpushed_revlog_rows(i, Direction.PRODUCTION, 0) == []

    def test_the_capture_holds_what_was_removed(self, world):
        ids = world.legacy_line(LINE4)
        notes, cards = _old_ids(world, ids)
        before = {i: world.direction(i) for i in ids}

        world.run(KEY4)

        captured = json.loads(world.capture.read_text())["lines"][KEY4]
        assert [r["row_id"] for r in captured["rows"]] == ids
        assert [r["note_id"] for r in captured["rows"]] == notes
        assert [r["card_id"] for r in captured["rows"]] == cards
        for r in captured["rows"]:
            assert r["direction"] == before[r["row_id"]]
            assert len(r["revlog"]) == 3 and all(x["collocation_id"] == r["row_id"] for x in r["revlog"])

    def test_a_second_line_adds_to_the_capture(self, world):
        world.legacy_line(LINE4)
        world.legacy_line(LINE5)

        world.run(KEY4)
        world.run(KEY5)

        assert set(json.loads(world.capture.read_text())["lines"]) == {KEY4, KEY5}

    def test_other_lines_are_untouched(self, world):
        world.legacy_line(LINE4)
        [other] = world.legacy_line(LINE5)
        before_row, before_dir = dict(world.row(other)), world.direction(other)

        world.run(KEY4)

        assert dict(world.row(other)) == before_row and world.direction(other) == before_dir
        assert world.revlog_count([other]) == 3

    def test_a_lesson_selects_every_legacy_line_and_no_other(self, world):
        ids = world.legacy_line(LINE4) + world.legacy_line(LINE5)
        # A line minted in the new shape (grouped at insert, never captured) is not the script's.
        fresh = SentenceCard(**{**LINE5[0].__dict__, "line_index": 6}).unit()
        assert world.db.add_collocation(fresh, LANG)

        assert world.run(lessons=("wolff-01b",)) == 0

        assert {world.row(i)["sibling_group"] for i in ids} == {KEY4, KEY5}
        assert set(json.loads(world.capture.read_text())["lines"]) == {KEY4, KEY5}

    def test_a_done_line_is_never_reset_again(self, world):
        """apply_tt and write_capture act on TODO lines only: a done line's rows may carry the NEW note's links."""
        world.legacy_line(LINE4)
        world.run(KEY4)
        captured = world.capture.read_text()
        conn = world.anki()
        with world.db._get_conn() as tt:
            plans = remint.plan(tt, conn, [KEY4], remint.load_capture(world.capture))
            assert [p.status for p in plans] == [remint.TT_DONE]
            remint.write_capture(world.capture, tt, plans)
            assert remint.apply_tt(tt, plans) == 0
        conn.close()
        assert world.capture.read_text() == captured

    def test_the_frozen_queue_is_dropped(self, world):
        """Reset rows must not be served from a queue frozen while they were reviews."""
        from app.srs.anki_mirror.queue_stats import _SESSION_MAIN_QUEUE_KEY

        world.legacy_line(LINE4)
        world.db.set_anki_state_cache(_SESSION_MAIN_QUEUE_KEY, '{"day": "x", "items": []}')

        world.run(KEY4)

        assert world.db.get_anki_state_cache(_SESSION_MAIN_QUEUE_KEY) is None

    def test_a_backup_is_taken(self, world):
        world.legacy_line(LINE4)

        world.run(KEY4)

        assert any(world.backups.iterdir())


class TestAtomicity:
    def test_a_tt_failure_leaves_the_collection_untouched(self, world):
        """The collection commits LAST: a TT write that fails must not leave graves behind it.

        Graves without the unlink are the control in ``TestThenSync``: the next
        sync deletes the rows.
        """
        world.legacy_line(LINE4)
        with world.db._get_conn() as tt:
            tt.execute(
                "CREATE TRIGGER boom BEFORE UPDATE OF sibling_group ON collocations "
                "BEGIN SELECT RAISE(ABORT, 'boom'); END"
            )
            tt.commit()
        before = world.snapshot()

        with pytest.raises(sqlite3.IntegrityError, match="boom"):
            world.run(KEY4)

        assert world.snapshot() == before


class TestThenSync:
    """The next sync, in its own order: orphans first, then create."""

    async def _sync(self, world: World) -> None:
        conn = world.anki()
        sync = AnkiSync(
            db=world.db,
            _reader=OfflineReader(conn, DECK, language_code=LANG),
            _writer=OfflineWriter(conn),
            language_code=LANG,
        )
        sync.detect_and_reset_orphans()
        await sync.sync_create_new(deck_name=DECK, model_name="Slovene Vocabulary")
        conn.commit()
        conn.close()

    async def test_the_graves_delete_no_row_and_one_note_is_minted(self, world, no_tts):
        ids = world.legacy_line(LINE4)
        world.run(KEY4)

        await self._sync(world)

        conn = world.anki()
        [note] = conn.execute(
            "SELECT n.id FROM notes n JOIN notetypes m ON m.id = n.mid WHERE m.name = ?", (NAME,)
        ).fetchall()
        card_ids = {r[0] for r in conn.execute("SELECT id FROM cards WHERE nid = ?", (note[0],))}
        conn.close()
        assert len(card_ids) == 3
        for i in ids:
            row = world.row(i)
            assert row is not None, "a note grave deleted a row the re-mint meant to keep"
            assert row["anki_note_id"] == note[0]
            assert world.direction(i)["anki_card_id"] in card_ids

    async def test_control_graving_without_unlinking_loses_the_rows(self, world, no_tts):
        """Why the TT half is not optional: graves alone and the next sync deletes the rows."""
        ids = world.legacy_line(LINE4)
        notes, _ = _old_ids(world, ids)
        conn = world.anki()
        for n in notes:
            remint.grave_note(conn, n)
        conn.commit()
        conn.close()

        await self._sync(world)

        assert all(world.row(i) is None for i in ids)


class TestRefusals:
    """Any refusal writes nothing, on either side, for any selected line."""

    def _refused(self, world: World, *lines: str, capsys=None, why: str = "") -> None:
        before = world.snapshot()
        assert world.run(*lines) == 2
        assert world.snapshot() == before
        assert not world.capture.exists()
        if capsys is not None:
            assert why in capsys.readouterr().out

    def test_an_unknown_line(self, world, capsys):
        world.legacy_line(LINE4)
        self._refused(world, "sent:wolff-01b:77", capsys=capsys, why="no rows")

    def test_a_line_graded_today(self, world, capsys):
        ids = world.legacy_line(LINE4)
        now_ms = int(datetime.now(UTC).timestamp() * 1000)
        with world.db._get_conn() as tt:
            tt.execute(
                "INSERT INTO tt_revlog (id, collocation_id, direction, button_chosen, interval, last_interval, "
                "factor, taken_millis, review_kind, anki_card_id) VALUES (?, ?, 'production', 3, 5, 1, 2500, 1, 1, 1)",
                (now_ms, ids[0]),
            )
            tt.commit()
        self._refused(world, KEY4, capsys=capsys, why="graded today")

    def test_an_unpushed_tt_grade(self, world, capsys):
        ids = world.legacy_line(LINE4)
        with world.db._get_conn() as tt:
            tt.execute("UPDATE collocation_directions SET dirty_fsrs = 1 WHERE collocation_id = ?", (ids[1],))
            tt.commit()
        self._refused(world, KEY4, capsys=capsys, why="unpushed")

    def test_an_unpushed_tt_field_edit(self, world, capsys):
        ids = world.legacy_line(LINE4)
        with world.db._get_conn() as tt:
            tt.execute("UPDATE collocations SET dirty_fields = 'Text' WHERE id = ?", (ids[0],))
            tt.commit()
        self._refused(world, KEY4, capsys=capsys, why="unpushed")

    def test_an_unpushed_anki_change(self, world, capsys):
        ids = world.legacy_line(LINE4)
        _, cards = _old_ids(world, ids)
        conn = world.anki()
        conn.execute("UPDATE cards SET usn = -1 WHERE id = ?", (cards[2],))
        conn.commit()
        conn.close()
        self._refused(world, KEY4, capsys=capsys, why="unpushed")

    def test_a_staged_listen_grade(self, world, capsys):
        ids = world.legacy_line(LINE4)
        with world.db._get_conn() as tt:
            tt.execute(
                "INSERT INTO pending_listen_grades (lesson_id, collocation_id, direction, rating, created_at) "
                "VALUES ('wolff-01b', ?, 'production', 'good', '2026-11-09')",
                (ids[0],),
            )
            tt.commit()
        self._refused(world, KEY4, capsys=capsys, why="staged")

    def test_a_note_that_is_not_a_cloze_note(self, world, capsys):
        ids = world.legacy_line(LINE4)
        notes, _ = _old_ids(world, ids)
        conn = world.anki()
        vocab_mid = conn.execute("SELECT id FROM notetypes WHERE name != 'Cloze' AND name != ?", (NAME,)).fetchone()[0]
        conn.execute("UPDATE notes SET mid = ? WHERE id = ?", (vocab_mid, notes[0]))
        conn.commit()
        conn.close()
        self._refused(world, KEY4, capsys=capsys, why="not a Cloze note")

    def test_a_note_missing_from_the_collection(self, world, capsys):
        ids = world.legacy_line(LINE4)
        notes, cards = _old_ids(world, ids)
        conn = world.anki()
        conn.execute("DELETE FROM cards WHERE id = ?", (cards[0],))
        conn.execute("DELETE FROM notes WHERE id = ?", (notes[0],))
        conn.commit()
        conn.close()
        self._refused(world, KEY4, capsys=capsys, why="not in the collection")

    def test_a_card_the_row_does_not_name(self, world, capsys):
        ids = world.legacy_line(LINE4)
        _, cards = _old_ids(world, ids)
        with world.db._get_conn() as tt:
            tt.execute(
                "UPDATE collocation_directions SET anki_card_id = ? WHERE collocation_id = ?", (cards[1], ids[0])
            )
            tt.commit()
        self._refused(world, KEY4, capsys=capsys, why="cards")

    def test_a_recognition_direction(self, world, capsys):
        ids = world.legacy_line(LINE4)
        with world.db._get_conn() as tt:
            tt.execute(
                "INSERT INTO collocation_directions (collocation_id, direction, due_at, state) "
                "VALUES (?, 'recognition', '2026-11-09T09:00:00+00:00', 'new')",
                (ids[0],),
            )
            tt.commit()
        self._refused(world, KEY4, capsys=capsys, why="expected production only")

    def test_a_legacy_row_with_no_note(self, world, capsys):
        ids = world.legacy_line(LINE4)
        with world.db._get_conn() as tt:
            tt.execute("UPDATE collocations SET anki_note_id = NULL WHERE id = ?", (ids[2],))
            tt.commit()
        self._refused(world, KEY4, capsys=capsys, why="not linked")

    def test_two_rows_on_one_note(self, world, capsys):
        ids = world.legacy_line(LINE4)
        notes, _ = _old_ids(world, ids)
        with world.db._get_conn() as tt:
            tt.execute("UPDATE collocations SET anki_note_id = ? WHERE id = ?", (notes[0], ids[1]))
            tt.commit()
        self._refused(world, KEY4, capsys=capsys, why="share")

    def test_a_half_migrated_line(self, world, capsys):
        ids = world.legacy_line(LINE4)
        with world.db._get_conn() as tt:
            tt.execute("UPDATE collocations SET sibling_group = ? WHERE id = ?", (KEY4, ids[0]))
            tt.commit()
        self._refused(world, KEY4, capsys=capsys, why="mixed")

    def test_one_bad_line_stops_every_line(self, world):
        world.legacy_line(LINE4)
        [other] = world.legacy_line(LINE5)
        with world.db._get_conn() as tt:
            tt.execute("UPDATE collocation_directions SET dirty_fsrs = 1 WHERE collocation_id = ?", (other,))
            tt.commit()
        self._refused(world, KEY4, KEY5)

    def test_nothing_selected(self, world, capsys):
        self._refused(world, capsys=capsys, why="nothing selected")


class TestDryRunAndRerun:
    def test_a_dry_run_writes_nothing_and_says_what_it_would_do(self, world, capsys):
        world.legacy_line(LINE4)
        before = world.snapshot()

        assert world.run(KEY4, apply=False) == 0

        assert world.snapshot() == before and not world.capture.exists()
        out = capsys.readouterr().out
        assert KEY4 in out and "DRY RUN" in out and "3 row(s)" in out

    def test_a_done_line_is_left_alone(self, world, no_tts):
        world.legacy_line(LINE4)
        world.run(KEY4)
        before = world.snapshot()

        assert world.run(KEY4) == 0

        assert world.snapshot() == before

    async def test_a_done_and_minted_line_is_left_alone(self, world, no_tts):
        world.legacy_line(LINE4)
        world.run(KEY4)
        await TestThenSync()._sync(world)
        before = world.snapshot()

        assert world.run(KEY4) == 0

        assert world.snapshot() == before

    def test_a_run_that_stopped_after_tt_graves_the_old_notes_on_rerun(self, world):
        """TT committed, the collection did not: the capture names the notes still to grave."""
        ids = world.legacy_line(LINE4)
        notes, cards = _old_ids(world, ids)
        conn = world.anki()
        with world.db._get_conn() as tt:
            plans = remint.plan(tt, conn, [KEY4])
            remint.write_capture(world.capture, tt, plans)
            remint.apply_tt(tt, plans)
            tt.commit()
        conn.close()

        assert world.run(KEY4) == 0

        conn = world.anki()
        assert conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 0
        assert {r[0] for r in conn.execute("SELECT oid FROM graves WHERE type = 1")} == set(notes)
        conn.close()

    def test_a_done_line_with_no_capture_is_refused(self, world, capsys):
        """Unlinked rows and no record of their old notes: the script cannot know what to grave."""
        world.legacy_line(LINE4)
        conn = world.anki()
        with world.db._get_conn() as tt:
            remint.apply_tt(tt, remint.plan(tt, conn, [KEY4]))
            tt.commit()
        conn.close()
        before = world.snapshot()

        assert world.run(KEY4) == 2

        assert world.snapshot() == before
        assert "no capture" in capsys.readouterr().out


def test_main_parses_its_flags_into_a_dry_run(world, capsys):
    """The CLI: explicit paths (the live run names tt_collection), --line, and no --apply = dry run."""
    world.legacy_line(LINE4)
    before = world.snapshot()

    code = remint.main(
        [
            "--db-url",
            world.db_url,
            "--line",
            KEY4,
            "--collection",
            str(world.collection),
            "--backup-dir",
            str(world.backups),
            "--capture-file",
            str(world.capture),
        ]
    )

    assert code == 0 and "DRY RUN" in capsys.readouterr().out
    assert world.snapshot() == before


class TestDrillRows:
    """A drill row (tunatale-5i1p.4) is a ``sent:`` row that is not a lesson line (audit I2).

    ``_line_rows`` derives every ``sent:`` row's line from its ``card_data`` as a
    SentenceCard; a drill row's is not one, so the script skips it rather than
    failing on it, and never writes it.
    """

    @staticmethod
    def _drill_row(world: World) -> int:
        from app.srs.lesson_card_mint import mint_lesson_cards
        from tests.test_drill_card import DRILLS
        from tests.test_lesson_drill_cards import _cards

        minted = mint_lesson_cards(world.db, LANG, _cards(), drills=DRILLS)
        return minted[0][0]

    def test_a_dry_run_beside_a_drill_row_plans_the_line_and_leaves_the_drill(self, world, capsys):
        world.legacy_line(LINE4)
        self._drill_row(world)
        before = world.snapshot()

        assert world.run(KEY4, apply=False) == 0

        assert world.snapshot() == before
        assert "3 row(s)" in capsys.readouterr().out

    def test_a_lesson_run_over_the_drills_set_finds_no_line(self, world, capsys):
        drill_id = self._drill_row(world)
        before = world.row(drill_id)

        assert world.run(lessons=("test-01",)) == 2  # nothing selected: a drill is no line

        assert tuple(world.row(drill_id)) == tuple(before)
        assert "nothing selected" in capsys.readouterr().out

    def test_an_applied_run_beside_a_drill_row_leaves_it_alone(self, world, no_tts):
        world.legacy_line(LINE4)
        drill_id = self._drill_row(world)
        before = tuple(world.row(drill_id))

        assert world.run(KEY4) == 0

        assert tuple(world.row(drill_id)) == before
