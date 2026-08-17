"""The recognition-only tripwire (tunatale-qf6.5).

Norwegian was missing production directions for 2990 of 3009 words and nothing
anywhere said so. Every consumer degraded quietly — the transcript skipped the
affordance, ``/inflection-clozes`` returned a plausible 409, mastery computed
over a shorter component list, the drill simply never served an L2-production
card. The number surfaced only from an ad-hoc ``GROUP BY direction``.

This is a sync-time tripwire in the ``warn_if_guid_collisions`` genre: reports,
never blocks, runs on dry-runs because it is a pure read of the collection. It
exists for the *next* deck imported from the community, not for Norwegian —
whose notetype now has its ``Production`` template, which is exactly why the
tripwire is silent on it today.

**It keys on the root cause, not the symptom.** "Vocab collocations lacking a
production direction" fires 77 false positives on Slovene. A note on a
single-template notetype is *structurally* recognition-only, which is the thing
that was actually wrong. Measured shares, against the real collection
(2026-08-17):

    Slovene deck      53 / 734  = 7.2%   (phonics notes on `Basic`)
    Norwegian deck     0 / 3023 = 0.0%   (post-migration; was ~99% before)

**Cloze-kind notetypes are excluded, and by their config rather than by name.**
``Cloze`` and ``Image Occlusion`` are both single-template and both generate a
card per deletion, so neither is recognition-only. Both declare ``kind=1`` in
``notetypes.config`` — verified against the user's real collection, where a
name-based check would have counted every Image Occlusion note as a defect.
"""

from __future__ import annotations

import logging
import sqlite3

import pytest

from app.cards.field_map import get_profile
from app.cards.vocab_notetype import build_notetype_config
from app.plugins.anki_sync.add_production_template import add_production_template, recognition_only_share
from app.plugins.anki_sync.sync import AnkiSync, OfflineReader, OfflineWriter
from app.srs.database import SRSDatabase

DECK_ID = 17
DECK_NAME = "Imported Deck"
OTHER_DECK_ID = 18
LANG = "no"

SEED_MID = 1694414741634
SEED_NOTETYPE = "6000 Most Frequent Norwegian Words"
SEED_FIELDS = ("Frequency index", "Norwegian word", "Word class", "Article", "IPA", "English translation")

VOCAB_MID = 2000
CLOZE_MID = 3000
BASIC_MID = 4000

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

#: A cloze-kind notetype declares ``kind = 1`` (field 1 of NotetypeConfig). A
#: normal one omits it — protobuf drops zero-valued scalars. Confirmed on the
#: real collection: ``Cloze`` and ``Image Occlusion`` both read 1, every other
#: notetype reads absent.
_CLOZE_KIND_CONFIG = bytes([0x08, 0x01]) + build_notetype_config("")


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.execute("INSERT INTO col VALUES (1, 0, 100, 1000, 18, 0, 5, 0, '{}', '{}', '{}', '{}', '{}')")
    conn.execute("INSERT INTO decks VALUES (?, ?, 50, 0, NULL)", (DECK_ID, DECK_NAME))
    conn.execute("INSERT INTO decks VALUES (?, 'Other Deck', 50, 0, NULL)", (OTHER_DECK_ID,))

    # The imported recognition-only notetype, pre-migration: one template.
    conn.execute("INSERT INTO notetypes VALUES (?, ?, 50, 0, X'')", (SEED_MID, SEED_NOTETYPE))
    for ord_, name in enumerate(SEED_FIELDS):
        conn.execute("INSERT INTO fields VALUES (?, ?, ?, X'')", (SEED_MID, ord_, name))
    conn.execute("INSERT INTO templates VALUES (?, 0, 'Card 1', 50, 0, X'')", (SEED_MID,))

    # A healthy two-template vocab notetype.
    conn.execute("INSERT INTO notetypes VALUES (?, 'TT Vocabulary', 50, 0, X'')", (VOCAB_MID,))
    conn.execute("INSERT INTO templates VALUES (?, 0, 'Recognition', 50, 0, X'')", (VOCAB_MID,))
    conn.execute("INSERT INTO templates VALUES (?, 1, 'Production', 50, 0, X'')", (VOCAB_MID,))

    # Cloze: single-template, but a card per deletion — never a defect.
    conn.execute("INSERT INTO notetypes VALUES (?, 'Cloze', 50, 0, ?)", (CLOZE_MID, _CLOZE_KIND_CONFIG))
    conn.execute("INSERT INTO templates VALUES (?, 0, 'Cloze', 50, 0, X'')", (CLOZE_MID,))

    # Basic: single-template and genuinely recognition-only (the Slovene phonics
    # notes) — legitimate, but it IS the population being measured.
    conn.execute("INSERT INTO notetypes VALUES (?, 'Basic', 50, 0, ?)", (BASIC_MID, build_notetype_config("")))
    conn.execute("INSERT INTO templates VALUES (?, 0, 'Card 1', 50, 0, X'')", (BASIC_MID,))
    conn.commit()
    return conn


def _add_notes(conn: sqlite3.Connection, mid: int, count: int, *, deck_id: int = DECK_ID, start: int = 0) -> None:
    for i in range(start, start + count):
        note_id = mid * 1000 + i
        conn.execute(
            "INSERT INTO notes VALUES (?, ?, ?, 100, 7, '', 'w', 'w', 0, 0, '')",
            (note_id, f"g-{note_id}", mid),
        )
        conn.execute(
            "INSERT INTO cards VALUES (?, ?, ?, 0, 100, 7, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, '')",
            (note_id * 10, note_id, deck_id),
        )
    conn.commit()


def _make_sync(conn: sqlite3.Connection) -> AnkiSync:
    return AnkiSync(
        db=SRSDatabase(":memory:"),
        _reader=OfflineReader(conn, DECK_NAME, language_code=LANG),
        _writer=OfflineWriter(conn),
    )


class TestRecognitionOnlyShare:
    def test_counts_only_single_template_non_cloze_notes_in_the_deck(self) -> None:
        conn = _make_conn()
        _add_notes(conn, SEED_MID, 30)
        _add_notes(conn, VOCAB_MID, 5)
        _add_notes(conn, CLOZE_MID, 5)
        # Same recognition-only notetype, but in a deck this sync doesn't touch.
        _add_notes(conn, BASIC_MID, 100, deck_id=OTHER_DECK_ID)

        share = recognition_only_share(conn, DECK_NAME)

        assert (share.notes, share.total) == (30, 40)
        assert share.fraction == pytest.approx(0.75)
        assert share.by_notetype == [(SEED_NOTETYPE, 30)]

    def test_a_cloze_notetype_is_not_recognition_only(self) -> None:
        """The discriminating case: cloze-kind is single-template by design.

        Without the exclusion a deck that is mostly clozes reads as 100% broken.
        """
        conn = _make_conn()
        _add_notes(conn, CLOZE_MID, 20)
        _add_notes(conn, VOCAB_MID, 5)

        share = recognition_only_share(conn, DECK_NAME)

        assert (share.notes, share.fraction) == (0, 0.0)

    def test_an_empty_or_unknown_deck_is_not_a_defect(self) -> None:
        conn = _make_conn()
        assert recognition_only_share(conn, DECK_NAME).fraction == 0.0
        assert recognition_only_share(conn, "No Such Deck").total == 0


class TestWarnIfRecognitionOnlyDeck:
    def test_fires_on_a_deck_imported_recognition_only(self, caplog) -> None:
        conn = _make_conn()
        _add_notes(conn, SEED_MID, 99)
        _add_notes(conn, VOCAB_MID, 1)

        with caplog.at_level(logging.WARNING, logger="app.anki.sync"):
            fraction = _make_sync(conn).warn_if_recognition_only_deck()

        assert fraction == pytest.approx(0.99)
        assert "RECOGNITION_ONLY_DECK" in caplog.text
        # The message must name the notetype to migrate and the counts, or the
        # reader has to go re-derive them by hand — which is how this went
        # unnoticed for 2990 words in the first place.
        assert SEED_NOTETYPE in caplog.text
        assert "99" in caplog.text and "100" in caplog.text
        assert "add_production_template" in caplog.text

    def test_stays_quiet_on_the_slovene_shape(self, caplog) -> None:
        """The false-positive boundary, at the real ratio: 53 Basic + 24 Cloze in 734."""
        conn = _make_conn()
        _add_notes(conn, BASIC_MID, 53)
        _add_notes(conn, CLOZE_MID, 24)
        _add_notes(conn, VOCAB_MID, 657)

        with caplog.at_level(logging.WARNING, logger="app.anki.sync"):
            fraction = _make_sync(conn).warn_if_recognition_only_deck()

        assert fraction == pytest.approx(53 / 734, abs=1e-4)
        assert "RECOGNITION_ONLY_DECK" not in caplog.text

    def test_goes_quiet_once_the_notetype_gains_its_production_template(self, caplog) -> None:
        """The migration is the fix, so the tripwire must stop firing after it.

        This is why Norwegian reads 0% today even though it is the deck the
        tripwire was written for.
        """
        conn = _make_conn()
        _add_notes(conn, SEED_MID, 99)
        _add_notes(conn, VOCAB_MID, 1)
        assert _make_sync(conn).warn_if_recognition_only_deck() == pytest.approx(0.99)

        profile = get_profile(SEED_NOTETYPE)
        assert profile is not None
        add_production_template(conn, SEED_NOTETYPE, profile)

        caplog.clear()  # the pre-migration call above fired, as it should
        with caplog.at_level(logging.WARNING, logger="app.anki.sync"):
            assert _make_sync(conn).warn_if_recognition_only_deck() == 0.0
        assert "RECOGNITION_ONLY_DECK" not in caplog.text

    def test_an_empty_deck_does_not_divide_by_zero(self, caplog) -> None:
        conn = _make_conn()
        with caplog.at_level(logging.WARNING, logger="app.anki.sync"):
            assert _make_sync(conn).warn_if_recognition_only_deck() == 0.0
        assert caplog.text == ""
