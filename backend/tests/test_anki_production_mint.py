"""TT's half of the just-in-time production mint (tunatale-qf6.2, piece B).

``add_production_template`` gives an imported recognition-only notetype the
*capability* to carry production cards. This is the other half: the two
primitives that turn that capability into one real card for one word —

- ``OfflineWriter.mint_production_card`` — write the image into the note and
  land its production card, in a single transaction;
- ``SRSDatabase.add_production_direction`` — record that card as a production
  direction on the collocation the word **already has**.

The three rules these tests exist to hold come from measuring Anki's card
generator against the real binary (``tests/test_parity_cardgen.py``, and
``.beads-tasks/briefs/design-no-production-cards-2026-08.md`` § "Settled
2026-08-17"). Restated as the failure each one prevents:

1. **One transaction.** A note left with a non-empty ``Image`` and no ord=1
   card is the window Anki fills on the user's next Check Database — with a
   card id TT never recorded, stranding the production direction.
2. **Adopt, don't insert.** If such a card already exists (Anki generated it,
   or a previous mint did), take its id. That is what makes the phase
   idempotent *and* self-heals an already-stranded note.
3. **Never mint against an empty image.** Anki classifies an ord=1 card whose
   ``Image`` is blank as an *empty card* and offers to delete it; that word
   belongs on the cloze branch instead.

The collection fixture is put through the real ``add_production_template``
rather than hand-written in its post-migration shape, so a drift between what
the migration creates and what the mint expects goes red here.

Not covered here: which words get promoted, at what rate, and from where —
that is the ``run_full_sync`` phase (piece C).
"""

from __future__ import annotations

import sqlite3

import pytest

from app.cards.field_map import get_profile
from app.models.srs_item import Direction, DirectionState, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.plugins.anki_sync.add_production_template import (
    IMAGE_FIELD,
    PRODUCTION_TEMPLATE,
    add_production_template,
)
from app.plugins.anki_sync.sync import OfflineWriter
from app.srs.anki_mirror.rollover import anki_today, due_at_rollover_utc

SEED_MID = 1694414741634
DECK_ID = 17
OTHER_DECK_ID = 18

#: Named exactly as the real imported notetype so ``get_profile`` resolves the
#: real profile and the migration renders the real template.
SEED_NOTETYPE = "6000 Most Frequent Norwegian Words"

SEED_FIELDS = (
    "Frequency index",
    "Norwegian word",
    "Word class",
    "Article",
    "IPA",
    "English translation",
    "General meaning",
)

_SCHEMA = """
    CREATE TABLE col (id INTEGER, crt INTEGER, mod INTEGER, scm INTEGER, ver INTEGER, dty INTEGER,
        usn INTEGER, ls INTEGER, conf TEXT, models TEXT, decks TEXT, dconf TEXT, tags TEXT);
    CREATE TABLE notes (id INTEGER PRIMARY KEY, guid TEXT, mid INTEGER, mod INTEGER, usn INTEGER,
        tags TEXT, flds TEXT, sfld TEXT, csum INTEGER, flags INTEGER, data TEXT);
    CREATE TABLE cards (id INTEGER PRIMARY KEY, nid INTEGER, did INTEGER, ord INTEGER, mod INTEGER,
        usn INTEGER, type INTEGER, queue INTEGER, due INTEGER, ivl INTEGER, factor INTEGER, reps INTEGER,
        lapses INTEGER, left INTEGER, odue INTEGER, odid INTEGER, flags INTEGER, data TEXT);
    CREATE TABLE notetypes (id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER, usn INTEGER, config BLOB);
    CREATE TABLE fields (ntid INTEGER, ord INTEGER, name TEXT, config BLOB, PRIMARY KEY (ntid, ord));
    CREATE TABLE templates (ntid INTEGER, ord INTEGER, name TEXT, mtime_secs INTEGER, usn INTEGER, config BLOB,
        PRIMARY KEY (ntid, ord));
    CREATE TABLE decks (id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER, usn INTEGER, common BLOB);
"""

#: The note every test mints against: a graduated recognition card, no image.
NOTE_ID = 1000
CARD_ID = 2000
#: A second seed note, so the "new card goes to the tail of the new queue"
#: assertion has a MAX(due) to beat.
OTHER_NOTE_ID = 1001
OTHER_CARD_ID = 2001
OTHER_DUE = 940

IMAGE_TAG = '<img src="hus.jpg">'


def _make_conn(*, migrate: bool = True) -> sqlite3.Connection:
    """A collection holding one imported note, optionally post-migration."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.execute("INSERT INTO col VALUES (1, 0, 100, 1000, 18, 0, 5, 0, '{}', '{}', '{}', '{}', '{}')")
    conn.execute("INSERT INTO decks VALUES (?, 'Imported Deck', 50, 0, NULL)", (DECK_ID,))
    conn.execute("INSERT INTO decks VALUES (?, 'Other Deck', 50, 0, NULL)", (OTHER_DECK_ID,))

    conn.execute("INSERT INTO notetypes VALUES (?, ?, 50, 0, X'')", (SEED_MID, SEED_NOTETYPE))
    for ord_, name in enumerate(SEED_FIELDS):
        conn.execute("INSERT INTO fields VALUES (?, ?, ?, X'')", (SEED_MID, ord_, name))
    conn.execute("INSERT INTO templates VALUES (?, 0, 'Card 1', 50, 0, X'')", (SEED_MID,))

    flds = "\x1f".join(("1", "hus", "noun", "et", "/hʉːs/", "house", "a building"))
    conn.execute(
        "INSERT INTO notes VALUES (?, 'guid-hus', ?, 100, 7, '', ?, 'hus', 0, 0, '')",
        (NOTE_ID, SEED_MID, flds),
    )
    conn.execute(
        "INSERT INTO cards VALUES (?, ?, ?, 0, 100, 7, 2, 2, 900, 30, 2500, 9, 0, 0, 0, 0, 0, '')",
        (CARD_ID, NOTE_ID, DECK_ID),
    )
    # A NEW card elsewhere in the collection: the mint's new-queue position must
    # land past it, not at 1.
    conn.execute(
        "INSERT INTO notes VALUES (?, 'guid-bil', ?, 100, 7, '', ?, 'bil', 0, 0, '')",
        (OTHER_NOTE_ID, SEED_MID, flds),
    )
    conn.execute(
        "INSERT INTO cards VALUES (?, ?, ?, 0, 100, 7, 0, 0, ?, 0, 0, 0, 0, 0, 0, 0, 0, '')",
        (OTHER_CARD_ID, OTHER_NOTE_ID, OTHER_DECK_ID, OTHER_DUE),
    )

    if migrate:
        profile = get_profile(SEED_NOTETYPE)
        assert profile is not None
        add_production_template(conn, SEED_NOTETYPE, profile)
    conn.commit()
    return conn


def _flds(conn: sqlite3.Connection, note_id: int = NOTE_ID) -> list[str]:
    return conn.execute("SELECT flds FROM notes WHERE id = ?", (note_id,)).fetchone()["flds"].split("\x1f")


def _image_ord(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT ord FROM fields WHERE ntid = ? AND name = ?", (SEED_MID, IMAGE_FIELD)).fetchone()
    return row["ord"]


def _production_ord(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT ord FROM templates WHERE ntid = ? AND name = ?", (SEED_MID, PRODUCTION_TEMPLATE)
    ).fetchone()
    return row["ord"]


def _cards_for(conn: sqlite3.Connection, note_id: int = NOTE_ID) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM cards WHERE nid = ? ORDER BY ord", (note_id,)).fetchall()


def _col_mod(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT mod FROM col").fetchone()["mod"]


class TestMintProductionCard:
    """``OfflineWriter.mint_production_card`` — image write + card, atomically."""

    def test_writes_the_image_and_creates_the_production_card(self) -> None:
        conn = _make_conn()
        writer = OfflineWriter(conn)
        before = _col_mod(conn)

        minted = writer.mint_production_card(NOTE_ID, IMAGE_TAG)

        assert minted.created is True
        cards = _cards_for(conn)
        assert [c["ord"] for c in cards] == [0, _production_ord(conn)]
        card = cards[1]
        assert card["id"] == minted.card_id
        assert card["due"] == minted.due
        # Added, not graded: a NEW card at the tail of the new queue.
        assert (card["type"], card["queue"], card["reps"], card["ivl"]) == (0, 0, 0, 0)
        assert card["due"] == OTHER_DUE + 1
        # Same deck as its recognition sibling — never deck 0.
        assert card["did"] == DECK_ID
        assert card["usn"] == -1

        # ...and the image is in the note, in the same transaction.
        assert _flds(conn)[_image_ord(conn)] == IMAGE_TAG
        note = conn.execute("SELECT usn FROM notes WHERE id = ?", (NOTE_ID,)).fetchone()
        assert note["usn"] == -1
        assert _col_mod(conn) != before

    def test_adopts_a_card_anki_already_generated(self) -> None:
        """The stranding self-heal: Anki minted ord=1 with an id TT never chose."""
        conn = _make_conn()
        anki_card_id = 5555
        prod_ord = _production_ord(conn)
        conn.execute(
            "INSERT INTO cards VALUES (?, ?, ?, ?, 100, 7, 0, 0, 77, 0, 0, 0, 0, 0, 0, 0, 0, '')",
            (anki_card_id, NOTE_ID, DECK_ID, prod_ord),
        )
        conn.commit()

        minted = OfflineWriter(conn).mint_production_card(NOTE_ID, IMAGE_TAG)

        assert minted == (anki_card_id, 77, False)
        # No sibling: exactly one production card on the note.
        assert [c["ord"] for c in _cards_for(conn)] == [0, prod_ord]
        # The image still gets written — adopting is not a reason to skip it.
        assert _flds(conn)[_image_ord(conn)] == IMAGE_TAG

    def test_re_minting_the_same_note_changes_nothing(self) -> None:
        conn = _make_conn()
        writer = OfflineWriter(conn)
        first = writer.mint_production_card(NOTE_ID, IMAGE_TAG)
        mod_after_first = _col_mod(conn)

        second = writer.mint_production_card(NOTE_ID, IMAGE_TAG)

        assert second == (first.card_id, first.due, False)
        assert len(_cards_for(conn)) == 2
        # A no-op mint must not re-stamp the collection as changed.
        assert _col_mod(conn) == mod_after_first

    def test_refuses_an_empty_image_and_writes_nothing(self) -> None:
        """Rule 3 — an ord=1 card with a blank front is an *empty card* to Anki."""
        conn = _make_conn()
        before = _col_mod(conn)

        with pytest.raises(ValueError, match="empty"):
            OfflineWriter(conn).mint_production_card(NOTE_ID, "   ")

        assert len(_cards_for(conn)) == 1
        assert _col_mod(conn) == before

    def test_leaves_the_image_unwritten_when_the_notetype_has_no_production_template(self) -> None:
        """Rule 1, stated as its failure: no image without a card to render it."""
        conn = _make_conn(migrate=False)
        # Half-migrated: the field landed, the template did not. Minting here
        # would put a card at an ord with no template — an orphan Check Database
        # deletes — so the guard must fire before the image is written.
        conn.execute("INSERT INTO fields VALUES (?, ?, ?, X'')", (SEED_MID, len(SEED_FIELDS), IMAGE_FIELD))
        conn.commit()
        before = _col_mod(conn)

        with pytest.raises(ValueError, match=PRODUCTION_TEMPLATE):
            OfflineWriter(conn).mint_production_card(NOTE_ID, IMAGE_TAG)

        assert len(_cards_for(conn)) == 1
        assert IMAGE_TAG not in conn.execute("SELECT flds FROM notes WHERE id = ?", (NOTE_ID,)).fetchone()["flds"]
        assert _col_mod(conn) == before

    def test_rejects_a_notetype_with_a_production_template_but_no_image_field(self) -> None:
        conn = _make_conn(migrate=False)
        conn.execute("INSERT INTO templates VALUES (?, 1, ?, 50, -1, X'')", (SEED_MID, PRODUCTION_TEMPLATE))
        conn.commit()

        with pytest.raises(ValueError, match=IMAGE_FIELD):
            OfflineWriter(conn).mint_production_card(NOTE_ID, IMAGE_TAG)

        assert len(_cards_for(conn)) == 1

    def test_production_capable_reads_the_notetype_not_the_tt_row(self) -> None:
        """The promotion phase's filter: capability is a fact about the collection."""
        conn = _make_conn()
        writer = OfflineWriter(conn)

        assert writer.production_capable(NOTE_ID) is True
        # A TT row pointing at a note Anki no longer has (orphan recovery's
        # territory) must answer False rather than raise — the phase skips it.
        assert writer.production_capable(424242) is False
        assert OfflineWriter(_make_conn(migrate=False)).production_capable(NOTE_ID) is False

    def test_rejects_an_unknown_note(self) -> None:
        conn = _make_conn()
        with pytest.raises(ValueError, match="not found"):
            OfflineWriter(conn).mint_production_card(424242, IMAGE_TAG)

    def test_rejects_a_note_with_no_cards_at_all(self) -> None:
        """Without a sibling there is no deck to inherit — never mint into deck 0."""
        conn = _make_conn()
        conn.execute("DELETE FROM cards WHERE nid = ?", (NOTE_ID,))
        conn.commit()

        with pytest.raises(ValueError, match="no cards"):
            OfflineWriter(conn).mint_production_card(NOTE_ID, IMAGE_TAG)

        assert _cards_for(conn) == []

    def test_allocates_past_a_taken_card_id(self) -> None:
        """``note_id + ord`` is the convention; a collision bumps rather than fails."""
        conn = _make_conn()
        prod_ord = _production_ord(conn)
        conn.execute(
            "INSERT INTO cards VALUES (?, ?, ?, 0, 100, 7, 0, 0, 5, 0, 0, 0, 0, 0, 0, 0, 0, '')",
            (NOTE_ID + prod_ord, OTHER_NOTE_ID, DECK_ID),
        )
        conn.commit()

        minted = OfflineWriter(conn).mint_production_card(NOTE_ID, IMAGE_TAG)

        assert minted.card_id == NOTE_ID + prod_ord + 1

    def test_pads_a_note_whose_flds_predates_the_image_field(self) -> None:
        """A note that missed the migration's separator sweep still mints cleanly."""
        conn = _make_conn()
        conn.execute("UPDATE notes SET flds = ? WHERE id = ?", ("1\x1fhus", NOTE_ID))
        conn.commit()

        OfflineWriter(conn).mint_production_card(NOTE_ID, IMAGE_TAG)

        parts = _flds(conn)
        assert len(parts) == len(SEED_FIELDS) + 1
        assert parts[_image_ord(conn)] == IMAGE_TAG


class TestAddProductionDirection:
    """``SRSDatabase.add_production_direction`` — a direction, not a collocation."""

    LANG = "sl"

    def _recognition_only(self, srs_db, text: str = "hus") -> tuple[str, int]:
        """Seed the legacy shape: a collocation with recognition and nothing else."""
        unit = SyntacticUnit(text=text, translation="house", word_count=1, difficulty=1, source="anki", frequency=0)
        state = DirectionState(
            direction=Direction.RECOGNITION,
            due_at=due_at_rollover_utc(anki_today()),
            state=SRSState.REVIEW,
            reps=9,
            anki_card_id=CARD_ID,
        )
        coll_id = srs_db.upsert_by_guid(unit, self.LANG, {Direction.RECOGNITION: state}, anki_note_id=NOTE_ID)
        guid = srs_db.get_guid_by_collocation_id(coll_id)
        assert guid is not None
        return guid, coll_id

    def test_adds_one_production_direction_and_no_collocation(self, srs_db) -> None:
        guid, coll_id = self._recognition_only(srs_db)
        before = srs_db.count_collocations()

        added = srs_db.add_production_direction(coll_id, anki_card_id=7777, anki_due=941)

        assert added is True
        assert srs_db.count_collocations() == before
        item = srs_db.get_collocation_by_guid(guid)
        assert set(item.directions) == {Direction.RECOGNITION, Direction.PRODUCTION}
        prod = item.directions[Direction.PRODUCTION]
        # Added, not graded (.claude/rules/anki-sync.md § "adds cards").
        assert prod.state is SRSState.NEW
        assert (prod.reps, prod.lapses) == (0, 0)
        assert prod.last_review is None
        assert prod.introduced_at is None
        assert prod.dirty_fsrs is False
        # Linked to the real minted card, never NULL.
        assert prod.anki_card_id == 7777
        assert prod.anki_due == 941
        assert prod.due_at == due_at_rollover_utc(anki_today())

    def test_leaves_the_recognition_direction_untouched(self, srs_db) -> None:
        guid, coll_id = self._recognition_only(srs_db)

        srs_db.add_production_direction(coll_id, anki_card_id=7777, anki_due=941)

        rec = srs_db.get_collocation_by_guid(guid).directions[Direction.RECOGNITION]
        assert rec.state is SRSState.REVIEW
        assert rec.reps == 9
        assert rec.anki_card_id == CARD_ID

    def test_is_a_no_op_when_a_production_direction_exists(self, srs_db) -> None:
        guid, coll_id = self._recognition_only(srs_db)
        assert srs_db.add_production_direction(coll_id, anki_card_id=7777, anki_due=941) is True

        assert srs_db.add_production_direction(coll_id, anki_card_id=8888, anki_due=999) is False

        prod = srs_db.get_collocation_by_guid(guid).directions[Direction.PRODUCTION]
        assert (prod.anki_card_id, prod.anki_due) == (7777, 941)

    def test_is_a_no_op_for_an_unknown_collocation(self, srs_db) -> None:
        assert srs_db.add_production_direction(999999, anki_card_id=7777, anki_due=941) is False
