"""The affix drill script: model, your turn, new on this root, whole line (tunatale-ve4p.4).

The script is built with no model call. Forms come from the language's
``pattern_forms`` (native news must use every form) and their English from
``pattern_glosses`` (hand-written, because an affix can change a root's
meaning). A root that fails either is DROPPED with its reason, never guessed,
and a drill with too few roots is an error.
"""

from __future__ import annotations

import pytest

from app.generation.affix_drill import MIN_ROOTS, DrillRoot, DrillStep, build_affix_drill, drill_root
from app.generation.section_builder import build_word_breakdown_spans
from app.languages import get_a1_morphology

_PATTERNS = {p.key: p for p in get_a1_morphology("ceb").patterns}
_MAG_NAG = _PATTERNS["mag-nag"]


def test_a_root_is_paired_with_its_forms_and_their_english():
    assert drill_root("ceb", "lakaw", _MAG_NAG) == DrillRoot(
        "lakaw", "walk", (("maglakaw", "will walk"), ("naglakaw", "walked"))
    )
    assert drill_root("ceb", "tulog", _PATTERNS["ma-na"]) == DrillRoot(
        "tulog", "sleep", (("matulog", "will sleep"), ("natulog", "slept"))
    )


def test_the_english_follows_the_form_not_the_roots_gloss():
    """kita is "see"; with mag-/nag- it is meeting. A prompt worded from the
    gloss would ask for "saw" and accept nagkita."""
    assert drill_root("ceb", "kita", _MAG_NAG) == DrillRoot(
        "kita", "see", (("magkita", "will meet"), ("nagkita", "met"))
    )


@pytest.mark.parametrize(
    ("root", "pattern", "reason"),
    [
        ("tulog", "mag-nag", "native news does not use every form"),
        ("anhi", "mag-nag", "native news does not use every form"),
        ("andam", "mag-nag", "no checked English for this root in this pattern"),
        ("dala", "ma-na", "no checked English for this root in this pattern"),
    ],
)
def test_a_root_that_cannot_be_drilled_says_why(root, pattern, reason):
    assert drill_root("ceb", root, _PATTERNS[pattern]) == reason


def test_a_language_without_patterns_cannot_drill():
    assert drill_root("no", "gå", _MAG_NAG) == "the language registers no affix patterns"


def _built(text: str) -> list[DrillStep]:
    """*text* the way Key Phrases builds it up: whole, the pieces from the end, whole again."""
    return [DrillStep("model", c.text, "ceb", c.source_word, c.span) for c in build_word_breakdown_spans(text, "ceb")]


def test_a_new_form_is_built_up_the_way_key_phrases_builds_a_phrase():
    """The owner's ear verdict (tunatale-ve4p.16): English, then the Cebuano, room
    to repeat, then the Cebuano again; for something brand new, the Key Phrases
    breakdown. The breakdown opens and closes on the whole form, so it IS the
    'say it, then say it again'; every Cebuano step leaves its repeat gap.

    Pinned as a literal once, so the shape is visible without the builder."""
    drill = build_affix_drill("ceb", _MAG_NAG, roots=["lakaw", "ampo"])

    start = drill.steps.index(DrillStep("model", "will walk", "en"))
    assert drill.steps[start : start + 7] == [
        DrillStep("model", "will walk", "en"),
        DrillStep("model", "maglakaw", "ceb"),
        DrillStep("model", "kaw", "ceb", "maglakaw", (2, 3)),
        DrillStep("model", "la", "ceb", "maglakaw", (1, 2)),
        DrillStep("model", "lakaw", "ceb", "maglakaw", (1, 3)),
        DrillStep("model", "mag", "ceb", "maglakaw", (0, 1)),
        DrillStep("model", "maglakaw", "ceb"),
    ]


def test_the_script_is_model_then_your_turn_then_new_roots_then_the_lines():
    drill = build_affix_drill(
        "ceb",
        _MAG_NAG,
        roots=["lakaw", "ampo"],
        new_roots=["luto"],
        lines=[("Naglakaw si Paul sa dalan.", "Paul walked on the road."), ("Mag-ampo ta.", "Let's pray.")],
    )
    assert drill.dropped == []
    assert drill.steps == [
        # Meet the pair: the round is announced, then the root, then each form
        # after its English, built up.
        DrillStep("model", "Listen and repeat.", "en"),
        DrillStep("model", "walk", "en"),
        DrillStep("model", "lakaw", "ceb"),
        DrillStep("model", "will walk", "en"),
        *_built("maglakaw"),
        DrillStep("model", "walked", "en"),
        *_built("naglakaw"),
        DrillStep("model", "pray", "en"),
        DrillStep("model", "ampo", "ceb"),
        DrillStep("model", "will pray", "en"),
        *_built("mag-ampo"),
        DrillStep("model", "prayed", "en"),
        *_built("nag-ampo"),
        # Your turn: asked done-first, so it is not an echo of the model order.
        DrillStep("prompt", "Say: walked.", "en"),
        DrillStep("answer", "naglakaw", "ceb"),
        DrillStep("prompt", "Say: will walk.", "en"),
        DrillStep("answer", "maglakaw", "ceb"),
        DrillStep("prompt", "Say: prayed.", "en"),
        DrillStep("answer", "nag-ampo", "ceb"),
        DrillStep("prompt", "Say: will pray.", "en"),
        DrillStep("answer", "mag-ampo", "ceb"),
        # New on this root: the root is named, the forms are NOT modelled.
        DrillStep("model", "cook", "en"),
        DrillStep("model", "luto", "ceb"),
        DrillStep("prompt", "Say: cooked.", "en"),
        DrillStep("answer", "nagluto", "ceb"),
        DrillStep("prompt", "Say: will cook.", "en"),
        DrillStep("answer", "magluto", "ceb"),
        # Whole lines: every one taught first, the Key Phrases way, and only
        # then asked for (tunatale-ve4p.20, 'Or both. A la key phrases.').
        DrillStep("model", "Listen and repeat.", "en"),
        DrillStep("model", "Paul walked on the road.", "en"),
        *_built("Naglakaw si Paul sa dalan."),
        DrillStep("model", "Let's pray.", "en"),
        *_built("Mag-ampo ta."),
        DrillStep("prompt", "Say: Paul walked on the road.", "en"),
        DrillStep("answer", "Naglakaw si Paul sa dalan.", "ceb"),
        DrillStep("prompt", "Say: Let's pray.", "en"),
        DrillStep("answer", "Mag-ampo ta.", "ceb"),
    ]


def test_a_line_is_never_asked_before_every_line_has_been_taught():
    """Teaching a line and asking for it straight after would be an echo, the
    thing the done-first order of the forms already avoids."""
    lines = [("Naglakaw si Paul sa dalan.", "Paul walked on the road."), ("Mag-ampo ta.", "Let's pray.")]
    drill = build_affix_drill("ceb", _MAG_NAG, roots=["lakaw", "ampo"], lines=lines)

    first_ask = drill.steps.index(DrillStep("prompt", "Say: Paul walked on the road.", "en"))
    assert DrillStep("model", "Mag-ampo ta.", "ceb") in drill.steps[:first_ask]


def test_a_drill_with_no_lines_says_nothing_about_them():
    drill = build_affix_drill("ceb", _MAG_NAG, roots=["lakaw", "ampo"])

    assert [s.text for s in drill.steps].count("Listen and repeat.") == 1
    assert drill.steps[-1] == DrillStep("answer", "mag-ampo", "ceb")


def test_the_mixed_round_asks_every_form_once_more_and_never_root_by_root():
    """After the new roots and before the lines. The earlier rounds ask a root's
    two forms back to back, so the second is half given away by the first; here
    neighbours are different roots, and the cell alternates."""
    drill = build_affix_drill(
        "ceb",
        _MAG_NAG,
        roots=["lakaw", "ampo"],
        new_roots=["luto"],
        lines=[("Mag-ampo ta.", "Let's pray.")],
        again=True,
    )
    answers = [s.text for s in drill.steps if s.kind == "answer"]

    assert answers[:6] == ["naglakaw", "maglakaw", "nag-ampo", "mag-ampo", "nagluto", "magluto"]
    assert answers[6:12] == ["maglakaw", "nag-ampo", "magluto", "naglakaw", "mag-ampo", "nagluto"]
    assert answers[12:] == ["Mag-ampo ta."]
    mixed = drill.steps[drill.steps.index(DrillStep("answer", "magluto", "ceb")) + 1 :]
    assert mixed[:4] == [
        DrillStep("prompt", "Say: will walk.", "en"),
        DrillStep("answer", "maglakaw", "ceb"),
        DrillStep("prompt", "Say: prayed.", "en"),
        DrillStep("answer", "nag-ampo", "ceb"),
    ]


def test_there_is_no_mixed_round_unless_asked_for():
    drill = build_affix_drill("ceb", _MAG_NAG, roots=["lakaw", "ampo"])
    assert len([s for s in drill.steps if s.kind == "answer"]) == 4


def test_roots_that_cannot_be_drilled_are_dropped_and_named():
    drill = build_affix_drill("ceb", _MAG_NAG, roots=["lakaw", "andam", "ampo", "tulog"])
    assert drill.dropped == [
        ("andam", "no checked English for this root in this pattern"),
        ("tulog", "native news does not use every form"),
    ]
    assert [s.text for s in drill.steps if s.kind == "answer"] == ["naglakaw", "maglakaw", "nag-ampo", "mag-ampo"]


def test_a_dropped_new_root_is_named_too():
    drill = build_affix_drill("ceb", _MAG_NAG, roots=["lakaw", "ampo"], new_roots=["andam"])
    assert drill.dropped == [("andam", "no checked English for this root in this pattern")]
    assert DrillStep("model", "andam", "ceb") not in drill.steps


def test_too_few_usable_roots_is_an_error_not_a_thin_drill():
    assert MIN_ROOTS == 2
    with pytest.raises(ValueError, match=r"needs at least 2 roots, got 1 usable \(andam: no checked English"):
        build_affix_drill("ceb", _MAG_NAG, roots=["lakaw", "andam"])
    with pytest.raises(ValueError, match=r"got 0 usable \(none were given\)"):
        build_affix_drill("ceb", _MAG_NAG, roots=[])
