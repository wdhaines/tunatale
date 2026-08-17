"""Tests for ``app.cards.example_sentences``."""

import pytest

from app.cards.example_sentences import ExampleSentence, parse_example_sentences

# ── Must-parse ────────────────────────────────────────────────────────────

MUST_PARSE: list[tuple[str, list[ExampleSentence]]] = [
    (
        "Hun er lærer (<i>She is a teacher</i>)",
        [ExampleSentence(l2="Hun er lærer", gloss="She is a teacher")],
    ),
    (
        "Det er ganske kaldt (<i>It is quite cold</i>).",
        [ExampleSentence(l2="Det er ganske kaldt", gloss="It is quite cold")],
    ),
    (
        "Jeg vil kjøpe aksjer i selskapet (<i>I want to buy shares in the company</i>).",
        [
            ExampleSentence(
                l2="Jeg vil kjøpe aksjer i selskapet",
                gloss="I want to buy shares in the company",
            )
        ],
    ),
    (
        "Grenda vår har bare ti hus (<i>Our hamlet only has ten houses</i>).",
        [
            ExampleSentence(
                l2="Grenda vår har bare ti hus",
                gloss="Our hamlet only has ten houses",
            )
        ],
    ),
    (
        "Det er stor stas å feire bursdag (<i>It's great fun to celebrate a birthday</i>).",
        [
            ExampleSentence(
                l2="Det er stor stas å feire bursdag",
                gloss="It's great fun to celebrate a birthday",
            )
        ],
    ),
    (
        "Mobiltelefon (<i>Mobile phone</i>).",
        [ExampleSentence(l2="Mobiltelefon", gloss="Mobile phone")],
    ),
]


@pytest.mark.parametrize("raw, expected", MUST_PARSE, ids=[c[0][:40] for c in MUST_PARSE])
def test_must_parse(raw: str, expected: list[ExampleSentence]) -> None:
    assert parse_example_sentences(raw) == expected


# ── Two-segment case ──────────────────────────────────────────────────────


def test_two_segments_order_preserved() -> None:
    raw = (
        "Hvilket tegn er det? (<i>What character is that?</i>).<br>Det er et tegn på regn (<i>It's a sign of rain</i>)."
    )
    result = parse_example_sentences(raw)
    assert len(result) == 2
    assert result[0] == ExampleSentence(
        l2="Hvilket tegn er det?",
        gloss="What character is that?",
    )
    assert result[1] == ExampleSentence(
        l2="Det er et tegn på regn",
        gloss="It's a sign of rain",
    )


# ── Must DECLINE ──────────────────────────────────────────────────────────

MUST_DECLINE: list[tuple[str, str]] = [
    ("Jeg har éi nese, og to øyne (<i>I have one nose, and two eyes", "truncated gloss"),
    ("Kommer du òg? (<i>Are you coming too?", "truncated gloss"),
    ("Det tror du vel ikke (<i>You surely don't believe that", "truncated gloss"),
    ("Sette kursen hjem", "no gloss at all"),
    ("Set course for home", "no gloss at all"),
    ("Jeg delte kaken i to", "no gloss at all"),
    (
        "En pluss to er tre (<i>One plus two is three</i>)</i>",
        "stray trailing </i>",
    ),
    (
        "Få et klart bilde av hva som skjer (<i>Get a clear (<i>mental</i>) picture of what is happening</i>)",
        "nested italics",
    ),
    (
        "Han slo ballen (<i>He hit the ball</i>) Slå på lyset (<i>Turn on the light</i>)",
        "two glosses in one segment",
    ),
    (
        "Oi, det var uventet! (<i>Oh, that was unexpected!</i>). Oi, jeg sølte! (<i>Oops, I spilled!</i>).",
        "two glosses in one segment",
    ),
]


@pytest.mark.parametrize("raw, _why", MUST_DECLINE, ids=[c[1] for c in MUST_DECLINE])
def test_must_decline(raw: str, _why: str) -> None:
    assert parse_example_sentences(raw) == []


# ── Whole-field cases ─────────────────────────────────────────────────────


def test_empty_string() -> None:
    assert parse_example_sentences("") == []


def test_whitespace_only() -> None:
    assert parse_example_sentences("   ") == []


def test_all_segments_decline() -> None:
    raw = "Sette kursen hjem<br>Set course for home"
    assert parse_example_sentences(raw) == []


def test_newline_separator() -> None:
    raw = (
        "drikk og dans og sådant mer (<i>drink and dance and such things</i>)\n"
        "de er musikere, og som sådanne verdsettes de høyt (<i>they are musicians, and as such, they are highly valued</i>)"
    )
    result = parse_example_sentences(raw)
    assert len(result) == 2
    assert result[0].l2 == "drikk og dans og sådant mer"
    assert result[0].gloss == "drink and dance and such things"
    assert result[1].l2 == "de er musikere, og som sådanne verdsettes de høyt"
    assert result[1].gloss == "they are musicians, and as such, they are highly valued"


def test_declines_italics_not_wrapped_in_parentheses() -> None:
    """Balanced `<i>` tags are not enough — the gloss must be the parenthesised tail.

    Covers the branch where the tag counts pass but the segment shape does not.
    A note that italicises a word mid-sentence has one `<i>` and one `</i>`, so the
    counting guards let it through; only the pattern rejects it.
    """
    assert parse_example_sentences("Kommer du <i>hit</i> i morgen?") == []


def test_declines_a_stray_angle_bracket_in_the_sentence() -> None:
    """A well-formed gloss does not license markup in the sentence half.

    `2 < 3 (<i>…</i>)` has exactly one balanced tag pair and matches the pattern,
    so the only thing standing between it and a card is the residual-markup
    check. Emitting `2 < 3` would put a stray bracket on the learner's card.
    """
    assert parse_example_sentences("2 < 3 (<i>two is less than three</i>)") == []


def test_declines_markup_leaking_into_the_gloss() -> None:
    """The balanced-tag guard is what stops a `</i>` ending up INSIDE the gloss.

    `x (<i>a</i>b</i>)` has one `<i>` and two `</i>`, so the exactly-one-gloss
    guard passes it, and the gloss group is greedy — the pattern happily matches
    with `gloss="a</i>b"`. Only the balanced-count check refuses it.

    This case exists because a sabotage drill on 2026-08-17 removed that guard and
    all 23 other tests stayed green, which would have argued the guard was
    redundant. It is not; the suite was simply blind to its input class. The
    guard's own drill now reddens here.
    """
    assert parse_example_sentences("x (<i>a</i>b</i>)") == []
