"""A homograph resolves to the card for the MEANING in the sentence (tunatale-u8nz.22).

Norwegian has 17 lemmas with more than one vocab card, told apart by the deck's
Word class (``disambig_key``): ``en`` = "one" (pronoun) / "a, an" (determinative),
``om`` = "if" (conjunction) / "again" (adverb) / "about" (preposition). Every
lookup was ``SELECT … WHERE lemma = ? LIMIT 1``, so the lowest id won whatever the
sentence meant. Measured on the live laptop DB 2026-09-27: the table lemmatizer
tagged ``en`` DET 17 of 18 times and ``om`` ADP 10 of 10, yet every one resolved
to "one" and "if" — a listen graded the wrong card and the reader showed its
gloss.

The token's UPOS picks the one vocab row whose Word class matches. Anything less
certain (no UPOS, no match, two matches) keeps the old first-by-id answer.
"""

from __future__ import annotations

from app.api.srs import _resolve_card_for_lemma
from app.models.lesson import Lesson, Phrase, Section, SectionType
from app.models.syntactic_unit import SyntacticUnit
from app.srs.database import SRSDatabase
from app.srs.lemmatizer import TokenAnalysis
from app.srs.transcript import extract_transcript, resolve_lemma_card

LANG = "no"


def _add(db: SRSDatabase, text: str, translation: str, word_class: str, card_type: str = "vocab") -> int:
    db.add_collocation(
        SyntacticUnit(
            text=text,
            translation=translation,
            word_count=1,
            difficulty=1,
            source="anki",
            lemma=text,
            disambig_key=word_class,
            card_type=card_type,
            source_sentence="Vi prøver {{c1::om}} igjen" if card_type == "cloze" else "",
        ),
        language_code=LANG,
    )
    with db._get_conn() as conn:
        return conn.execute(
            "SELECT id FROM collocations WHERE text = ? AND disambig_key = ?", (text, word_class)
        ).fetchone()[0]


def _om(db: SRSDatabase) -> tuple[int, int, int]:
    conj = _add(db, "om", "if", "conjunction")
    adv = _add(db, "om", "again; about", "adverb")
    prep = _add(db, "om", "about, around, by", "preposition")
    _add(db, "om", "again; about", "", card_type="cloze")
    return conj, adv, prep


class TestResolveLemmaCard:
    def test_the_upos_picks_the_matching_meaning(self):
        db = SRSDatabase(":memory:")
        conj, adv, prep = _om(db)
        assert resolve_lemma_card(db, "om", "ADP")[0] == prep
        assert resolve_lemma_card(db, "om", "ADV")[0] == adv

    def test_either_conjunction_tag_matches_the_decks_conjunction(self):
        """The deck says 'conjunction' (mapped to CCONJ); 'om' = if is SCONJ."""
        db = SRSDatabase(":memory:")
        _add(db, "om", "about, around, by", "preposition")  # first by id: a wrong answer must not pass
        conj = _add(db, "om", "if", "conjunction")
        assert resolve_lemma_card(db, "om", "SCONJ")[0] == conj
        assert resolve_lemma_card(db, "om", "CCONJ")[0] == conj

    def test_the_article_resolves_to_the_determinative(self):
        db = SRSDatabase(":memory:")
        one = _add(db, "en", "one", "pronoun")
        article = _add(db, "en", "a, an, one", "determinative")
        assert resolve_lemma_card(db, "en", "DET")[0] == article
        assert resolve_lemma_card(db, "en", "PRON")[0] == one

    def test_no_upos_keeps_the_first_row(self):
        db = SRSDatabase(":memory:")
        conj, _adv, _prep = _om(db)
        assert resolve_lemma_card(db, "om", None)[0] == conj
        assert resolve_lemma_card(db, "om", "")[0] == conj

    def test_a_upos_no_meaning_has_keeps_the_first_row(self):
        db = SRSDatabase(":memory:")
        conj, _adv, _prep = _om(db)
        assert resolve_lemma_card(db, "om", "VERB")[0] == conj

    def test_two_matching_meanings_keep_the_first_row(self):
        """Slovene twins share a POS (barva = color / paint): nothing to pick by."""
        db = SRSDatabase(":memory:")
        first = _add(db, "barva", "color", "noun")
        _add(db, "barva", "paint", "noun ")  # a second NOUN meaning, distinct disambig
        assert resolve_lemma_card(db, "barva", "NOUN")[0] == first

    def test_a_single_card_is_returned_whatever_the_upos(self):
        """Not a homograph: the tagger's word class is never a reason to miss."""
        db = SRSDatabase(":memory:")
        only = _add(db, "hus", "house", "noun")
        assert resolve_lemma_card(db, "hus", "VERB")[0] == only

    def test_an_unknown_lemma_is_none(self):
        assert resolve_lemma_card(SRSDatabase(":memory:"), "ukjent", "NOUN") is None


class TestListenResolution:
    def test_the_lessons_upos_picks_the_card_a_listen_grades(self):
        db = SRSDatabase(":memory:")
        _conj, _adv, prep = _om(db)
        res = _resolve_card_for_lemma(db, "om", {"om"}, surface_upos={"om": "ADP"})
        assert res[0] == prep

    def test_the_surface_fallback_uses_the_upos_too(self):
        db = SRSDatabase(":memory:")
        _one = _add(db, "en", "one", "pronoun")
        article = _add(db, "en", "a, an, one", "determinative")
        # The lemmatizer's lemma has no card; the surface does.
        res = _resolve_card_for_lemma(db, "ein", {"En"}, surface_upos={"en": "DET"})
        assert res[0] == article

    def test_without_a_upos_map_nothing_changes(self):
        db = SRSDatabase(":memory:")
        conj, _adv, _prep = _om(db)
        assert _resolve_card_for_lemma(db, "om", {"om"})[0] == conj


class _UposLemmatizer:
    """Tags each token from a fixed table; lemma = lowercased surface."""

    _cache_version = "test-homograph-v1"

    def __init__(self, upos: dict[str, str]) -> None:
        self._upos = upos

    def lemmatize(self, word: str, language_code: str) -> str:
        return word.lower()

    def analyze(self, word: str, language_code: str) -> tuple[str, str, str]:
        return word.lower(), self._upos.get(word.lower(), ""), ""

    def analyze_sentence(self, sentence: str, language_code: str) -> list:
        tokens = [t.strip(".,!?") for t in sentence.split()]
        return [TokenAnalysis(surface=t, lemma=t.lower(), upos=self._upos.get(t.lower(), "")) for t in tokens if t]


class TestTranscriptResolution:
    def test_the_reader_shows_the_meaning_in_the_sentence(self):
        db = SRSDatabase(":memory:")
        _one = _add(db, "en", "one", "pronoun")
        article = _add(db, "en", "a, an, one", "determinative")
        _conj, _adv, prep = _om(db)
        lesson = Lesson(title="t", language_code=LANG)
        lesson.sections = [
            Section(
                section_type=SectionType.NATURAL_SPEED,
                phrases=[Phrase(text="Vi snakket om en film.", voice_id="female-1", language_code=LANG, role="a")],
            )
        ]
        lemmatizer = _UposLemmatizer({"vi": "PRON", "snakket": "VERB", "om": "ADP", "en": "DET", "film": "NOUN"})

        words = extract_transcript(lesson, db, lemmatizer).dialogue_lines[0].words
        by_surface = {w.surface.strip(".").lower(): w for w in words}

        assert by_surface["om"].srs_item_id == prep
        assert by_surface["en"].srs_item_id == article
        assert by_surface["om"].translation == "about, around, by"
