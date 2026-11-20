"""Cebuano ``ni``: the personal-name marker, or the short form of ``kini`` 'this' (tunatale-qjjc).

The owner's rule (2026-11-20): ``ni`` IMMEDIATELY followed by a capitalised word
is the marker ('ang balay ni Maria'); any other ``ni`` is ``kini`` ('Pila man ni?',
'Nindot ni nga balay'). Punctuation between ``ni`` and the next word means a
new clause, so the capital there starts a sentence and names no one. The marker
keeps its one cloze; ``ni`` 'this' counts for the kini vocab card, as ``ning``
already does. No new cards.

The rule is data: ``name_marker_short_forms`` in the language's
``function_words.json``.
"""

from __future__ import annotations

import pytest

from app.languages import get_lemma_table_path
from app.models.syntactic_unit import SyntacticUnit
from app.srs.database import SRSDatabase
from app.srs.function_words import is_function_word_for, name_marker_short_forms
from app.srs.lemma_table import TableLemmatizer
from app.srs.transcript import extract_transcript
from tests.test_transcript import _make_lesson


@pytest.fixture(scope="module")
def ceb():
    lemmatizer = TableLemmatizer("ceb", get_lemma_table_path("ceb"))
    yield lemmatizer
    lemmatizer.close()


def _ni(lemmatizer: TableLemmatizer, sentence: str) -> tuple[str, str]:
    (token,) = [t for t in lemmatizer.analyze_sentence(sentence, "ceb") if t.surface.casefold() == "ni"]
    return token.lemma, token.upos


# ── the rule, in the lemmatizer ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "sentence",
    ["Pila man ni?", "Nindot ni nga balay.", "Unsa ni? Maayo.", "Kaon ni, Pedro."],
)
def test_ni_not_before_a_name_is_kini(ceb, sentence):
    assert _ni(ceb, sentence) == ("kini", "PRON")


@pytest.mark.parametrize("sentence", ["Ang balay ni Maria.", "Ni Pedro kini.", "Libro ni Juan ang akoa."])
def test_ni_before_a_name_is_the_marker(ceb, sentence):
    assert _ni(ceb, sentence) == ("ni", "DET")


def test_the_surface_stays_ni(ceb):
    (token,) = [t for t in ceb.analyze_sentence("Pila man ni?", "ceb") if t.lemma == "kini"]
    assert token.surface == "ni"


def test_a_caller_tag_does_not_override_the_rule(ceb):
    """The publish-time LLM resolver hands tags in; the curated rule still decides ``ni``."""
    tokens = ceb.analyze_sentence_with_tags("Pila man ni?", "ceb", {2: "DET"})
    assert (tokens[2].lemma, tokens[2].upos) == ("kini", "PRON")


def test_the_bare_word_without_context_stays_the_marker(ceb):
    """A lone word has no next token to read; the table's default stands."""
    assert ceb.lemmatize("ni", "ceb") == "ni"


def test_the_rule_is_in_the_cache_key(ceb):
    """Sentences cached before the rule read ni as the marker; a new key recomputes them."""
    assert "ni=kini" in ceb._cache_version


def test_a_language_without_the_rule_keeps_its_old_cache_key():
    """Norwegian's cached analyses must not be invalidated by a Cebuano rule."""
    no = TableLemmatizer("no", get_lemma_table_path("no"))
    try:
        assert no._cache_version == f"table-{no._table.source_version}-{no._table.source_id}"
    finally:
        no.close()


def test_the_rule_is_read_from_the_language_data():
    assert name_marker_short_forms("ceb") == {"ni": "kini"}
    assert name_marker_short_forms("no") == {}


# ── the kini card is not a function word because ni is one of its surfaces ────


def test_kini_with_ni_among_its_surfaces_is_not_a_function_word():
    assert is_function_word_for("kini", {"kini", "ni"}, "ceb", {"ni": "PRON"}) is False


def test_the_marker_is_still_a_function_word():
    assert is_function_word_for("ni", {"ni"}, "ceb", {"ni": "DET"}) is True


# ── the screen's data: the reader's word links the kini card ──────────────────


def _lesson(text: str):
    return _make_lesson([("female-1", text)], lang="ceb")


def test_the_reader_links_ni_this_to_the_kini_card_and_the_marker_to_its_cloze(ceb):
    db = SRSDatabase(":memory:")
    db.add_collocation(
        SyntacticUnit(text="kini", translation="this", word_count=1, difficulty=1, source="llm", lemma="kini"),
        language_code="ceb",
    )
    db.add_collocation(
        SyntacticUnit(
            text="ni",
            translation="of (name)",
            word_count=1,
            difficulty=1,
            source="llm",
            lemma="ni",
            card_type="cloze",
        ),
        language_code="ceb",
    )
    kini_id = db.get_collocation_by_lemma_with_id("kini")[0]
    ni_id = db.get_collocation_by_lemma_with_id("ni")[0]

    this = extract_transcript(_lesson("Pila man ni?"), db, ceb).dialogue_lines[0].words
    marker = extract_transcript(_lesson("Ang balay ni Maria."), db, ceb).dialogue_lines[0].words

    (this_ni,) = [w for w in this if w.surface == "ni"]
    (marker_ni,) = [w for w in marker if w.surface == "ni"]
    assert (this_ni.lemma, this_ni.srs_item_id, this_ni.card_type) == ("kini", kini_id, "vocab")
    assert (marker_ni.lemma, marker_ni.srs_item_id, marker_ni.card_type) == ("ni", ni_id, "cloze")


# ── the reader's gloss follows the reading, not the spelling ──────────────────


def _glossed(text: str, glosses: dict[str, str]):
    lesson = _lesson(text)
    lesson.generation_metadata = {"token_glosses": glosses}
    return lesson


@pytest.mark.parametrize(
    ("glosses", "expected"),
    [({"ni": "of", "kini": "this"}, "this"), ({"ni": "of"}, None)],
)
def test_ni_this_shows_kinis_lesson_gloss_never_the_markers(ceb, glosses, expected):
    """The live lesson glossed the spelling ni 'of' (one gloss per spelling), so the
    popup said 'of' over the kini card. Read as kini, ni takes kini's gloss or none."""
    db = SRSDatabase(":memory:")
    words = extract_transcript(_glossed("Pila man ni?", glosses), db, ceb).dialogue_lines[0].words
    (ni,) = [w for w in words if w.surface == "ni"]
    assert ni.gloss == expected


def test_the_marker_keeps_the_spellings_gloss(ceb):
    db = SRSDatabase(":memory:")
    lesson = _glossed("Ang balay ni Maria.", {"ni": "of", "kini": "this"})
    (ni,) = [w for w in extract_transcript(lesson, db, ceb).dialogue_lines[0].words if w.surface == "ni"]
    assert ni.gloss == "of"
