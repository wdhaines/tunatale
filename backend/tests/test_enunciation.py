"""An Enunciated line, cut into the words a voice pauses between (tunatale-tyfk).

The cut is made at RENDER time, from the natural line, so a stored lesson needs
only a re-render to be said the new way. Lessons stored before that carry the
old notation — ``" ... "`` between words, ``", "`` inside a split word — and are
read here too, because a stored section is a snapshot nobody rewrites.
"""

from __future__ import annotations

import pytest

from app.audio.enunciation import ENUNCIATED_SECTIONS, EnunciatedLine, line_phonemes, plan_line
from app.models.lesson import SectionType


def _comma_split(word: str) -> str:
    """A slow-word function in the registry's shape: ", " where the word is cut."""
    return {"flyplassen.": "fly, plassen.", "Nakikiramay": "Nakiki, ramay"}.get(word, word)


def test_the_three_enunciated_sections_and_no_other():
    assert {
        SectionType.SLOW_SPEED,
        SectionType.SLOW_TRANSLATED,
        SectionType.SLOW_EN_TRANSLATED,
    } == ENUNCIATED_SECTIONS


def test_a_natural_line_is_cut_at_its_spaces_and_keeps_its_punctuation():
    line = plan_line("Salamat, hijo. Ikaw ba?")

    assert line == EnunciatedLine(
        text="Salamat, hijo. Ikaw ba?",
        words=(("Salamat,",), ("hijo.",), ("Ikaw",), ("ba?",)),
    )


def test_a_word_the_language_splits_becomes_parts_and_the_text_stays_natural():
    """The split is how the word is SAID; the line a caption or an
    instruction-led voice is given is still the line as written."""
    line = plan_line("Magandang gabi po. Nakikiramay po ako.", _comma_split)

    assert line.text == "Magandang gabi po. Nakikiramay po ako."
    assert line.words == (("Magandang",), ("gabi",), ("po.",), ("Nakiki", "ramay"), ("po",), ("ako.",))


def test_a_stored_line_in_the_old_notation_reads_the_same_way():
    """`` ... `` was a word boundary and ``, `` inside a word was its split."""
    line = plan_line("mappen ... ble ... jo ... over, ført ... til ... en ... annen ... avdeling.")

    assert line.words == (
        ("mappen",),
        ("ble",),
        ("jo",),
        ("over", "ført"),
        ("til",),
        ("en",),
        ("annen",),
        ("avdeling.",),
    )
    # No " ... " and no ", " reaches a provider: neither was ever speech.
    assert line.text == "mappen ble jo overført til en annen avdeling."


def test_a_natural_comma_in_a_stored_line_is_not_a_split():
    """ "Hei," ends its word; only a comma INSIDE one stored word is a cut."""
    line = plan_line("Hei, ... dette ... er ... Anders.")

    assert line.words == (("Hei,",), ("dette",), ("er",), ("Anders.",))
    assert line.text == "Hei, dette er Anders."


def test_a_stored_word_that_was_never_split_is_still_offered_to_the_language():
    """Tagalog had no split rule when its lessons were stored, so their long
    words arrive whole; a re-render must not need a rebuild to cut them."""
    line = plan_line("Nakikiramay ... po ... ako.", _comma_split)

    assert line.words == (("Nakiki", "ramay"), ("po",), ("ako.",))


def test_a_stored_line_with_an_empty_word_skips_it():
    """Two separators in a row hold nothing between them, and nothing is not a
    word: it must not become a pause with silence on both sides of it."""
    line = plan_line("ja ...  ... takk")

    assert line.words == (("ja",), ("takk",))
    assert line.text == "ja takk"


def test_a_stored_split_is_not_split_again():
    calls: list[str] = []

    def spy(word: str) -> str:
        calls.append(word)
        return word

    plan_line("fly, plassen. ... er ... her", spy)

    assert calls == ["er", "her"]


def test_punctuation_standing_alone_joins_the_word_before_it():
    """A dash between words is not a word: it must not get a pause of its own."""
    line = plan_line("Ja – det er sant")

    assert line.words == (("Ja –",), ("det",), ("er",), ("sant",))
    assert line.text == "Ja – det er sant"


def test_punctuation_opening_a_line_joins_the_word_after_it():
    line = plan_line("– Ja takk")

    assert line.words == (("– Ja",), ("takk",))


@pytest.mark.parametrize("text", ["Opo.", "", "   ", "?"])
def test_a_line_with_nothing_to_separate_is_not_enunciated(text):
    """One unsplit word has no gap to pause in: it is said as the natural line
    is, from the very same clip."""
    assert plan_line(text) is None


def test_a_single_split_word_is_still_enunciated():
    line = plan_line("flyplassen.", _comma_split)

    assert line == EnunciatedLine(text="flyplassen.", words=(("fly", "plassen."),))


def test_a_slow_word_function_that_returns_nothing_leaves_the_word_whole():
    assert plan_line("ja takk", lambda word: "").words == (("ja",), ("takk",))


# ---------------------------------------------------------------------------
# line_phonemes — the per-word readings an instruction-led voice is given.
# ---------------------------------------------------------------------------


class _LinePlanner:
    def __init__(self, readings: dict[str, str]) -> None:
        self._readings = readings
        self.asked: list[str] = []

    def plan_line_word(self, word: str) -> str | None:
        self.asked.append(word)
        return self._readings.get(word.lower())


def test_every_word_of_the_line_is_read_and_keyed_bare_and_lowercase():
    planner = _LinePlanner({"liza": "lisa", "maayong": "maʔajoŋ", "buntag": "buntaɡ"})
    line = plan_line("Liza! Maayong buntag.")

    assert line_phonemes(line, planner) == {"liza": "lisa", "maayong": "maʔajoŋ", "buntag": "buntaɡ"}
    assert planner.asked == ["Liza", "Maayong", "buntag"]


def test_a_repeated_word_is_one_entry():
    """Unlike a drill phrase, a line is read by LOOKUP, so a repeat is fine."""
    planner = _LinePlanner({"ako": "ʔako", "si": "si"})

    assert line_phonemes(plan_line("Ako si ako"), planner) == {"ako": "ʔako", "si": "si"}


def test_one_unreadable_word_refuses_the_whole_line():
    planner = _LinePlanner({"ako": "ʔako"})

    assert line_phonemes(plan_line("Ako si 3"), planner) is None


def test_punctuation_standing_alone_is_not_asked_about():
    planner = _LinePlanner({"ja": "ja", "takk": "tak"})

    assert line_phonemes(plan_line("Ja – takk"), planner) == {"ja": "ja", "takk": "tak"}


def test_a_planner_that_cannot_read_a_line_word_is_never_asked_to():
    """A lexicon-backed planner has no such method; it is skipped, not called."""

    class _ChunkOnly:
        plan_chunk = None

    assert line_phonemes(plan_line("Ja takk"), _ChunkOnly()) is None
    assert line_phonemes(plan_line("Ja takk"), None) is None
