"""Dictionary layers written straight into TT, for a learner with no Anki (tunatale-h0q3.2).

The owner's cards get their layers through Anki: note fields, then the pull's
``extract_back_fields``. The second learner has no Anki, so TT writes the
extras itself. The oracle is the pull: the extras written here must equal what
the pull would make from a note carrying the same fields.
"""

from __future__ import annotations

from app.cards.dictionary_extras import dictionary_extras
from app.cards.vocab_notetype import CEBUANO_VOCAB
from app.languages import dictionary_note_fields
from app.models.syntactic_unit import BackField, SyntacticUnit
from app.plugins.anki_sync.sqlite_reader import AnkiNote, extract_back_fields
from app.srs.database import SRSDatabase


def _pulled(word: str, english: str) -> tuple[BackField, ...]:
    by_name = {"Cebuano": word, "English": english, **dictionary_note_fields("ceb", word, english)}
    names = CEBUANO_VOCAB.field_names
    note = AnkiNote(1, "g", 1, 0, [], [by_name.get(n, "") for n in names], CEBUANO_VOCAB.name, names)
    return extract_back_fields(note, "ceb")


def test_the_extras_equal_what_the_pull_makes_from_the_same_note():
    for word, english in [("maayo", "good"), ("tan-aw", "to look"), ("tulo", "three")]:
        extras = dictionary_extras("ceb", word, english)
        assert extras == _pulled(word, english)
        assert extras[0] == BackField("Spelling", dictionary_note_fields("ceb", word, english)["Spelling"], "headword")


def test_a_word_wolff_does_not_settle_gets_none():
    assert dictionary_extras("ceb", "xyzzy", "nothing") == ()


def test_a_language_without_a_dictionary_gets_none():
    assert dictionary_extras("no", "være", "to be") == ()
    assert dictionary_extras("en", "be", "be") == ()


class TestDatabase:
    def _db(self) -> SRSDatabase:
        db = SRSDatabase(":memory:")
        for text, tr, card_type in [("maayo", "good", "vocab"), ("nâa", "there is", "cloze"), ("xyzzy", "?", "vocab")]:
            db.add_collocation(
                SyntacticUnit(
                    text=text, translation=tr, word_count=1, difficulty=1, source="corpus", card_type=card_type
                ),
                language_code="ceb",
            )
        db.add_collocation(
            SyntacticUnit(text="hus", translation="house", word_count=1, difficulty=1, source="corpus"),
            language_code="no",
        )
        return db

    def test_lists_the_languages_vocab_cards_only(self):
        rows = self._db().list_vocab_for_extras("ceb")
        assert sorted((r.text, r.translation) for r in rows) == [("maayo", "good"), ("xyzzy", "?")]
        assert all(r.extras == () and r.anki_note_id is None for r in rows)

    def test_set_extras_replaces_the_stored_extras(self):
        db = self._db()
        row = next(r for r in db.list_vocab_for_extras("ceb") if r.text == "maayo")
        extras = (BackField("Spelling", "màáyo", "headword"),)
        db.set_extras(row.id, extras)
        assert db.get_collocation("maayo").syntactic_unit.extras == extras
        assert next(r for r in db.list_vocab_for_extras("ceb") if r.text == "maayo").extras == extras
