"""Persisting a rewritten cloze sentence (tunatale-keb0).

The Cards viewer's "Cloze sentence…" control writes the accepted sentence to the
TT row and marks it dirty; the next sync carries it into Anki through
``OfflineWriter.update_cloze_text``. The endpoint itself never opens the
collection — ``.claude/rules/anki-safety-core.md``: the collection is read at
sync time only, never on a live request path.

The sentence and its translation move together — see
``test_replaces_the_translation_it_was_given``, which is here because the first
version wrote the sentence alone.
"""

from __future__ import annotations

from app.models.syntactic_unit import SyntacticUnit
from app.srs.database import SRSDatabase


def _db_with_cloze(sentence: str = "{{c1::han}} kommer i morgen"):
    db = SRSDatabase(":memory:")
    unit = SyntacticUnit(
        text="han",
        translation="he",
        word_count=1,
        difficulty=1,
        source="anki",
        lemma="han",
        card_type="cloze",
        source_sentence=sentence,
        source_sentence_translation="he is coming tomorrow",
    )
    db.add_collocation(unit, language_code="no")
    item = db.get_collocation_by_lemma("han")
    return db, db.get_collocation_id_by_guid(item.guid)


class TestSetClozeSentence:
    def test_writes_the_new_sentence(self):
        db, row_id = _db_with_cloze()
        db.set_cloze_sentence(row_id, "Kari kommer. {{c1::Hun}} tar toget.", "Kari is coming. She takes the train.")
        _rid, item, _lang = db.get_collocation_by_id(row_id)
        assert item.syntactic_unit.source_sentence == "Kari kommer. {{c1::Hun}} tar toget."

    def test_replaces_the_translation_it_was_given(self):
        """The English must describe the sentence now stored, not its predecessor.

        Writing ``source_sentence`` alone left the card back showing an English
        line under a Norwegian sentence it does not translate — on ``/review``
        and, once ``sync_push`` rebuilt Back Extra, in Anki too. ``translation``
        is a required argument for this reason: there is no call shape that means
        "leave the old one".
        """
        db, row_id = _db_with_cloze()
        db.set_cloze_sentence(row_id, "Kari ringte. {{c1::Han}} tar toget.", "Kari called. He takes the train.")
        _rid, item, _lang = db.get_collocation_by_id(row_id)
        assert item.syntactic_unit.source_sentence_translation == "Kari called. He takes the train."

    def test_an_empty_translation_clears_rather_than_keeps_the_old_one(self):
        """No translator available is not a reason to keep a wrong one."""
        db, row_id = _db_with_cloze()
        db.set_cloze_sentence(row_id, "Kari ringte. {{c1::Han}} tar toget.", "")
        _rid, item, _lang = db.get_collocation_by_id(row_id)
        assert item.syntactic_unit.source_sentence_translation == ""

    def test_marks_both_fields_dirty_so_the_next_sync_pushes_them(self):
        """Two flags, two jobs: ``source_sentence`` routes to ``update_cloze_text``
        (field 0, so also ``sfld``/``csum``/guid), ``sentence_translation`` is what
        rebuilds Back Extra — and Back Extra is where the sentence ``[sound:]``
        is re-emitted, so a re-synthesized clip rides the same flag."""
        db, row_id = _db_with_cloze()
        db.set_cloze_sentence(row_id, "Noe nytt med {{c1::han}} i.", "Something new.")
        _rid, item, _lang = db.get_collocation_by_id(row_id)
        flags = set(db.get_dirty_fields(item.guid).split(","))
        assert {"source_sentence", "sentence_translation"} <= flags

    def test_preserves_dirty_flags_it_did_not_set(self):
        """A pending edit must not be dropped by a sentence rewrite."""
        db, row_id = _db_with_cloze()
        _rid, item, _lang = db.get_collocation_by_id(row_id)
        db.set_dirty_fields(item.guid, "translation")
        db.set_cloze_sentence(row_id, "Noe nytt med {{c1::han}} i.", "Something new with him in it.")
        flags = set(db.get_dirty_fields(item.guid).split(","))
        assert {"translation", "source_sentence", "sentence_translation"} <= flags

    def test_leaves_the_guid_alone(self):
        """A cloze collocation's guid is built from the WORD, not the sentence.

        Only the ANKI note guid is text-derived. Recomputing here would strand
        the row's `anki_note_id` link and the base-word link with it.
        """
        db, row_id = _db_with_cloze()
        _rid, before, _lang = db.get_collocation_by_id(row_id)
        db.set_cloze_sentence(row_id, "Helt annen setning med {{c1::han}} i.", "A completely different sentence.")
        _rid2, after, _lang2 = db.get_collocation_by_id(row_id)
        assert after.guid == before.guid

    def test_unknown_id_is_a_noop(self):
        db, _row_id = _db_with_cloze()
        db.set_cloze_sentence(999_999, "irrelevant {{c1::han}}", "irrelevant")
