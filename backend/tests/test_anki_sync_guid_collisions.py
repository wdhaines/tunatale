"""Sync-time tripwire for two Anki notes collapsing to one TT guid.

A TT guid is ``(text, language, disambig_key)`` with the part of speech as
disambig. Two Anki notes sharing the same ``(text, POS)`` therefore collapse to
ONE collocation holding TWO candidate cards, and nothing pins which one
``anki_card_id`` follows. ``foran`` alternated between its twins for three weeks
(2026-07-14 → 2026-08-02), splitting one word's review history across both cards
and leaving TT carrying a due date from the card it wasn't tracking.

POS homonyms are NOT this — ``løfte`` noun/verb, ``vår`` noun/determinative and
the other 15 in the Norwegian deck differ in disambig, so they get different
guids and separate pinned collocations. A check keyed on the bare surface form
would flag all of them and be useless; the key must include the disambig.

This is live-fire, not just a tripwire: on its first run against the real
collection it found two collisions the ``foran`` investigation had missed —
duplicate *cloze* notes in the Slovene deck. They were missed because that
investigation hand-rolled the check over raw note fields, which is not the guid
input for cloze notes (their text is the cloze body, disambig empty). Hence
``test_cloze_notes_collide_on_empty_disambig`` below: keying on the reader's
extraction is the whole point.
"""

from __future__ import annotations

from app.plugins.anki_sync.sync import AnkiSync
from app.plugins.anki_sync.sync_common import NoteRecord

_LOGGER = "app.anki.sync"


def _rec(nid: int, text: str, disambig: str) -> NoteRecord:
    return NoteRecord(
        anki_note_id=nid,
        anki_guid=f"g{nid}",
        l2_text=text,
        translation="",
        note="",
        disambig_key=disambig,
        mod=0,
        cards=[],
    )


class _FakeReader:
    def __init__(self, records: list[NoteRecord]) -> None:
        self._records = records

    def get_note_records(self) -> list[NoteRecord]:
        return self._records

    def get_revlog_for_card(self, card_id: int, after_ms: int = 0) -> list:
        return []


def _sync(srs_db, records):
    return AnkiSync(db=srs_db, _reader=_FakeReader(records), _writer=object())


class TestWarnIfGuidCollisions:
    def test_clean_collection_is_silent(self, srs_db, caplog):
        sync = _sync(srs_db, [_rec(1, "foran", "preposition"), _rec(2, "huset", "noun")])
        with caplog.at_level("WARNING", logger=_LOGGER):
            assert sync.warn_if_guid_collisions() == 0
        assert "GUID_COLLISION" not in caplog.text

    def test_pos_homonyms_do_not_collide(self, srs_db, caplog):
        """The whole point: løfte noun/verb is legitimate, not a duplicate."""
        sync = _sync(srs_db, [_rec(1, "løfte", "noun"), _rec(2, "løfte", "verb")])
        with caplog.at_level("WARNING", logger=_LOGGER):
            assert sync.warn_if_guid_collisions() == 0
        assert "GUID_COLLISION" not in caplog.text

    def test_same_text_and_disambig_collides(self, srs_db, caplog):
        sync = _sync(srs_db, [_rec(300232, "foran", "preposition"), _rec(305378, "foran", "preposition")])
        with caplog.at_level("WARNING", logger=_LOGGER):
            assert sync.warn_if_guid_collisions() == 1
        assert "GUID_COLLISION" in caplog.text
        assert "foran" in caplog.text
        assert "300232" in caplog.text and "305378" in caplog.text

    def test_comparison_is_case_insensitive(self, srs_db, caplog):
        """compute_guid casefolds, so the tripwire must too or it misses the pair."""
        sync = _sync(srs_db, [_rec(1, "Foran", "Preposition"), _rec(2, "foran", "preposition")])
        with caplog.at_level("WARNING", logger=_LOGGER):
            assert sync.warn_if_guid_collisions() == 1

    def test_cloze_notes_collide_on_empty_disambig(self, srs_db, caplog):
        """The real 2026-08-02 finding: cloze twins carry no POS to tell them apart.

        Their guid text is the cloze body and disambig is "", so an empty
        disambig must NOT be treated as "no key" and skipped — two cloze notes
        with the same body genuinely collapse.
        """
        body = "kako {{c1::si}}?"
        sync = _sync(srs_db, [_rec(1778769239095, body, ""), _rec(1780517550779, body, "")])
        with caplog.at_level("WARNING", logger=_LOGGER):
            assert sync.warn_if_guid_collisions() == 1
        assert "1778769239095" in caplog.text and "1780517550779" in caplog.text

    def test_distinct_cloze_bodies_do_not_collide(self, srs_db, caplog):
        sync = _sync(srs_db, [_rec(1, "kako {{c1::si}}?", ""), _rec(2, "to {{c1::je}} dobro.", "")])
        with caplog.at_level("WARNING", logger=_LOGGER):
            assert sync.warn_if_guid_collisions() == 0

    def test_counts_each_colliding_group_once(self, srs_db, caplog):
        records = [
            _rec(1, "foran", "preposition"),
            _rec(2, "foran", "preposition"),
            _rec(3, "om", "adverb"),
            _rec(4, "om", "adverb"),
            _rec(5, "om", "conjunction"),
            _rec(6, "huset", "noun"),
        ]
        sync = _sync(srs_db, records)
        with caplog.at_level("WARNING", logger=_LOGGER):
            assert sync.warn_if_guid_collisions() == 2
