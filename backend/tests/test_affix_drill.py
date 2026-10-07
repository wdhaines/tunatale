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


def test_the_script_is_model_then_your_turn_then_new_roots_then_the_line():
    drill = build_affix_drill(
        "ceb",
        _MAG_NAG,
        roots=["lakaw", "ampo"],
        new_roots=["luto"],
        line=("Naglakaw si Paul sa dalan.", "Paul walked on the road."),
    )
    assert drill.dropped == []
    assert drill.steps == [
        # Meet the pair: the root, then each form after its English.
        DrillStep("model", "walk", "en"),
        DrillStep("model", "lakaw", "ceb"),
        DrillStep("model", "will walk", "en"),
        DrillStep("model", "maglakaw", "ceb"),
        DrillStep("model", "walked", "en"),
        DrillStep("model", "naglakaw", "ceb"),
        DrillStep("model", "pray", "en"),
        DrillStep("model", "ampo", "ceb"),
        DrillStep("model", "will pray", "en"),
        DrillStep("model", "mag-ampo", "ceb"),
        DrillStep("model", "prayed", "en"),
        DrillStep("model", "nag-ampo", "ceb"),
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
        # The whole line.
        DrillStep("prompt", "Say: Paul walked on the road.", "en"),
        DrillStep("answer", "Naglakaw si Paul sa dalan.", "ceb"),
    ]


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
