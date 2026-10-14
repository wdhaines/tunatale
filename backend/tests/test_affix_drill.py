"""The affix drill script: model, your turn, new roots, everything mixed, whole line (tunatale-ve4p.4).

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
# What one drill asks first, as it came out the first time it was built.
_FIRST_ROUND = ["mouban", "moadto", "moinom", "miadto", "miuban", "miinom"]


def test_a_root_is_paired_with_its_forms_and_their_english():
    assert drill_root("ceb", "lakaw", _MAG_NAG) == DrillRoot(
        "lakaw", "walk", (("maglakaw", "will walk"), ("naglakaw", "is walking"))
    )
    assert drill_root("ceb", "tulog", _PATTERNS["ma-na"]) == DrillRoot(
        "tulog", "sleep", (("matulog", "will sleep"), ("natulog", "is asleep"))
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


def _rounds(drill) -> list[list[str]]:
    """The answers of each asking round, in the order asked. A round opens on the question."""
    rounds: list[list[str]] = []
    for step in drill.steps:
        if step.kind == "prompt" and step.text.startswith("How do you say"):
            rounds.append([])
        if step.kind == "answer":
            rounds[-1].append(step.text)
    return rounds


def _forms(pattern, *roots: str) -> dict[str, str]:
    """Every form of *roots* in *pattern*, each with the root it is a form of."""
    return {form: root for root in roots for form, _ in drill_root("ceb", root, pattern).cells}


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


# --- a root is built up once (the owner, 2026-10-14) ---------------------------
#
# "The Pimsleur buildups are great, but they're too repetitive. We shouldn't
# need to Pimsleur rebuild the root once the root is introduced." Day 5 said
# `lod, su, sulod` for mosulod and again for misulod, and `to, ad, adto` three
# times. The first form of a root is built up in full; after that a form adds
# only its affix, and a word already built is said whole.


def test_the_second_form_of_a_root_adds_only_its_affix():
    drill = build_affix_drill("ceb", _MAG_NAG, roots=["lakaw", "ampo"])

    start = drill.steps.index(DrillStep("model", "is walking", "en"))
    assert drill.steps[start : start + 4] == [
        DrillStep("model", "is walking", "en"),
        DrillStep("model", "naglakaw", "ceb"),
        DrillStep("model", "nag", "ceb", "naglakaw", (0, 1)),
        DrillStep("model", "naglakaw", "ceb"),
    ]


def test_a_hyphenated_form_drops_its_roots_pieces_too():
    drill = build_affix_drill("ceb", _MAG_NAG, roots=["lakaw", "ampo"])

    start = drill.steps.index(DrillStep("model", "is praying", "en"))
    assert drill.steps[start : start + 4] == [
        DrillStep("model", "is praying", "en"),
        DrillStep("model", "nag-ampo", "ceb"),
        DrillStep("model", "nag", "ceb", "nag-ampo", (0, 1)),
        DrillStep("model", "nag-ampo", "ceb"),
    ]


def test_a_form_already_built_is_said_whole_inside_a_line():
    lines = [("Naglakaw si Paul sa dalan.", "Paul walked on the road.")]
    drill = build_affix_drill("ceb", _MAG_NAG, roots=["lakaw", "ampo"], lines=lines)

    start = drill.steps.index(DrillStep("model", "Paul walked on the road.", "en"))
    assert [step.text for step in drill.steps[start + 1 : start + 15]] == [
        "Naglakaw si Paul sa dalan.",
        "lan",
        "da",
        "dalan",
        "sa",
        "sa dalan.",
        "ul",
        "pa",
        "paul",
        "Paul sa dalan.",
        "si",
        "si Paul sa dalan.",
        "naglakaw",
        "Naglakaw si Paul sa dalan.",
    ]


def test_a_line_word_on_a_built_root_adds_only_its_affix():
    """molakaw is no form of mag-/nag-, but its root was built by maglakaw."""
    drill = build_affix_drill("ceb", _MAG_NAG, roots=["lakaw", "ampo"], lines=[("Molakaw ko.", "I will walk.")])

    start = drill.steps.index(DrillStep("model", "I will walk.", "en"))
    assert drill.steps[start + 1 : start + 6] == [
        DrillStep("model", "Molakaw ko.", "ceb"),
        DrillStep("model", "ko", "ceb", "ko", (0, 1)),
        DrillStep("model", "mo", "ceb", "Molakaw", (0, 1)),
        DrillStep("model", "molakaw", "ceb", "Molakaw", (0, 3)),
        DrillStep("model", "Molakaw ko.", "ceb"),
    ]


def test_a_word_built_in_one_line_is_said_whole_in_the_next():
    lines = [("Naglakaw si Paul sa dalan.", "Paul walked on the road."), ("Nag-ampo sa dalan.", "Prayed on the road.")]
    drill = build_affix_drill("ceb", _MAG_NAG, roots=["lakaw", "ampo"], lines=lines)

    start = drill.steps.index(DrillStep("model", "Prayed on the road.", "en"))
    assert [step.text for step in drill.steps[start + 1 : start + 7]] == [
        "Nag-ampo sa dalan.",
        "dalan",
        "sa",
        "sa dalan.",
        "nag-ampo",
        "Nag-ampo sa dalan.",
    ]


def test_the_script_is_model_then_your_turn_then_new_roots_then_everything_then_the_lines():
    lines = [("Naglakaw si Paul sa dalan.", "Paul walked on the road."), ("Mag-ampo ta.", "Let's pray.")]
    drill = build_affix_drill("ceb", _MAG_NAG, roots=["lakaw", "ampo"], new_roots=["luto", "dala"], lines=lines)
    assert drill.dropped == []

    # Meet the pair: the round is announced, then the root, then each form
    # after its English, built up.
    first_ask = next(i for i, step in enumerate(drill.steps) if step.kind == "prompt")
    assert drill.steps[:first_ask] == [
        DrillStep("model", "Listen and repeat.", "en"),
        DrillStep("model", "walk", "en"),
        DrillStep("model", "lakaw", "ceb"),
        DrillStep("model", "will walk", "en"),
        *_built("maglakaw"),
        DrillStep("model", "is walking", "en"),
        DrillStep("model", "naglakaw", "ceb"),
        DrillStep("model", "nag", "ceb", "naglakaw", (0, 1)),
        DrillStep("model", "naglakaw", "ceb"),
        DrillStep("model", "pray", "en"),
        DrillStep("model", "ampo", "ceb"),
        DrillStep("model", "will pray", "en"),
        *_built("mag-ampo"),
        DrillStep("model", "is praying", "en"),
        DrillStep("model", "nag-ampo", "ceb"),
        DrillStep("model", "nag", "ceb", "nag-ampo", (0, 1)),
        DrillStep("model", "nag-ampo", "ceb"),
    ]

    modelled, new = _forms(_MAG_NAG, "lakaw", "ampo"), _forms(_MAG_NAG, "luto", "dala")
    yours, unmodelled, everything, whole = _rounds(drill)
    # Your turn: every modelled form, once.
    assert sorted(yours) == sorted(modelled)
    # New roots: each is named, none of its forms is modelled, and every root
    # is named before any is asked for.
    named = drill.steps[drill.steps.index(DrillStep("answer", yours[-1], "ceb")) + 1 :][:4]
    assert named == [
        DrillStep("model", "cook", "en"),
        DrillStep("model", "luto", "ceb"),
        DrillStep("model", "bring", "en"),
        DrillStep("model", "dala", "ceb"),
    ]
    assert sorted(unmodelled) == sorted(new)
    assert not [step for step in drill.steps if step.kind == "model" and step.text in new]
    # Everything, once more: the forms of the new roots among the modelled ones.
    assert sorted(everything) == sorted([*modelled, *new])
    # Whole lines: every one taught first, the Key Phrases way, and only then
    # asked for (tunatale-ve4p.20, 'Or both. A la key phrases.').
    taught = len(drill.steps) - 1 - drill.steps[::-1].index(DrillStep("model", "Listen and repeat.", "en"))
    assert drill.steps[taught:] == [
        DrillStep("model", "Listen and repeat.", "en"),
        DrillStep("model", "Paul walked on the road.", "en"),
        # naglakaw and mag-ampo were built as forms: each is said whole here.
        *[
            step
            for step in _built("Naglakaw si Paul sa dalan.")
            if step.source_word != "Naglakaw" or step.span == (0, 3)
        ],
        DrillStep("model", "Let's pray.", "en"),
        *[step for step in _built("Mag-ampo ta.") if step.source_word != "Mag-ampo" or step.span == (0, 3)],
        DrillStep("prompt", 'How do you say "Paul walked on the road"?', "en"),
        DrillStep("answer", "Naglakaw si Paul sa dalan.", "ceb"),
        DrillStep("prompt", "Let's pray.", "en"),
        DrillStep("answer", "Mag-ampo ta.", "ceb"),
    ]
    assert whole == [text for text, _ in lines]


# --- the order a round asks in (tunatale-ve4p.19) -------------------------------
#
# The owner, 2026-10-09: "Mixing up the order of the answers is good for
# learning"; asked on 2026-10-13 which reading was meant: "Mix from the first
# asking." Before, the first round went root by root, so the second prompt of a
# root was half given away by the first, and the one mixed round was a fixed
# rotation.

_MO_MI = _PATTERNS["mo-mi"]
_DRILLS = [
    (_MAG_NAG, ["lakaw", "ampo"], []),
    (_MAG_NAG, ["lakaw", "ampo", "kita"], ["luto", "dala"]),
    (_MO_MI, ["uban", "inom", "adto"], ["anhi", "lakaw"]),
    (_MO_MI, ["uban", "inom", "adto", "balik", "kaon"], ["anhi", "lakaw", "sulti"]),
]


@pytest.mark.parametrize(("pattern", "roots", "new_roots"), _DRILLS)
def test_no_round_asks_for_the_same_root_twice_running(pattern, roots, new_roots):
    """From the first asking, and in every round after it."""
    root_of = _forms(pattern, *roots, *new_roots)
    drill = build_affix_drill("ceb", pattern, roots=roots, new_roots=new_roots)

    rounds = _rounds(drill)
    assert len(rounds) == (3 if new_roots else 2)
    for answers in rounds:
        asked = [root_of[form] for form in answers]
        assert all(a != b for a, b in zip(asked, asked[1:], strict=False)), answers


def test_a_round_cannot_be_answered_from_its_position():
    """Neither which root comes next nor which tense is the same from one lesson
    to the next. What tells two drills of one pattern apart is their lines, so
    twelve stories are twelve drills of the same three roots."""
    tense = {form: i for root in ("uban", "inom", "adto") for i, (form, _) in enumerate(_drilled(root).cells)}
    root_of = _forms(_MO_MI, "uban", "inom", "adto")
    firsts = [
        _rounds(build_affix_drill("ceb", _MO_MI, roots=["uban", "inom", "adto"], lines=[(f"Moadto ko {n}.", "x")]))[0]
        for n in range(12)
    ]

    assert len({tuple(root_of[form] for form in answers) for answers in firsts}) > 1
    assert len({tuple(tense[form] for form in answers) for answers in firsts}) > 1
    # The tense does not simply alternate either: that is a position too.
    assert any(tense[a] == tense[b] for answers in firsts for a, b in zip(answers, answers[1:], strict=False))


def test_the_same_drill_is_the_same_script_every_time():
    """The order is drawn from what the drill is made of, not from the clock: a
    lesson rebuilt from its story asks in the order it asked before, and the
    clips it rendered are the ones it asks for again."""
    made = [
        build_affix_drill(
            "ceb", _MO_MI, roots=["uban", "inom", "adto"], new_roots=["anhi"], lines=[("Oo, moadto ko.", "Yes.")]
        )
        for _ in range(2)
    ]
    assert made[0].steps == made[1].steps
    # Pinned as a literal, because 'the same in this process' is not 'the same
    # on the next run': a seed that read the interpreter's hash salt would pass
    # the line above and fail here one run in several.
    assert _rounds(made[0])[0] == _FIRST_ROUND


def test_a_single_new_root_is_named_and_asked_for_both_forms():
    """One root cannot be kept apart from itself; the round after it does that."""
    drill = build_affix_drill("ceb", _MAG_NAG, roots=["lakaw", "ampo"], new_roots=["luto"])

    _, unmodelled, everything = _rounds(drill)
    assert sorted(unmodelled) == ["magluto", "nagluto"]
    luto = [i for i, form in enumerate(everything) if form in unmodelled]
    assert luto[1] - luto[0] > 1


def _drilled(root: str) -> DrillRoot:
    return drill_root("ceb", root, _MO_MI)


@pytest.mark.parametrize(
    ("english", "asked"),
    [
        ("Paul walked on the road.", 'How do you say "Paul walked on the road"?'),
        ("Are you happy, Paul?", 'How do you say "Are you happy, Paul"?'),
        ("Yes, I'm very happy.", 'How do you say "Yes, I\'m very happy"?'),
        ("Stop!", 'How do you say "Stop"?'),
    ],
)
def test_the_first_line_is_asked_for_as_a_question_about_its_english(english, asked):
    """The frame the owner chose by ear on a whole round (2026-10-12): the
    narrator asks the question on a round's first prompt. The question mark is
    the frame's, so the line's own closing mark goes; a comma inside it stays."""
    drill = build_affix_drill("ceb", _MAG_NAG, roots=["lakaw", "ampo"], lines=[("Naglakaw si Paul sa dalan.", english)])

    assert [step.text for step in drill.steps if step.kind == "prompt"][-1] == asked


def test_only_the_first_prompt_of_a_round_carries_the_question():
    """'Question then bare English' (the owner, 2026-10-12): once the narrator
    has asked, every later prompt of that round is the English alone, with its
    own punctuation. Each round opens with the question again: the modelled
    roots, the new roots once they are named, everything mixed, and the lines."""
    drill = build_affix_drill(
        "ceb",
        _MAG_NAG,
        roots=["lakaw", "ampo"],
        new_roots=["luto", "dala"],
        lines=[
            ("Naglakaw si Paul sa dalan.", "Paul walked on the road."),
            ("Malipayon ka, Paul?", "Are you happy, Paul?"),
        ],
    )

    prompts = [step.text for step in drill.steps if step.kind == "prompt"]
    asked = [i for i, text in enumerate(prompts) if text.startswith("How do you say")]
    assert asked == [0, 4, 8, 16]
    bare = [text for i, text in enumerate(prompts) if i not in asked]
    assert set(bare[:-1]) <= {
        f"{tense}."
        for tense in (
            "is walking",
            "will walk",
            "is praying",
            "will pray",
            "is cooking",
            "will cook",
            "is bringing",
            "will bring",
        )
    }
    assert prompts[-2:] == ['How do you say "Paul walked on the road"?', "Are you happy, Paul?"]


def test_a_line_is_never_asked_before_every_line_has_been_taught():
    """Teaching a line and asking for it straight after would be an echo, the
    thing the done-first order of the forms already avoids."""
    lines = [("Naglakaw si Paul sa dalan.", "Paul walked on the road."), ("Mag-ampo ta.", "Let's pray.")]
    drill = build_affix_drill("ceb", _MAG_NAG, roots=["lakaw", "ampo"], lines=lines)

    first_ask = drill.steps.index(DrillStep("prompt", 'How do you say "Paul walked on the road"?', "en"))
    assert DrillStep("model", "Mag-ampo ta.", "ceb") in drill.steps[:first_ask]


def test_a_drill_with_no_lines_says_nothing_about_them():
    drill = build_affix_drill("ceb", _MAG_NAG, roots=["lakaw", "ampo"])

    assert [s.text for s in drill.steps].count("Listen and repeat.") == 1
    assert drill.steps[-1].kind == "answer"
    assert drill.steps[-1].text in _forms(_MAG_NAG, "lakaw", "ampo")


def test_every_form_is_asked_twice_when_there_are_no_new_roots():
    """Your turn, then everything once more: with no new roots that is the same
    forms again, a second time through in another order."""
    drill = build_affix_drill("ceb", _MAG_NAG, roots=["lakaw", "ampo"])

    yours, everything = _rounds(drill)
    assert sorted(yours) == sorted(everything) == sorted(_forms(_MAG_NAG, "lakaw", "ampo"))


def test_the_note_is_said_once_right_after_the_first_root_is_modelled():
    """'The first' and 'the second' are then the two forms just heard, and the
    second root is met with the note in mind. Nothing else in the script moves."""
    note = ("The first is for what has not begun.", "The second is for what is going on.")
    drill = build_affix_drill("ceb", _MAG_NAG, roots=["lakaw", "ampo"], note=note)

    said = drill.steps.index(DrillStep("model", note[0], "en"))
    assert drill.steps[said - 1] == _built("naglakaw")[-1]
    assert drill.steps[said : said + 3] == [
        DrillStep("model", note[0], "en"),
        DrillStep("model", note[1], "en"),
        DrillStep("model", "pray", "en"),
    ]
    plain = build_affix_drill("ceb", _MAG_NAG, roots=["lakaw", "ampo"])
    assert [step for step in drill.steps if step.text not in note] == plain.steps
    assert not [step for step in plain.steps if step.text in note]


def test_roots_that_cannot_be_drilled_are_dropped_and_named():
    drill = build_affix_drill("ceb", _MAG_NAG, roots=["lakaw", "andam", "ampo", "tulog"])
    assert drill.dropped == [
        ("andam", "no checked English for this root in this pattern"),
        ("tulog", "native news does not use every form"),
    ]
    assert {s.text for s in drill.steps if s.kind == "answer"} == {"naglakaw", "maglakaw", "nag-ampo", "mag-ampo"}


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
