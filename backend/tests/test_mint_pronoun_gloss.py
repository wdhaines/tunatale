"""The listen/grade mint glosses a listed pronoun from the pronoun table (tunatale-v2tz).

Cebuano 'ila' reached the deck as "their (ila + the linker -ng)": the lesson's
gloss map was keyed from the surface ``ilang``, and story.py's pronoun-table
override runs over surfaces only, so the lemma ``ila`` (never a surface in
that lesson) kept the hand-written parts narration. ``POST /items`` already
applied ``pronoun_gloss``; the mint did not (design-short-forms-and-linkers
§ 3 R2, oracle rows O1/O1c).

The fix lives inside ``_resolve_gloss_translation`` because the listen preview
and the create path both resolve through it, so the previewed gloss and the
stored gloss stay identical by construction.
"""

from __future__ import annotations

import logging

from app.api.srs import _resolve_gloss_translation


def _resolve(lemma, glosses, surfaces, first, upos=None):
    return _resolve_gloss_translation(
        lemma,
        glosses,
        surfaces,
        first,
        language_code="ceb",
        surface_upos=upos,
        warn_on_missing=False,
    )


def test_pronoun_lemma_from_a_joined_surface_takes_the_table_gloss():
    """O1: lemma ila, surface ilang, the lesson map narrating the linker."""
    out = _resolve("ila", {"ilang": "their (ila + the linker -ng)"}, {"ilang"}, "ilang")
    assert out == "their, theirs; by them"


def test_pronoun_lemma_gloss_from_the_lemma_key_is_replaced_too():
    """story.py's setdefault(lemma, …) fills the lemma key with the surface's gloss."""
    out = _resolve(
        "ila",
        {"ila": "their (ila + the linker -ng)", "ilang": "their, theirs; by them"},
        {"ilang"},
        "ilang",
    )
    assert out == "their, theirs; by them"


def test_control_surface_that_is_the_pronoun_keeps_the_table_gloss():
    """O1c: nako as its own surface already carried the table gloss."""
    assert _resolve("nako", {"nako": "my; by me"}, {"nako"}, "nako") == "my; by me"


def test_pronoun_with_no_lesson_gloss_still_gets_the_table_gloss(caplog):
    """A missing lesson gloss is not a blank card when the table has the word."""
    with caplog.at_level(logging.WARNING):
        out = _resolve_gloss_translation("ikaw", {}, {"ikaw"}, "ikaw", language_code="ceb")
    assert out == "you (one person)"
    assert "No gloss" not in caplog.text


def test_tagged_as_another_part_of_speech_keeps_the_lesson_gloss():
    """Same tag gate as POST /items: a listed word the tagger read as a NOUN is not that pronoun."""
    out = _resolve("siya", {"siya": "chair"}, {"siya"}, "siya", upos={"siya": "NOUN"})
    assert out == "chair"


def test_tagged_pron_takes_the_table_gloss():
    out = _resolve("siya", {"siya": "he"}, {"siya"}, "siya", upos={"siya": "PRON"})
    assert out == "he / she"


def test_non_pronoun_is_untouched():
    out = _resolve("balay", {"balay": "house"}, {"balay"}, "balay")
    assert out == "house"
