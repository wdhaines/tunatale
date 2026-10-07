"""Which drawn picture a number word gets — and the data files that decide it.

``app.cards.number_picture`` is the single dispatcher every image path asks
first (tunatale-w4m7.10). The renderers have their own suites
(``test_number_image.py``, ``test_number_scenes.py``); this one pins the ROUTING:
a native cardinal is a heap, a clock word is a clock, a money word is money,
and everything else is left on the route it was already on.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from app.cards.number_image import render_count_svg
from app.cards.number_picture import number_picture
from app.cards.number_scenes import break_into_denominations, render_clock_svg, render_money_svg
from app.languages import get_numbers_path, known_language_codes

LANGS = tuple(sorted(c for c in known_language_codes() if get_numbers_path(c) is not None))

#: Languages whose file declares a `clock` or `money` context. Derived, so a new
#: language with Spanish-derived numbers is checked without editing this suite.
SCENE_LANGS = tuple(c for c in LANGS if {"clock", "money"} & json.loads(get_numbers_path(c).read_text()).keys())


def _config(code: str) -> dict:
    return json.loads(get_numbers_path(code).read_text(encoding="utf-8"))


class TestDispatch:
    @pytest.mark.parametrize(
        ("word", "code", "svg"),
        [
            ("fem", "no", render_count_svg(5)),
            ("lima", "ceb", render_count_svg(5)),
            ("lima", "tl", render_count_svg(5)),
            ("singko", "ceb", render_clock_svg(5)),
            ("dose", "tl", render_clock_svg(12)),
            ("kinse", "ceb", render_money_svg(15, symbol="₱", coins=(1, 5, 10, 20), bills=(50, 100, 200, 500, 1000))),
            ("mil", "tl", render_money_svg(1000, symbol="₱", coins=(1, 5, 10, 20), bills=(50, 100, 200, 500, 1000))),
        ],
    )
    def test_each_word_gets_the_picture_of_its_context(self, word, code, svg) -> None:
        picture = number_picture(word, code)
        assert picture is not None
        assert picture.svg == svg

    @pytest.mark.parametrize(
        ("word", "code", "stem"),
        [("fem", "no", "count_005"), ("singko", "ceb", "clock_05"), ("kinse", "ceb", "money_0015")],
    )
    def test_the_filename_names_the_picture_and_hashes_its_bytes(self, word, code, stem) -> None:
        """Content-addressed, so a changed drawing can never overwrite a file in use."""
        picture = number_picture(word, code)
        digest = hashlib.sha256(picture.svg).hexdigest()[:8]
        assert picture.filename == f"{stem}_{digest}.svg"

    def test_lookup_is_case_insensitive_and_ignores_surrounding_space(self) -> None:
        assert number_picture("  Singko ", "ceb") == number_picture("singko", "ceb")

    @pytest.mark.parametrize(
        ("word", "code"),
        [
            ("dyis", "ceb"),  # spelling doublet of diyes, which carries the clock
            ("sero", "ceb"),  # zero: nothing to draw in any context
            ("en", "no"),
            ("båt", "no"),
            ("singko", "no"),  # a Cebuano word means nothing in Norwegian
        ],
    )
    def test_everything_else_keeps_its_route(self, word, code) -> None:
        assert number_picture(word, code) is None

    @pytest.mark.parametrize(("word", "code"), [("usa", "ceb"), ("isa", "tl")])
    def test_one_is_a_number_in_cebuano_and_tagalog(self, word, code) -> None:
        """Both were excluded as "also the indefinite article", by analogy with
        Norwegian `en`. The user (2026-10-06): "Cebuano and Tagalog don't really
        do indefinite articles that way. I think treating them primarily as a
        number makes sense." `en` stays excluded: it IS Norwegian's article."""
        picture = number_picture(word, code)
        assert picture is not None
        assert picture.filename.startswith("count_001_")
        assert number_picture("en", "no") is None


class TestSceneFiles:
    """The `clock` / `money` keys, enforced rather than documented."""

    def test_the_philippine_languages_declare_both_scenes(self) -> None:
        """The control for the derived list: were it empty, every test below would pass vacuously."""
        assert set(SCENE_LANGS) >= {"ceb", "tl"}

    @pytest.mark.parametrize("code", SCENE_LANGS)
    def test_every_scene_word_has_a_value_and_is_excluded_from_counting(self, code) -> None:
        """Excluded, because a word drawn as a clock must not ALSO be drawn as a heap."""
        cfg = _config(code)
        words = set(cfg["clock"]) | set(cfg["money"]["words"])
        assert words <= set(cfg["values"])
        assert words <= set(cfg["exclude"])

    @pytest.mark.parametrize("code", SCENE_LANGS)
    def test_no_word_is_both_a_clock_and_money(self, code) -> None:
        cfg = _config(code)
        assert set(cfg["clock"]) & set(cfg["money"]["words"]) == set()

    @pytest.mark.parametrize("code", SCENE_LANGS)
    @pytest.mark.parametrize("scene", ["clock", "money"])
    def test_no_two_words_draw_the_same_scene(self, code, scene) -> None:
        """Two words sharing one picture would make a production card with two right answers."""
        cfg = _config(code)
        words = cfg["clock"] if scene == "clock" else cfg["money"]["words"]
        values = [cfg["values"][w] for w in words]
        assert len(values) == len(set(values)), f"{code} {scene}: duplicate values"

    @pytest.mark.parametrize("code", SCENE_LANGS)
    def test_every_clock_word_is_an_hour(self, code) -> None:
        cfg = _config(code)
        assert all(1 <= cfg["values"][w] <= 12 for w in cfg["clock"])

    @pytest.mark.parametrize("code", SCENE_LANGS)
    def test_every_money_word_can_be_paid_exactly(self, code) -> None:
        cfg = _config(code)
        money = cfg["money"]
        for word in money["words"]:
            value = cfg["values"][word]
            assert sum(break_into_denominations(value, [*money["coins"], *money["bills"]])) == value

    @pytest.mark.parametrize("code", SCENE_LANGS)
    def test_every_scene_word_actually_draws(self, code) -> None:
        """End to end through the loader: the file's words reach the dispatcher."""
        cfg = _config(code)
        for word in [*cfg["clock"], *cfg["money"]["words"]]:
            assert number_picture(word, code) is not None, f"{code}: {word} did not draw"
