"""Counting pictures for number words (tunatale-elrj).

A number word routed to a cloze production card is the complaint this module
answers: ``jeg har ___ barn`` admits every number, so the card teaches nothing
and marks a right answer wrong. Numbers are the one word class with a perfect,
unambiguous picture — the quantity itself — and the picture is *rendered*, not
searched for, because a fetched photo's count cannot be verified.

That asymmetry is the reason these tests can exist at all. The oracle for
"the picture shows exactly N objects" is a count of the emitted elements, which
is true by construction; for a Pixabay photo there is no assertable oracle.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET

import pytest

from app.cards.number_image import (
    MAX_RENDERABLE,
    ONES_PER_ROD,
    number_value,
    render_count_svg,
)
from app.languages import get_numbers_path

LANGS = ("no", "sl")


def _config(code: str) -> dict:
    path = get_numbers_path(code)
    assert path is not None, f"{code} registers no numbers.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _dots(svg: bytes) -> int:
    """Every dot drawn, whether loose or inside a rod. This is the oracle."""
    return len(re.findall(rb"<circle", svg))


class TestNumberValue:
    """The lookup that decides a word is a picturable quantity."""

    @pytest.mark.parametrize(("code", "word", "expected"), [("no", "fem", 5), ("sl", "pet", 5)])
    def test_reads_a_cardinal_from_the_languages_own_vocabulary(self, code, word, expected) -> None:
        assert number_value(word, code) == expected

    def test_is_case_insensitive_and_ignores_surrounding_space(self) -> None:
        """A headword arrives from the deck, which capitalises inconsistently."""
        assert number_value("  Fem ", "no") == 5

    def test_an_ordinary_word_is_not_a_number(self) -> None:
        assert number_value("katt", "no") is None

    def test_a_language_with_no_vocabulary_file_has_no_numbers(self) -> None:
        """Capability-driven, exactly like function words: absence is not an error."""
        assert number_value("five", "en") is None

    def test_a_number_from_another_language_does_not_resolve(self) -> None:
        """The vocabularies are per-language, never a merged union."""
        assert number_value("pet", "no") is None

    def test_an_excluded_word_is_refused_even_though_it_has_a_value(self) -> None:
        """Norwegian `en` is 'one' AND 'a/an'; a picture cannot tell them apart."""
        assert _config("no")["values"]["en"] == 1
        assert number_value("en", "no") is None

    def test_zero_is_refused(self) -> None:
        """An empty frame is indistinguishable from a render that failed."""
        assert _config("no")["values"]["null"] == 0
        assert number_value("null", "no") is None

    def test_a_value_above_the_renderable_range_is_refused(self) -> None:
        """1000 dots is a texture, not a countable set. Those words stay clozes."""
        assert _config("no")["values"]["tusen"] > MAX_RENDERABLE
        assert number_value("tusen", "no") is None

    def test_the_largest_renderable_deck_word_is_accepted(self) -> None:
        assert number_value("hundre", "no") == MAX_RENDERABLE


class TestVocabularyFiles:
    """Properties of the data files themselves, enforced rather than documented."""

    @pytest.mark.parametrize("code", LANGS)
    def test_no_two_renderable_words_name_the_same_quantity(self, code) -> None:
        """The picture is keyed on the value alone.

        Two surfaces sharing a quantity would share one rendered file, which is
        the duplicate-image ambiguity the pre-stage's digest guard exists to
        prevent — reached by a different road. `sju` is absent from Norwegian for
        exactly this reason.

        The requirement is on what SURVIVES `exclude`, not on the raw map: an
        excluded word never renders, so it cannot collide with anything. That is
        what lets `en` and `ene` both record the quantity they honestly name.
        """
        cfg = _config(code)
        renderable = [v for w, v in cfg["values"].items() if w not in set(cfg["exclude"])]
        collisions = {v for v in renderable if renderable.count(v) > 1}
        assert collisions == set(), f"{code}: more than one renderable word for {sorted(collisions)}"

    @pytest.mark.parametrize("code", LANGS)
    def test_every_excluded_word_is_one_the_file_knows_a_value_for(self, code) -> None:
        """An exclusion for a word with no value is dead config that reads as live."""
        cfg = _config(code)
        assert set(cfg["exclude"]) <= set(cfg["values"])

    @pytest.mark.parametrize("code", LANGS)
    def test_every_word_is_stored_casefolded(self, code) -> None:
        """Lookup casefolds the query; a capitalised key would be unreachable."""
        cfg = _config(code)
        assert [w for w in (*cfg["values"], *cfg["exclude"]) if w != w.casefold()] == []


class TestRender:
    """The picture. Its count is true by construction, which is the whole point."""

    @pytest.mark.parametrize("n", range(1, MAX_RENDERABLE + 1))
    def test_draws_exactly_that_many_objects(self, n) -> None:
        """The oracle, over the entire renderable range with no gaps."""
        assert _dots(render_count_svg(n)) == n

    @pytest.mark.parametrize(("n", "rods"), [(1, 0), (9, 0), (10, 1), (13, 1), (20, 2), (99, 9), (100, 10)])
    def test_groups_completed_tens_into_rods(self, n, rods) -> None:
        """Place value: thirteen is one rod and three dots, not thirteen marks."""
        svg = render_count_svg(n)
        # One <rect> is the background; every other is a rod frame.
        assert len(re.findall(rb"<rect", svg)) == rods + 1

    def test_the_ones_left_over_are_the_remainder(self) -> None:
        """47 is four rods and seven loose dots — 4*10 + 7, drawn as it reads."""
        svg = render_count_svg(47)
        rods = len(re.findall(rb"<rect", svg)) - 1
        assert (rods, _dots(svg) - rods * ONES_PER_ROD) == (4, 7)

    def test_is_well_formed_xml(self) -> None:
        """It is written into an <img src>; a malformed file renders as nothing."""
        root = ET.fromstring(render_count_svg(13).decode("utf-8"))
        assert root.tag == "{http://www.w3.org/2000/svg}svg"

    def test_paints_its_own_background(self) -> None:
        """An <img> cannot inherit currentColor.

        A transparent render with dark dots disappears in Anki's night mode, so
        the file carries its own opaque ground and reads identically in both.
        """
        svg = render_count_svg(5).decode("utf-8")
        first_rect = re.search(r"<rect[^>]*>", svg).group(0)
        assert 'fill="#' in first_rect
        assert "viewBox" in svg

    def test_is_deterministic(self) -> None:
        """The filename is the content hash; a jittered render would re-stage forever."""
        assert render_count_svg(23) == render_count_svg(23)

    def test_two_quantities_never_render_the_same_bytes(self) -> None:
        """Identical bytes would point two cards at one picture."""
        rendered = {render_count_svg(n) for n in range(1, MAX_RENDERABLE + 1)}
        assert len(rendered) == MAX_RENDERABLE

    @pytest.mark.parametrize("n", range(1, MAX_RENDERABLE + 1))
    def test_stays_close_enough_to_square_to_survive_a_card(self, n) -> None:
        """A card scales the picture to fit its width, so a very wide one shrinks.

        Ten rods in a single row is 940x198; the drill card caps an image at
        240px wide on small screens, which would draw those dots at about two
        pixels and make the one thing the card asks for impossible. Rods wrap
        instead. The bound is what keeps the encoding honest at the top of the
        range, so it is asserted rather than eyeballed.
        """
        svg = render_count_svg(n).decode("utf-8")
        w, h = (int(v) for v in re.search(r'viewBox="0 0 (\d+) (\d+)"', svg).groups())
        assert w / h <= 3.0, f"{n} renders {w}x{h}, too wide to read when scaled down"

    def test_wraps_rods_into_rows_rather_than_one_long_line(self) -> None:
        """The specific shape the bound above exists to produce."""
        svg = render_count_svg(100).decode("utf-8")
        rod_ys = {m for m in re.findall(r'<rect x="\d+" y="(\d+)"', svg)}
        assert len(rod_ys) == 2, "ten rods should sit on two rows of five"

    @pytest.mark.parametrize("n", [0, -1, MAX_RENDERABLE + 1])
    def test_refuses_a_quantity_outside_the_renderable_range(self, n) -> None:
        """`number_value` already filters these; the renderer will not guess."""
        with pytest.raises(ValueError, match="renderable"):
            render_count_svg(n)
