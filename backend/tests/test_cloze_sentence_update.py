"""Persisting a regenerated cloze sentence (tunatale-keb0).

The ``/review`` "try again" control writes the new sentence to the TT row and
marks it dirty; the next sync carries it into Anki through
``OfflineWriter.update_cloze_text``. The endpoint itself never opens the
collection — ``.claude/rules/anki-safety-core.md``: the collection is read at
sync time only, never on a live request path.
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
        db.set_cloze_sentence(row_id, "Kari kommer. {{c1::Hun}} tar toget.")
        _rid, item, _lang = db.get_collocation_by_id(row_id)
        assert item.syntactic_unit.source_sentence == "Kari kommer. {{c1::Hun}} tar toget."

    def test_marks_source_sentence_dirty_so_the_next_sync_pushes_it(self):
        db, row_id = _db_with_cloze()
        db.set_cloze_sentence(row_id, "Noe nytt med {{c1::han}} i.")
        _rid, item, _lang = db.get_collocation_by_id(row_id)
        assert "source_sentence" in db.get_dirty_fields(item.guid)

    def test_preserves_dirty_flags_it_did_not_set(self):
        """A pending translation edit must not be dropped by a sentence rewrite."""
        db, row_id = _db_with_cloze()
        _rid, item, _lang = db.get_collocation_by_id(row_id)
        db.set_dirty_fields(item.guid, "sentence_translation")
        db.set_cloze_sentence(row_id, "Noe nytt med {{c1::han}} i.")
        flags = set(db.get_dirty_fields(item.guid).split(","))
        assert {"sentence_translation", "source_sentence"} <= flags

    def test_leaves_the_guid_alone(self):
        """A cloze collocation's guid is built from the WORD, not the sentence.

        Only the ANKI note guid is text-derived. Recomputing here would strand
        the row's `anki_note_id` link and the base-word link with it.
        """
        db, row_id = _db_with_cloze()
        _rid, before, _lang = db.get_collocation_by_id(row_id)
        db.set_cloze_sentence(row_id, "Helt annen setning med {{c1::han}} i.")
        _rid2, after, _lang2 = db.get_collocation_by_id(row_id)
        assert after.guid == before.guid

    def test_unknown_id_is_a_noop(self):
        db, _row_id = _db_with_cloze()
        db.set_cloze_sentence(999_999, "irrelevant {{c1::han}}")
