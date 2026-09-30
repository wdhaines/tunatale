"""Which spatial words get a drawn picture — and the data files that decide it.

``app.cards.spatial_picture`` maps a language's spatial words (``under``,
``sulod``, ``gor``) to a language-neutral concept, and draws it with
``app.cards.spatial_scenes`` (which has its own suite). This one pins the
ROUTING, the gloss guard that keeps a homograph off the wrong picture, and the
function-word veto that makes these words picture cards instead of clozes
(tunatale-hvj0).
"""

from __future__ import annotations

import hashlib
import json

import pytest

from app.cards.drawn_picture import drawn_picture
from app.cards.number_picture import number_picture
from app.cards.spatial_picture import gloss_matches, is_spatial_word, load_spatial_words, spatial_picture
from app.cards.spatial_scenes import SPATIAL_CONCEPTS, render_spatial_svg
from app.languages import get_function_words_path, get_spatial_path, known_language_codes
from app.srs.function_words import is_function_word

LANGS = tuple(sorted(c for c in known_language_codes() if get_spatial_path(c) is not None))


def _words(code: str) -> dict[str, str]:
    return json.loads(get_spatial_path(code).read_text(encoding="utf-8"))["words"]


class TestDataFiles:
    def test_every_language_that_teaches_vocabulary_registers_a_file(self) -> None:
        assert set(LANGS) == {"ceb", "no", "sl", "tl"}

    @pytest.mark.parametrize("code", LANGS)
    def test_every_word_names_a_concept_the_renderer_can_draw(self, code) -> None:
        unknown = {w: c for w, c in _words(code).items() if c not in SPATIAL_CONCEPTS}
        assert unknown == {}

    @pytest.mark.parametrize("code", LANGS)
    def test_words_are_lowercase_single_tokens(self, code) -> None:
        bad = [w for w in _words(code) if w != w.casefold() or not w.isalpha()]
        assert bad == []

    @pytest.mark.parametrize("code", LANGS)
    def test_no_spatial_word_is_also_listed_as_a_function_word(self, code) -> None:
        # The veto makes an `include` entry for a spatial word dead data. Dead
        # data is where the next reader believes the wrong file, so it is refused.
        path = get_function_words_path(code)
        include = {w.casefold() for w in json.loads(path.read_text(encoding="utf-8")).get("include", [])}
        assert include & set(_words(code)) == set()

    @pytest.mark.parametrize("code", LANGS)
    def test_no_spatial_word_is_also_a_number(self, code) -> None:
        assert [w for w in _words(code) if number_picture(w, code) is not None] == []

    #: Pairs the user decided MAY share a picture: true synonyms, where a second
    #: drawing would invent a distinction the language does not make (Slovene
    #: poleg / zraven, both "beside"; "we can ignore the synonyms", 2026-09-29).
    _SYNONYMS_MAY_SHARE: dict[str, dict[str, list[str]]] = {"sl": {"beside": ["poleg", "zraven"]}}

    @pytest.mark.parametrize("code", ["no", "ceb", "sl", "tl"])
    def test_no_two_words_share_a_picture(self, code) -> None:
        """A picture card must not be ambiguous: distinct words, distinct drawings.

        The user found inn and innenfor on one picture (2026-09-29); six groups
        of Norwegian words, two Cebuano pairs (kilid/tupad, taliwala/tunga) and
        two Slovene ones (nad/zgoraj, pod/spodaj) shared one then. The only
        sharing left is the user's recorded synonym exception above.
        """
        words = _words(code)
        shared = {c: sorted(w for w, k in words.items() if k == c) for c in set(words.values())}
        assert {c: ws for c, ws in shared.items() if len(ws) > 1} == self._SYNONYMS_MAY_SHARE.get(code, {})

    def test_a_language_without_a_file_has_no_spatial_words(self) -> None:
        assert load_spatial_words("en") == {}
        assert spatial_picture("under", "en", "under") is None


class TestGlossGuard:
    @pytest.mark.parametrize(
        ("concept", "gloss"),
        [
            ("under", "below; down; low"),
            ("above", "top; above"),
            ("above", "high"),
            ("right", "right (side)"),
            ("right", "turn right"),
            ("behind", "behind, at the back"),
            ("beside", "side"),
            ("in_front", "in front of"),
            ("in_front", "front"),
            ("in", "inside, within"),
            ("out", "outside"),
            ("between", "between"),
            ("up", "UP"),
            # The eight added 2026-09-29, on the glosses their Norwegian words carry.
            ("into", "in, into"),
            ("within", "inside, within"),
            ("outside_of", "outside"),
            ("outdoors", "outside; outdoors"),
            ("up_there", "up (there)"),
            ("down_there", "down (there)"),
            ("further_down", "below"),
            ("further_up", "above"),
            ("side", "side"),
            ("middle", "middle, centre"),
        ],
    )
    def test_a_gloss_that_names_the_relation_matches(self, concept, gloss) -> None:
        assert gloss_matches(concept, gloss)

    @pytest.mark.parametrize(
        ("concept", "gloss"),
        [
            # The live homographs this guard exists for (Cebuano, 2026-09-28):
            ("left", "none"),  # wala
            ("right", "believe"),  # tuo
            ("between", "half"),  # tunga
            ("left", ""),
            ("up", "upset"),  # a word containing the keyword is not the keyword
            ("on", "only"),
            ("in", "inch"),
        ],
    )
    def test_a_gloss_that_does_not_name_the_relation_refuses(self, concept, gloss) -> None:
        assert not gloss_matches(concept, gloss)

    def test_every_concept_has_keywords(self) -> None:
        assert all(gloss_matches(c, c.replace("_", " ")) for c in SPATIAL_CONCEPTS)


class TestDispatch:
    @pytest.mark.parametrize(
        ("word", "code", "gloss", "concept"),
        [
            ("under", "no", "under", "under"),
            ("opp", "no", "up", "up"),
            ("inni", "no", "inside", "in"),
            ("foran", "no", "in front of", "in_front"),
            ("sulod", "ceb", "inside", "in"),
            ("ubos", "ceb", "below; down; low", "under"),
            ("likod", "ceb", "back (behind)", "behind"),
            ("gor", "sl", "up", "up"),
            ("zunaj", "sl", "outside", "out"),
            ("kaliwa", "tl", "to the left", "left"),
            ("Under", "no", "under", "under"),
            # The user's report (2026-09-29): inn and innenfor were one picture.
            ("inn", "no", "in", "into"),
            ("innenfor", "no", "inside, within", "within"),
            ("utenfor", "no", "outside", "outside_of"),
            ("ute", "no", "outside", "outdoors"),
            ("ut", "no", "out", "out"),
            ("oppe", "no", "up", "up_there"),
            ("nede", "no", "down", "down_there"),
            ("nedenfor", "no", "below", "further_down"),
            ("ovenfor", "no", "above", "further_up"),
            # Cebuano (2026-09-29): kilid shared tupad's "beside", tunga shared
            # taliwala's "between".
            ("kilid", "ceb", "side", "side"),
            ("tunga", "ceb", "middle", "middle"),
            ("tupad", "ceb", "beside", "beside"),
            ("taliwala", "ceb", "between", "between"),
            # Slovene (2026-09-29): the Norwegian going/being split again — nad
            # "above (something)" vs zgoraj "up there", pod vs spodaj.
            ("zgoraj", "sl", "up; upstairs", "up_there"),
            ("spodaj", "sl", "down; downstairs", "down_there"),
            ("nad", "sl", "above", "above"),
            ("pod", "sl", "under", "under"),
        ],
    )
    def test_a_spatial_word_is_drawn_as_its_concept(self, word, code, gloss, concept) -> None:
        picture = spatial_picture(word, code, gloss)
        assert picture is not None
        svg = render_spatial_svg(concept)
        assert picture.svg == svg
        assert picture.filename == f"spatial_{concept}_{hashlib.sha256(svg).hexdigest()[:8]}.svg"

    @pytest.mark.parametrize(
        ("word", "code", "gloss"),
        [
            ("wala", "ceb", "none"),  # a homograph of "left", glossed as its other sense
            ("tunga", "ceb", "half"),
            ("under", "no", ""),  # no gloss: cannot confirm the sense, so no picture
            ("hus", "no", "house"),  # not a spatial word at all
            ("fem", "no", "five"),
        ],
    )
    def test_anything_else_is_left_on_its_route(self, word, code, gloss) -> None:
        assert spatial_picture(word, code, gloss) is None


class TestDrawnPicture:
    def test_a_number_is_drawn_as_a_number_whatever_the_gloss(self) -> None:
        assert drawn_picture("fem", "no", "") == number_picture("fem", "no")
        assert drawn_picture("singko", "ceb", "five") == number_picture("singko", "ceb")

    def test_a_spatial_word_is_drawn_as_its_concept(self) -> None:
        assert drawn_picture("under", "no", "under") == spatial_picture("under", "no", "under")

    def test_an_ordinary_word_has_no_drawing(self) -> None:
        assert drawn_picture("hus", "no", "house") is None


class TestFunctionWordVeto:
    """A spatial word is a picture card, never a cloze (the user, 2026-09-28)."""

    @pytest.mark.parametrize(("word", "code"), [("under", "no"), ("over", "no"), ("mellom", "no"), ("pod", "sl")])
    def test_a_spatial_word_is_not_a_function_word(self, word, code) -> None:
        assert is_spatial_word(word, code)
        assert not is_function_word(word, code)

    @pytest.mark.parametrize(("word", "code"), [("under", "no"), ("nad", "sl")])
    def test_the_veto_outranks_a_closed_class_pos_tag(self, word, code) -> None:
        # The mint's fork passes the deck's UPOS; ADP is in every `pos` set.
        assert not is_function_word(word, code, upos="ADP")

    @pytest.mark.parametrize(("word", "code"), [("på", "no"), ("i", "no"), ("na", "sl"), ("sa", "ceb")])
    def test_the_polysemous_prepositions_stay_clozes(self, word, code) -> None:
        # Deliberately NOT mapped: `på mandag`, `i dag`, `na pošti` are not
        # places, and the cloze drills the preposition CHOICE, which is the
        # difficulty. A picture of "on" would teach the wrong half.
        assert not is_spatial_word(word, code)
        assert is_function_word(word, code)
