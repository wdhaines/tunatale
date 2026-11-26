"""Two cards for one word become one (tunatale-h0q3.71, design-short-forms-and-linkers slice 4).

The Cebuano deck holds 'nako' and 'ko' keyed genitive, both 'my; by me'; Wolff 3 B 4
makes the genitive pair interchangeable, so the owner wants one card (11-21, 11-25).
The survivor is the card with the stronger production memory ('ko' #796), renamed
to the main spelling; the other card's note is graved and its TT row deleted.

What these tests are about:
- The loser leaves Anki through ``graves`` (one per card, one for the note), never a
  bare delete, and ``col.usn`` stays the sync anchor.
- The survivor's note keeps its id, cards and scheduling; only its text, its key and
  the guid derived from both move, plus the dictionary fields for the new word.
- The TT survivor matches the note (text, key, guid) without a dirty text flag,
  takes the loser's recording of the long form and the long form's picture, and
  both of those are flagged for the push.
- A merge with nothing to rename (Day into inday) only removes the loser.
- The plan refuses anything it cannot do cleanly before a single write.
"""

from __future__ import annotations

import sqlite3

import pytest

from app.common.guid import compute_guid
from app.languages import get_vocab_notetype
from app.models.syntactic_unit import SyntacticUnit
from app.plugins.anki_sync.merge_pair_cards import Merge, apply_anki, apply_tt, plan_merges
from app.srs.database import SRSDatabase

LANG = "ceb"
VOCAB = get_vocab_notetype(LANG)
COL_USN = 77


def _anki() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE col (id INTEGER PRIMARY KEY, crt INTEGER, mod INTEGER, scm INTEGER,
                          ver INTEGER, dty INTEGER, usn INTEGER, ls INTEGER);
        CREATE TABLE notes (id INTEGER PRIMARY KEY, guid TEXT, mid INTEGER, mod INTEGER, usn INTEGER,
                            tags TEXT, flds TEXT, sfld TEXT, csum INTEGER, flags INTEGER, data TEXT);
        CREATE TABLE cards (id INTEGER PRIMARY KEY, nid INTEGER, did INTEGER, ord INTEGER, mod INTEGER,
                            usn INTEGER, type INTEGER, queue INTEGER, due INTEGER, ivl INTEGER,
                            factor INTEGER, reps INTEGER, lapses INTEGER, left INTEGER, odue INTEGER,
                            odid INTEGER, flags INTEGER, data TEXT);
        CREATE TABLE graves (oid INTEGER NOT NULL, type INTEGER NOT NULL, usn INTEGER NOT NULL,
                             PRIMARY KEY (oid, type));
        CREATE TABLE notetypes (id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER, usn INTEGER, config BLOB);
        CREATE TABLE fields (ntid INTEGER, ord INTEGER, name TEXT, config BLOB, PRIMARY KEY (ntid, ord));
        """
    )
    conn.execute("INSERT INTO col VALUES (1, 0, 100, 5, 18, 0, ?, 0)", (COL_USN,))
    conn.execute("INSERT INTO notetypes VALUES (1, ?, 0, 0, x'')", (VOCAB.name,))
    conn.executemany("INSERT INTO fields VALUES (1, ?, ?, x'')", list(enumerate(VOCAB.field_names)))
    conn.commit()
    return conn


def _note(conn: sqlite3.Connection, nid: int, text: str, english: str, disambig: str = "") -> None:
    flds = dict.fromkeys(VOCAB.field_names, "")
    flds.update({VOCAB.l2_field: text, "English": english, "DisambigKey": disambig, "Spelling": f"old {text}"})
    conn.execute(
        "INSERT INTO notes VALUES (?, ?, 1, 100, 0, '', ?, ?, 0, 0, '')",
        (nid, compute_guid(text, LANG, disambig), "\x1f".join(flds[n] for n in VOCAB.field_names), text),
    )
    for ord_ in (0, 1):
        conn.execute(
            "INSERT INTO cards VALUES (?, ?, 1, ?, 100, 0, 2, 2, 40, 23, 2500, 4, 0, 0, 0, 0, 0, '{\"s\":23.7}')",
            (nid + ord_, nid, ord_),
        )
    conn.commit()


def _tt_add(db: SRSDatabase, text: str, english: str, disambig: str, note_id: int) -> int:
    db.add_collocation(
        SyntacticUnit(
            text=text,
            translation=english,
            word_count=1,
            difficulty=1,
            source="user",
            card_type="vocab",
            disambig_key=disambig,
        ),
        LANG,
    )
    coll_id = db.get_collocation_id_by_guid(compute_guid(text, LANG, disambig))
    with db._get_conn() as conn:
        conn.execute("UPDATE collocations SET anki_note_id = ? WHERE id = ?", (note_id, coll_id))
        conn.commit()
    return coll_id


def _media(db: SRSDatabase, coll_id: int, kind: str, filename: str) -> None:
    db.add_media(coll_id, kind, filename, f"media/{filename}", filename, "0" * 64, 1)


@pytest.fixture
def deck(tmp_path, monkeypatch):
    """The live shape on 2026-11-26: ko[genitive] #796 and nako #928; inday #844 and Day #879."""
    monkeypatch.setattr("app.cards.media.vocab_media._MEDIA_DIR", tmp_path / "media")
    db = SRSDatabase(":memory:")
    anki = _anki()
    _note(anki, 1000, "ko", "my; by me", "genitive")
    _note(anki, 2000, "nako", "my; by me")
    _note(anki, 3000, "inday", "Inday")
    _note(anki, 4000, "Day", "Miss")
    ids = {
        "ko": _tt_add(db, "ko", "my; by me", "genitive", 1000),
        "nako": _tt_add(db, "nako", "my; by me", "", 2000),
        "inday": _tt_add(db, "inday", "Inday", "", 3000),
        "Day": _tt_add(db, "Day", "Miss", "", 4000),
    }
    _media(db, ids["ko"], "audio_forvo", "ceb_ko.mp3")
    _media(db, ids["ko"], "image", "pronoun_i_poss_3cde0e3e.svg")
    _media(db, ids["nako"], "audio_tts", "tts_nako_b1ba00fc.mp3")
    _media(db, ids["Day"], "audio_tts", "tts_Day.mp3")
    return db, anki, ids


def _plan(db: SRSDatabase, ids: dict[str, int]):
    with db._get_conn() as conn:
        return plan_merges(
            conn,
            LANG,
            [Merge(ids["ko"], ids["nako"], text="nako", disambig=""), Merge(ids["inday"], ids["Day"])],
        )


class TestPlan:
    def test_resolves_both_notes_and_the_survivors_new_name(self, deck) -> None:
        db, _anki, ids = deck
        plan = _plan(db, ids)
        assert [(s.survivor_note_id, s.loser_note_id, s.text, s.disambig, s.renamed) for s in plan] == [
            (1000, 2000, "nako", "", True),
            (3000, 4000, "inday", "", False),
        ]

    @pytest.mark.parametrize(
        ("merge", "why"),
        [
            (lambda ids: Merge(ids["ko"], 99999, text="nako", disambig=""), "no card"),
            (lambda ids: Merge(ids["ko"], ids["ko"]), "itself"),
            # The new name may only collide with the card being removed.
            (lambda ids: Merge(ids["ko"], ids["Day"], text="inday", disambig=""), "already"),
        ],
    )
    def test_refuses_what_it_cannot_do_cleanly(self, deck, merge, why) -> None:
        db, _anki, ids = deck
        with db._get_conn() as conn, pytest.raises(ValueError, match=why):
            plan_merges(conn, LANG, [merge(ids)])

    def test_refuses_a_card_with_no_anki_note(self, deck) -> None:
        db, _anki, ids = deck
        with db._get_conn() as conn:
            conn.execute("UPDATE collocations SET anki_note_id = NULL WHERE id = ?", (ids["Day"],))
            conn.commit()
            with pytest.raises(ValueError, match="no Anki note"):
                plan_merges(conn, LANG, [Merge(ids["inday"], ids["Day"])])


class TestApplyAnki:
    def test_graves_each_loser_card_and_note_and_leaves_col_usn(self, deck) -> None:
        db, anki, ids = deck
        apply_anki(anki, _plan(db, ids), LANG)

        graves = sorted(tuple(r) for r in anki.execute("SELECT oid, type, usn FROM graves"))
        assert graves == [(2000, 0, -1), (2000, 1, -1), (2001, 0, -1), (4000, 0, -1), (4000, 1, -1), (4001, 0, -1)]
        assert anki.execute("SELECT COUNT(*) FROM notes WHERE id IN (2000, 4000)").fetchone()[0] == 0
        assert anki.execute("SELECT COUNT(*) FROM cards WHERE nid IN (2000, 4000)").fetchone()[0] == 0
        col = anki.execute("SELECT usn, mod, scm FROM col").fetchone()
        assert (col["usn"], col["scm"]) == (COL_USN, 5)
        assert col["mod"] > 100

    def test_renames_the_survivor_note_and_moves_its_guid(self, deck) -> None:
        db, anki, ids = deck
        apply_anki(anki, _plan(db, ids), LANG)

        row = anki.execute("SELECT flds, sfld, guid, usn FROM notes WHERE id = 1000").fetchone()
        flds = dict(zip(VOCAB.field_names, row["flds"].split("\x1f"), strict=True))
        assert (flds[VOCAB.l2_field], flds["DisambigKey"], flds["English"]) == ("nako", "", "my; by me")
        assert (row["sfld"], row["guid"], row["usn"]) == ("nako", compute_guid("nako", LANG, ""), -1)
        # The dictionary fields now describe nákò, and the back names the short form.
        assert flds["Spelling"] == "nákò"
        assert flds["Short form"] == "short form: ko"

    def test_keeps_the_survivors_cards_and_scheduling(self, deck) -> None:
        db, anki, ids = deck
        query = "SELECT id, ord, due, ivl, reps, data FROM cards WHERE nid IN (1000, 3000) ORDER BY id"
        before = [tuple(r) for r in anki.execute(query)]
        apply_anki(anki, _plan(db, ids), LANG)
        assert [tuple(r) for r in anki.execute(query)] == before

    def test_a_merge_with_no_rename_leaves_the_survivor_note_alone(self, deck) -> None:
        db, anki, ids = deck
        before = tuple(anki.execute("SELECT flds, guid, usn, mod FROM notes WHERE id = 3000").fetchone())
        apply_anki(anki, _plan(db, ids), LANG)
        assert tuple(anki.execute("SELECT flds, guid, usn, mod FROM notes WHERE id = 3000").fetchone()) == before


class TestApplyTT:
    def test_the_survivor_row_matches_its_renamed_note(self, deck) -> None:
        db, _anki, ids = deck
        apply_tt(db, _plan(db, ids), LANG)

        with db._get_conn() as conn:
            row = conn.execute(
                "SELECT text, disambig_key, guid, lemma, translation, anki_note_id FROM collocations WHERE id = ?",
                (ids["ko"],),
            ).fetchone()
        assert tuple(row) == ("nako", "", compute_guid("nako", LANG, ""), "nako", "my; by me", 1000)

    def test_the_survivor_takes_the_long_forms_recording_and_picture_and_flags_both(self, deck) -> None:
        db, _anki, ids = deck
        apply_tt(db, _plan(db, ids), LANG)

        assert db.get_audio_filename(ids["ko"]) == "tts_nako_b1ba00fc.mp3"
        assert db.get_image_filename(ids["ko"]) == "pronoun_i_poss_7862b380.svg"
        # The note already holds the new text: only the media wait for the push.
        assert set(db.get_dirty_fields(compute_guid("nako", LANG, "")).split(",")) == {"audio", "image"}

    def test_the_losers_are_gone_and_an_unrenamed_survivor_is_untouched(self, deck) -> None:
        db, _anki, ids = deck
        apply_tt(db, _plan(db, ids), LANG)

        with db._get_conn() as conn:
            left = {r[0] for r in conn.execute("SELECT id FROM collocations")}
            assert left == {ids["ko"], ids["inday"]}
            assert (
                conn.execute(
                    "SELECT COUNT(*) FROM collocation_directions WHERE collocation_id IN (?, ?)",
                    (ids["nako"], ids["Day"]),
                ).fetchone()[0]
                == 0
            )
        assert db.get_dirty_fields(compute_guid("inday", LANG, "")) == ""
        assert db.get_audio_filename(ids["inday"]) is None


def test_a_survivor_note_missing_from_the_collection_stops_the_batch_unwritten(deck) -> None:
    db, anki, ids = deck
    plan = _plan(db, ids)
    anki.execute("DELETE FROM notes WHERE id = 1000")
    anki.commit()
    with pytest.raises(ValueError, match="not in this collection"):
        apply_anki(anki, plan, LANG)
    assert anki.execute("SELECT COUNT(*) FROM graves").fetchone()[0] == 0
