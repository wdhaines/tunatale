"""The affix drill card's checked chain, Cebuano mo- / mi- (tunatale-5i1p.6, oracles C1-C4, C7).

A chain is a model sentence and six steps, each a cue and an answer, every
step's sentence before being the previous answer. Two roots: A, one the
studied lesson's drill voiced, says the model and step 1; C, a card-only root
the audio drill never voiced, says steps 2 to 6.

Oracle: the owner's chain in ``brief-ve4p-affix-drill-card-2026-11.md``, word
for word. Drilled roots are the owner's two studied mo- / mi- lessons (day 3:
``uban, inom, adto, anhi, lakaw``; day 5: ``sulod, adto, inom, anhi``).
"""

from __future__ import annotations

import logging

from app.languages import get_a1_morphology
from app.plugins.languages.ceb.affix_patterns import (
    CARD_ROOTS_PATH,
    GLOSSES_PATH,
    PATTERNS,
    chain_roots,
    load_affix_glosses,
    pattern_chain,
    pattern_forms,
    pattern_roots,
)
from app.srs.a1_morphology import ChainStep, PatternChain

_BY_KEY = {p.key: p for p in PATTERNS}
_MO_MI = _BY_KEY["mo-mi"]
_DAY_3 = ("uban", "inom", "adto", "anhi", "lakaw")
_DAY_5 = ("sulod", "adto", "inom", "anhi")


def _knows(*roots: str):
    return lambda root: root in roots


# The brief's table: steps 2 to 6 are C (gawas) whichever A the lesson gives.
_C_STEPS = (
    ChainStep("gawas", "Migawas ko gahapon.", "I went out yesterday."),
    ChainStep("kada adlaw", "Mogawas ko kada adlaw.", "I go out every day."),
    ChainStep("unya", "Mogawas ko unya.", "I'll go out later."),
    ChainStep("ganiha", "Migawas ko ganiha.", "I went out earlier today."),
    ChainStep("wala", "Wala ko mogawas ganiha.", "I didn't go out earlier today."),
)


def test_c1_the_owners_chain_word_for_word():
    chain = pattern_chain(_MO_MI, _DAY_3, _knows("gawas"))

    assert chain == PatternChain(
        model="Molakaw ko ugma.",
        english="I'll walk tomorrow.",
        steps=(ChainStep("gahapon", "Milakaw ko gahapon.", "I walked yesterday."), *_C_STEPS),
    )
    assert len(chain.steps) == 6


def test_c2_root_a_is_the_first_chain_root_the_lesson_drilled():
    chain = pattern_chain(_MO_MI, _DAY_5, _knows("gawas"))

    assert chain.model == "Moanhi ko ugma."
    assert chain.steps[0] == ChainStep("gahapon", "Mianhi ko gahapon.", "I came yesterday.")
    assert chain.steps[1:] == _C_STEPS


def test_c3_no_understood_card_only_root_takes_the_next_chain_root(caplog):
    with caplog.at_level(logging.INFO):
        chain = pattern_chain(_MO_MI, _DAY_3, _knows())  # gawas not learned, abot still NEW

    # A is lakaw; the chain root after it is balik.
    assert chain.model == "Molakaw ko ugma."
    assert chain.steps[1] == ChainStep("balik", "Mibalik ko gahapon.", "I returned yesterday.")
    assert chain.steps[-1].answer == "Wala ko mobalik ganiha."
    assert "DRILL_CARD_NO_NEW_ROOT pattern=mo-mi" in caplog.text


def test_c3_the_second_card_only_root_stands_in_when_the_first_is_not_understood():
    chain = pattern_chain(_MO_MI, _DAY_3, _knows("abot"))

    assert chain.steps[1] == ChainStep("abot", "Miabot ko gahapon.", "I arrived yesterday.")


def test_c4_a_lesson_that_drilled_no_chain_root_has_no_chain(caplog):
    with caplog.at_level(logging.INFO):
        assert pattern_chain(_MO_MI, ("uban", "adto", "sulod"), _knows("gawas")) is None

    assert "DRILL_CARD_NO_ROOT pattern=mo-mi" in caplog.text


def test_c6_every_other_pattern_has_no_chain():
    for pattern in PATTERNS:
        if pattern.key != "mo-mi":
            assert pattern_chain(pattern, _DAY_3, _knows("gawas")) is None, pattern.key


def test_the_hook_is_registered_for_cebuano():
    assert get_a1_morphology("ceb").pattern_chain is pattern_chain


def test_c7_every_card_only_row_is_vouched_and_never_reaches_the_audio_drill():
    rows = load_affix_glosses(CARD_ROOTS_PATH)
    glosses = load_affix_glosses(GLOSSES_PATH)

    assert [root for root, _ in rows] == ["gawas", "abot"]
    for root, key in rows:
        pattern = _BY_KEY[key]
        assert pattern_forms(root, pattern) is not None, root
        assert (root, key) not in glosses, root
        assert root not in pattern_roots(pattern), root


def test_every_chain_root_is_a_vouched_gloss_row_of_its_pattern():
    roots = chain_roots(_MO_MI)

    assert roots == ("lakaw", "balik", "anhi", "kaon", "inom")
    for root in roots:
        assert pattern_forms(root, _MO_MI) is not None, root
        assert root in pattern_roots(_MO_MI), root
