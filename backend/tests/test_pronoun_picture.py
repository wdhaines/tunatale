"""Which pronoun words get a drawn picture — and the data files that decide it.

``app.cards.pronoun_picture`` maps a language's personal pronouns (``jeg``,
``jaz``, ``ako``) to a language-neutral concept and draws it with
``app.cards.pronoun_scenes`` (which has its own suite, and where the geometry
lives). This one pins the ROUTING, the gloss guard that keeps a homograph off
the wrong picture, and the function-word veto that makes these words picture
cards instead of clozes (tunatale-3rxu).

**The veto is the expensive half here, and it is a data decision.** A pronoun is
``PRON`` in classla, so without a veto every one of these words would go down the
cloze route — which is exactly the wrong drill for ``hun``: the difficulty in a
personal pronoun is knowing *who it points at*, and a case-marker cloze teaches
the marker instead. It is also the half that cannot be undone per sense later:
the veto is asked about a bare token, so listing a homograph (``de``) takes
every sense of it off the picture route, which is why the gloss guard below
exists and why the tables are restricted to words whose main sense is the
pronoun.

**The possessives ride the same route, and are the reason the gloss guard has to
keep working.** A possessive is its nominative scene plus a bag in every lit
referent's hands (tunatale-uy38), so ``siya`` "he/she" and ``niya`` "his/her" are
two words pointing at two different pictures — and nothing but the card's English
gloss says which one a ``niya`` is. The enclitics (``ko``, ``mo``, ``ka``,
``akin``, ``iyo``) are the other half of that decision and stay out of the
tables; see :data:`_ENCLITICS`.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from app.cards.drawn_picture import drawn_picture
from app.cards.number_picture import number_picture
from app.cards.pronoun_picture import (
    _CONCEPT_GLOSSES,
    _spec,
    gloss_matches,
    is_pronoun_word,
    load_picture_only,
    load_pronoun_words,
    pronoun_picture,
)
from app.cards.pronoun_scenes import PRONOUN_CONCEPTS, render_pronoun_svg
from app.cards.spatial_picture import is_spatial_word, load_spatial_words
from app.languages import get_function_words_path, get_pronouns_path, known_language_codes
from app.srs.function_words import is_function_word

LANGS = tuple(sorted(c for c in known_language_codes() if get_pronouns_path(c) is not None))

#: Every word of every table, as the brief's ORACLE 3 lists it. Kept here as a
#: literal so that deleting a word from a data file is caught, and so that the
#: veto tests below can enumerate rather than sample.
_TABLES: dict[str, dict[str, str]] = {
    "no": {
        "jeg": "i",
        "du": "you_one",
        "han": "he",
        "hun": "she",
        "den": "it",
        "vi": "we_many",
        "dere": "you_many",
        "de": "they_many",
    },
    "sl": {
        "jaz": "i",
        "on": "he",
        "ona": "she",
        "ono": "it",
        "midva": "we_two",
        "midve": "we_two",
        "vidva": "you_two",
        "vidve": "you_two",
        "onadva": "they_two",
        "onidve": "they_two",
        "oni": "they_many",
        "one": "they_many",
    },
    "tl": {
        "ako": "i",
        "ikaw": "you_one",
        "siya": "third_one",
        "tayo": "we_incl",
        "kami": "we_excl",
        "kayo": "you_many",
        "sila": "they_many",
        "niya": "third_one_poss",
        "namin": "we_excl_poss",
        "natin": "we_incl_poss",
        "ninyo": "you_many_poss",
        "nila": "they_many_poss",
    },
    "ceb": {
        "ako": "i",
        "ikaw": "you_one",
        "siya": "third_one",
        "kita": "we_incl",
        "kami": "we_excl",
        "kamo": "you_many",
        "sila": "they_many",
        "nako": "i_poss",
        "nimo": "you_one_poss",
        "niya": "third_one_poss",
        "namo": "we_excl_poss",
        "nato": "we_incl_poss",
        "ninyo": "you_many_poss",
        "nila": "they_many_poss",
    },
}

#: The words deliberately left as clozes (ORACLE 3's "NOT listed"), and why.
#: The object forms are the case the cloze route is FOR, and ``mi``/``ti``/``vi``
#: are also dative or enclitic in Slovene — a token veto could not see that, so
#: they are simply not listed.
_CLOSES: tuple[tuple[str, str, str], ...] = (
    ("meg", "no", "me (object) — the cloze drills the case"),
    ("deg", "no", "you (object)"),
    ("ham", "no", "him (object)"),
    ("henne", "no", "her (object)"),
    ("oss", "no", "us (object)"),
    ("dem", "no", "them (object)"),
    ("det", "no", "dummy subject / article — polysemous, so every sense would be taken"),
    ("mi", "sl", "also dative/enclitic '(to) me'"),
    ("ti", "sl", "also dative/enclitic '(to) you'"),
    ("vi", "sl", "also dative/enclitic '(to) us'"),
)

#: The possessive ENCLITICS, which are not in the tables above and must stay
#: out of them. Each has a free form carrying the same meaning — ``nako`` "my",
#: ``nimo`` "your" — and the user's rule, set on ``ka``/``ikaw`` the same day, is
#: that the enclitic is drilled as a cloze and the FREE FORM gets the picture.
#: Listing both would give one meaning two pictures and put a ``ko`` on the
#: cloze route and off it again. These are asserted as *absent from the tables*
#: rather than through ``_CLOSES``, which asserts they are function words: in
#: ceb and tl ``pos`` carries no ``PRON``, so ``ko`` was never on the cloze route
#: to begin with and claiming otherwise would be a false assertion.
_ENCLITICS: tuple[tuple[str, str, str], ...] = (
    ("ka", "ceb", "your/yours — and it is in function_words.json's include list, so listing it would be dead data"),
    ("ko", "ceb", "his/her — the free form `niya` carries the picture"),
    ("mo", "ceb", "my/our — the free forms `nako`/`namo` carry the picture"),
    ("akin", "tl", "also the oblique 'to me'"),
    ("iyo", "tl", "also the oblique 'to you'"),
)


def _words(code: str) -> dict[str, str]:
    return json.loads(get_pronouns_path(code).read_text(encoding="utf-8"))["words"]


# ── Part C: the Norwegian gendered possessives ─────────────────────────────
#
# ``min`` is "my", ``mi`` "my" of a feminine thing, ``mitt`` of a neuter one and
# ``mine`` of a plural: four words, one picture family, and the mark inside the
# bag is the only thing that tells them apart (tunatale-l1ba). They live in a
# SECOND key of the same file — ``picture_only`` — and that separation is the
# whole routing decision, not a tidiness one: ``words`` is what
# ``is_pronoun_word`` reads, and its entire job is to VETO the cloze route. The
# user's deck holds each of these words as a vocab card AND as a sentence cloze,
# so listing them in ``words`` would have re-pointed lesson matching at a picture
# and dropped the cloze. ``picture_only`` is read by the drawing and by nothing
# else — which is why the invariants below are stated as absences.
#
# Slovene gets nothing: it has no cards in the deck, and ``moje``/``moja`` do not
# separate a gender from a number.

_PICTURE_ONLY: dict[str, dict[str, list[str]]] = {
    "no": {
        "min": ["i_poss:m"],
        "mi": ["i_poss:f"],
        "mitt": ["i_poss:n"],
        "mine": ["i_poss:pl"],
        "din": ["you_one_poss:m"],
        "di": ["you_one_poss:f"],
        "ditt": ["you_one_poss:n"],
        "dine": ["you_one_poss:pl"],
        "vår": ["we_many_poss"],
        "vårt": ["we_many_poss:n"],
        "våre": ["we_many_poss:pl"],
        "hans": ["he_poss"],
        "hennes": ["she_poss"],
        # `deres` is both "your (plural)" and "their", so one word takes a LIST of
        # specs and the card's gloss says which picture it wants.
        "deres": ["you_many_poss", "they_many_poss"],
    },
}

#: The words deliberately NOT in the table, and why. ``sin/si/sitt/sine`` are the
#: REFLEXIVE ("his own") and there is no clean scene for them; Slovene inflects
#: its possessives for the possessed noun (``moj``/``moja``/``moje``), so a mark
#: inside the bag would be ambiguous between a gender and a number.
_REFLEXIVES: tuple[tuple[str, str], ...] = (
    ("sin", "his own"),
    ("si", "her own"),
    ("sitt", "its own"),
    ("sine", "their own"),
)


def _picture_only_file(code: str) -> dict[str, list[str]]:
    """The raw ``picture_only`` key, as the data file spells it."""
    return json.loads(get_pronouns_path(code).read_text(encoding="utf-8")).get("picture_only", {})


def _discriminating_gloss(spec: str, earlier: list[str]) -> str:
    """An English gloss that selects *spec* and none of the specs before it.

    The list is tried in order, so "is this spec reachable" is a question about
    the ORDER and not only about the concept — a table where the second spec is
    unreachable is a table with a dead entry, and only a gloss that skips the
    first one can say so.
    """
    concept = _spec(spec)[0]
    taken = set().union(*(_CONCEPT_GLOSSES[_spec(other)[0]] for other in earlier)) if earlier else set()
    return sorted(set(_CONCEPT_GLOSSES[concept]) - taken)[0]


class TestDataFiles:
    def test_every_language_that_teaches_vocabulary_registers_a_file(self) -> None:
        assert set(LANGS) == {"ceb", "no", "sl", "tl"}

    @pytest.mark.parametrize("code", LANGS)
    def test_the_table_is_the_one_the_brief_lays_out(self, code) -> None:
        # Exact, not a subset: a word dropped from a file stops being a picture
        # card and nobody would notice from the app, and one ADDED is a decision
        # the gloss guard has not been checked against.
        assert _words(code) == _TABLES[code]

    @pytest.mark.parametrize("code", LANGS)
    def test_every_word_names_a_concept_the_renderer_can_draw(self, code) -> None:
        assert {w: c for w, c in _words(code).items() if c not in PRONOUN_CONCEPTS} == {}

    @pytest.mark.parametrize("code", LANGS)
    def test_words_are_lowercase_single_tokens(self, code) -> None:
        assert [w for w in _words(code) if w != w.casefold() or not w.isalpha()] == []

    @pytest.mark.parametrize("code", LANGS)
    def test_no_pronoun_word_is_also_a_function_word(self, code) -> None:
        # The veto makes an `include` entry dead data. Dead data is where the
        # next reader believes the wrong file, so it is refused.
        include = {
            w.casefold()
            for w in json.loads(get_function_words_path(code).read_text(encoding="utf-8")).get("include", [])
        }
        assert include & set(_words(code)) == set()

    @pytest.mark.parametrize("code", LANGS)
    def test_no_pronoun_word_is_also_a_spatial_word(self, code) -> None:
        # Two gloss-guarded families sharing a word would mean the dispatch
        # order silently decided which sense the card shows.
        assert set(_words(code)) & set(load_spatial_words(code)) == set()

    @pytest.mark.parametrize("code", LANGS)
    def test_no_pronoun_word_is_also_a_number(self, code) -> None:
        assert [w for w in _words(code) if number_picture(w, code) is not None] == []

    def test_a_language_without_a_file_has_no_pronoun_words(self) -> None:
        assert load_pronoun_words("en") == {}
        assert pronoun_picture("she", "en", "she") is None


class TestGlossGuard:
    def test_the_keywords_are_the_briefs(self) -> None:
        # ORACLE 4 and ORACLE A3, pinned exactly. Whole-word, casefolded matching
        # against a gloss is the only thing standing between a homograph and a
        # wrong picture, so the vocabulary it matches on is data, not an
        # implementation detail. The possessive half is the reason the guard has
        # to keep working: `siya` and `niya` are different words for "he/she"
        # and "his/her", and only the gloss says which one a card is.
        assert {c: set(k) for c, k in _CONCEPT_GLOSSES.items()} == {
            "i": {"i", "me"},
            "you_one": {"you"},
            "you_two": {"you"},
            "you_many": {"you"},
            "he": {"he", "him"},
            "she": {"she", "her"},
            "third_one": {"he", "she", "him", "her"},
            "it": {"it"},
            "we_two": {"we", "us"},
            "we_many": {"we", "us"},
            "we_incl": {"we", "us"},
            "we_excl": {"we", "us"},
            "they_two": {"they", "them"},
            "they_many": {"they", "them"},
            "i_poss": {"my", "mine"},
            "you_one_poss": {"your", "yours"},
            "you_two_poss": {"your", "yours"},
            "you_many_poss": {"your", "yours"},
            "he_poss": {"his"},
            "she_poss": {"her", "hers"},
            "third_one_poss": {"his", "her", "hers"},
            "it_poss": {"its"},
            "we_two_poss": {"our", "ours"},
            "we_many_poss": {"our", "ours"},
            "we_incl_poss": {"our", "ours"},
            "we_excl_poss": {"our", "ours"},
            "they_two_poss": {"their", "theirs"},
            "they_many_poss": {"their", "theirs"},
        }

    def test_every_concept_has_keywords(self) -> None:
        assert set(_CONCEPT_GLOSSES) == set(PRONOUN_CONCEPTS)

    @pytest.mark.parametrize(
        ("concept", "gloss"),
        [
            ("i", "I"),
            ("i", "me"),
            ("you_one", "you"),
            ("you_two", "you (the two of you)"),
            ("you_many", "you all"),
            ("he", "he"),
            ("he", "him"),
            ("she", "she"),
            ("she", "her"),
            ("third_one", "he or she"),
            ("it", "it"),
            ("it", "it (the cat)"),
            ("we_two", "we two"),
            ("we_many", "we"),
            ("we_incl", "us, including you"),
            ("we_excl", "we, but not you"),
            ("they_two", "the two of them"),
            ("they_many", "they"),
            # The possessive half, including the real deck's glosses: the
            # "his / her; by him or her" form is how a gender-neutral `niya`
            # says which picture it wants.
            ("i_poss", "my"),
            ("i_poss", "mine"),
            ("you_one_poss", "your"),
            ("you_many_poss", "yours"),
            ("he_poss", "his"),
            ("she_poss", "her, hers"),
            ("third_one_poss", "his / her; by him or her"),
            ("it_poss", "its"),
            ("we_excl_poss", "our"),
            ("we_incl_poss", "ours, including you"),
            ("they_many_poss", "their"),
            ("they_two_poss", "theirs, the two of them"),
        ],
    )
    def test_a_gloss_that_names_the_referent_matches(self, concept, gloss) -> None:
        assert gloss_matches(concept, gloss)

    @pytest.mark.parametrize(
        ("concept", "gloss"),
        [
            # The homographs the token veto cannot see (ORACLE 3, ORACLE 4):
            ("it", "that"),  # no `den`
            ("they_many", "the"),  # no `de` — the article, in English too
            ("we_incl", "see"),  # no ceb `kita`
            ("i", ""),
            ("i", "we"),  # a different concept's keyword does not confirm this one
            ("it", "its"),  # a word containing the keyword is not the keyword
            ("he", "she"),
            ("you_one", "young"),
            ("third_one", "it"),  # siya is not glossed "it"
            # A NOMINATIVE gloss must not confirm a possessive concept: `niya`
            # glossed "he" is a card about the wrong word, and the free form
            # `siya` is the one that means it.
            ("third_one_poss", "he"),
            ("i_poss", "me"),
            ("it_poss", "it"),
            ("he_poss", "him"),
            ("we_excl_poss", "we"),
            ("you_one_poss", "you"),
            ("i_poss", ""),
        ],
    )
    def test_a_gloss_that_does_not_name_the_referent_refuses(self, concept, gloss) -> None:
        assert not gloss_matches(concept, gloss)


class TestDispatch:
    @pytest.mark.parametrize(
        ("word", "code", "gloss", "concept"),
        [
            ("jeg", "no", "I", "i"),
            ("du", "no", "you", "you_one"),
            ("hun", "no", "she", "she"),
            ("vi", "no", "we", "we_many"),
            ("de", "no", "they", "they_many"),
            ("jaz", "sl", "I", "i"),
            ("midva", "sl", "we two", "we_two"),
            ("vidve", "sl", "you two", "you_two"),
            ("oni", "sl", "they", "they_many"),
            ("ako", "tl", "I", "i"),
            ("siya", "tl", "he or she", "third_one"),
            ("kami", "tl", "we, but not you", "we_excl"),
            ("kita", "ceb", "we, including you", "we_incl"),
            ("sila", "ceb", "they", "they_many"),
            ("AKO", "ceb", "I", "i"),
            # The possessives, in both languages. `niya` and `siya` are the pair
            # the whole possessive family exists for: two words, two pictures,
            # and only the gloss tells them apart.
            ("niya", "ceb", "his / her; by him or her", "third_one_poss"),
            ("nako", "ceb", "my", "i_poss"),
            ("nimo", "ceb", "your", "you_one_poss"),
            ("nila", "ceb", "their", "they_many_poss"),
            ("namo", "ceb", "our", "we_excl_poss"),
            ("nato", "ceb", "our, including you", "we_incl_poss"),
            ("ninyo", "ceb", "yours", "you_many_poss"),
            ("niya", "tl", "his", "third_one_poss"),
            ("namin", "tl", "our", "we_excl_poss"),
            ("natin", "tl", "our, including you", "we_incl_poss"),
            ("ninyo", "tl", "yours", "you_many_poss"),
            ("nila", "tl", "their", "they_many_poss"),
            ("NIYA", "tl", "her, hers", "third_one_poss"),
        ],
    )
    def test_a_pronoun_word_is_drawn_as_its_concept(self, word, code, gloss, concept) -> None:
        picture = pronoun_picture(word, code, gloss)
        assert picture is not None
        svg = render_pronoun_svg(concept)
        assert picture.svg == svg
        assert picture.filename == f"pronoun_{concept}_{hashlib.sha256(svg).hexdigest()[:8]}.svg"

    @pytest.mark.parametrize(
        ("word", "code", "gloss"),
        [
            ("den", "no", "that"),  # a homograph of `it`, glossed as its other sense
            ("de", "no", "the"),
            ("kita", "ceb", "see"),
            ("jeg", "no", ""),  # no gloss: cannot confirm the sense, so no picture
            ("hus", "no", "house"),  # not a pronoun at all
            ("det", "no", "it"),  # polysemous, deliberately not listed
            ("jaz", "no", "I"),  # right word, wrong language
            # The possessive guards: a nominative gloss on a possessive word, and
            # the enclitics whose free forms carry the pictures.
            ("niya", "ceb", "he"),
            ("niya", "tl", "she"),
            ("nako", "ceb", "I"),
            ("nila", "tl", "they"),
            ("ko", "ceb", "his"),
            ("mo", "ceb", "my"),
            ("ka", "ceb", "your"),
            ("akin", "tl", "my"),
            ("iyo", "tl", "your"),
        ],
    )
    def test_anything_else_is_left_on_its_route(self, word, code, gloss) -> None:
        assert pronoun_picture(word, code, gloss) is None

    def test_the_word_is_stripped_and_casefolded_like_the_spatial_family(self) -> None:
        assert pronoun_picture("  Hun ", "no", "she") == pronoun_picture("hun", "no", "she")


class TestPictureOnlyData:
    """The table is a second key of ``pronouns.json``, and that is the design."""

    def test_only_norwegian_registers_one(self) -> None:
        # Slovene gets nothing: no cards in the deck, and `moje`/`moja` do not
        # separate a gender from a number. ceb/tl have no gender to mark.
        assert set(_picture_only_file("no")) == set(_PICTURE_ONLY["no"])
        for code in ("sl", "ceb", "tl"):
            assert _picture_only_file(code) == {}
            assert load_picture_only(code) == {}

    @pytest.mark.parametrize("code", LANGS)
    def test_the_table_is_the_one_the_brief_lays_out(self, code) -> None:
        # Exact, like `words`: a word dropped stops being a picture and nothing in
        # the app would say so.
        assert _picture_only_file(code) == _PICTURE_ONLY.get(code, {})

    @pytest.mark.parametrize(("word", "specs"), _PICTURE_ONLY["no"].items())
    def test_every_spec_names_a_thing_the_renderer_can_draw(self, word, specs) -> None:
        for spec in specs:
            concept, owned = _spec(spec)
            assert concept in PRONOUN_CONCEPTS, f"{word}: {spec}"
            assert owned in (None, "m", "f", "n", "pl"), f"{word}: {spec}"
            # And it really is drawable, which is the check the concept list alone
            # cannot make: a gender mark is the only shape that needs a bag, and
            # every word here is a possessive.
            assert render_pronoun_svg(concept, owned).startswith(b"<svg ")
            assert concept.endswith("_poss"), f"{word}: {spec} has no bag to mark"

    @pytest.mark.parametrize(("word", "specs"), _PICTURE_ONLY["no"].items())
    def test_the_two_keys_are_disjoint(self, word, specs) -> None:
        # The same word in `words` would be vetoed off the cloze route, which is
        # the thing this key exists to avoid.
        assert word not in _words("no")

    @pytest.mark.parametrize(("word", "specs"), _PICTURE_ONLY["no"].items())
    def test_a_picture_only_word_is_not_a_pronoun_word(self, word, specs) -> None:
        # THE invariant. `is_pronoun_word` is what `is_function_word` asks first,
        # and a True here would take every one of these words off the cloze route
        # the deck already has it on.
        assert not is_pronoun_word(word, "no")
        assert is_function_word(word, "no", upos="PRON"), f"{word} must stay a cloze"

    @pytest.mark.parametrize(("word", "specs"), _PICTURE_ONLY["no"].items())
    def test_the_cloze_routing_is_unchanged_by_the_picture(self, word, specs) -> None:
        # Read as a whole, and over both tags the deck actually uses: these words
        # are function words by POS (no's `pos` set takes DET and PRON) and the
        # veto does not reach them. The veto's one exemption in this language — a
        # DET homograph like `den` — must not be extended to them either.
        assert is_function_word(word, "no", upos="DET"), f"{word} must stay a cloze"
        assert is_function_word(word, "no", upos="PRON"), f"{word} must stay a cloze"

    def test_min_stays_a_function_word_the_way_the_brief_names_it(self) -> None:
        # Spelled out because it is the one the brief pins, and because it is the
        # word a future reader would most expect to have been "fixed" into a
        # picture-only listing.
        assert not is_pronoun_word("min", "no")
        assert is_function_word("min", "no", upos="DET")
        assert is_function_word("min", "no", upos="PRON")

    @pytest.mark.parametrize(("word", "why"), _REFLEXIVES)
    def test_a_reflexive_gets_no_picture(self, word, why) -> None:
        # `sin/si/sitt/sine` are "his own", and no scene says that: the bag would
        # be on the same referent that already has one, and the mark would be
        # indistinguishable from the one the ordinary possessive draws.
        assert word not in _picture_only_file("no"), why
        assert pronoun_picture(word, "no", "his own") is None

    def test_norwegian_mi_is_not_slovene_mi(self) -> None:
        # The two languages' data files are separate, and Slovene `mi` is a dative
        # and an enclitic — so it must not pick up a feminine bag by name alone.
        # Note the gloss: "her" confirms a THIRD-person possessive, and `mi` is
        # "my", so the guard refuses it. The gender lives in the spec, never in
        # what the card's English says.
        assert _spec(_picture_only_file("no")["mi"][0]) == ("i_poss", "f")
        assert load_picture_only("sl").get("mi") is None
        assert pronoun_picture("mi", "sl", "my") is None
        assert pronoun_picture("mi", "no", "her") is None
        assert pronoun_picture("mi", "no", "my").svg == render_pronoun_svg("i_poss", "f")

    @pytest.mark.parametrize("spec", ["", "i_poss:", ":m", "i_poss:x", "sideways", "i_poss:m:m", "i:m"])
    def test_an_unreadable_spec_is_refused_rather_than_approximated(self, spec) -> None:
        # A spec naming a concept the renderer cannot draw, or a gender it does
        # not have, would otherwise draw the bag it always draws and be confidently
        # wrong — which is the same argument as refusing an unrenderable concept.
        with pytest.raises(ValueError, match="spec"):
            _spec(spec)

    def test_the_spec_parser_returns_the_concept_and_the_gender(self) -> None:
        assert _spec("i_poss") == ("i_poss", None)
        assert _spec("i_poss:m") == ("i_poss", "m")
        assert _spec("we_many_poss:pl") == ("we_many_poss", "pl")


class TestPictureOnlyDispatch:
    #: Every word, with a gloss that reaches its FIRST spec, and the concept that
    #: spec names. Transcribed rather than derived so that a change to the table
    #: above cannot quietly change the expectation with it.
    _CASES: tuple[tuple[str, str, str, str | None], ...] = (
        ("min", "my, mine", "i_poss", "m"),
        ("mi", "my, mine", "i_poss", "f"),
        ("mitt", "my, mine", "i_poss", "n"),
        ("mine", "my, mine", "i_poss", "pl"),
        ("din", "your, yours", "you_one_poss", "m"),
        ("di", "your, yours", "you_one_poss", "f"),
        ("ditt", "your, yours", "you_one_poss", "n"),
        ("dine", "your, yours", "you_one_poss", "pl"),
        ("vår", "our, ours", "we_many_poss", None),
        ("vårt", "our, ours", "we_many_poss", "n"),
        ("våre", "our, ours", "we_many_poss", "pl"),
        ("hans", "his", "he_poss", None),
        ("hennes", "her, hers", "she_poss", None),
        ("deres", "your (plural)", "you_many_poss", None),
    )

    @pytest.mark.parametrize(("word", "gloss", "concept", "owned"), _CASES)
    def test_a_gendered_possessive_is_drawn_with_its_mark(self, word, gloss, concept, owned) -> None:
        picture = pronoun_picture(word, "no", gloss)
        svg = render_pronoun_svg(concept, owned)
        assert picture is not None
        assert picture.svg == svg
        assert (
            picture.filename
            == f"pronoun_{concept}{f'_{owned}' if owned else ''}_{hashlib.sha256(svg).hexdigest()[:8]}.svg"
        )

    def test_the_four_rendered_files_are_four_different_pictures(self) -> None:
        # Four words, one family: if any two of min/mi/mitt/mine rendered the same
        # bytes they would be three words on two pictures.
        renders = {owned: render_pronoun_svg("i_poss", owned) for owned in (None, "m", "f", "n", "pl")}
        assert len({bytes(v) for v in renders.values()}) == len(renders)

    def test_the_gender_is_carried_by_the_gloss_and_nothing_else(self) -> None:
        # `min` and `mi` are different words with the SAME gloss, so the table is
        # the only thing that could tell them apart — and the picture it names is
        # the one that differs.
        assert render_pronoun_svg("i_poss", "m") != render_pronoun_svg("i_poss", "f")
        assert pronoun_picture("min", "no", "my").svg == render_pronoun_svg("i_poss", "m")
        assert pronoun_picture("mi", "no", "my").svg == render_pronoun_svg("i_poss", "f")

    @pytest.mark.parametrize(("word", "spec"), [(w, s) for w, ss in _PICTURE_ONLY["no"].items() for s in ss])
    def test_every_spec_in_the_table_is_reachable_by_its_own_gloss(self, word, spec) -> None:
        # The property, over the whole table rather than a sample: a spec is
        # reachable when SOME gloss selects it and no earlier spec claims that
        # gloss first. A `deres` whose second spec were shadowed would be a dead
        # entry, and no count would see it.
        specs = _picture_only_file("no")[word]
        earlier = specs[: specs.index(spec)]
        gloss = _discriminating_gloss(spec, earlier)
        concept, owned = _spec(spec)
        assert pronoun_picture(word, "no", gloss).svg == render_pronoun_svg(concept, owned)

    def test_deres_is_your_plural_or_theirs_by_the_gloss(self) -> None:
        # The one word in the table that is two words, and the case the list was
        # specified for: `vår` "spring" is the neighbouring homograph that gets
        # nothing at all.
        assert pronoun_picture("deres", "no", "your (plural)").svg == render_pronoun_svg("you_many_poss")
        assert pronoun_picture("deres", "no", "their").svg == render_pronoun_svg("they_many_poss")

    @pytest.mark.parametrize(
        ("word", "gloss"),
        [
            ("vår", "spring"),
            ("min", "spring"),
            ("mine", "spring"),
            ("hans", "he"),
            ("hennes", "she"),
            ("vår", ""),
            ("mine", ""),
            ("min", "I"),
            ("sin", "his own"),
            ("sine", "their own"),
            # A nominative gloss never confirms a possessive concept — the same
            # rule the `words` table is guarded by, and it is the guard doing the
            # work, not the key the word is in.
            ("hans", "he"),
            ("min", "it"),
        ],
    )
    def test_a_gloss_that_does_not_name_the_referent_is_left_on_its_route(self, word, gloss) -> None:
        assert pronoun_picture(word, "no", gloss) is None

    def test_a_word_is_stripped_and_casefolded_like_every_other_word(self) -> None:
        assert pronoun_picture("  Min ", "no", "my") == pronoun_picture("min", "no", "my")
        assert pronoun_picture("VÅR", "no", "our") == pronoun_picture("vår", "no", "our")

    def test_the_picture_route_does_not_disturb_the_drawn_picture_dispatch(self) -> None:
        # `drawn_picture` is the front door, and these words now have pictures of
        # their own: the family grew, the order did not.
        assert drawn_picture("min", "no", "my") == pronoun_picture("min", "no", "my")
        assert drawn_picture("vår", "no", "spring") is None
        assert drawn_picture("jeg", "no", "I") == pronoun_picture("jeg", "no", "I")


class TestDrawnPicture:
    def test_a_number_is_drawn_as_a_number_whatever_the_gloss(self) -> None:
        # The dispatch order is number, spatial, pronoun — and it is asserted
        # here rather than trusted, because a number and a pronoun are both
        # "a word with a perfect picture" and only one can be.
        assert drawn_picture("fem", "no", "") == number_picture("fem", "no")
        assert drawn_picture("singko", "ceb", "five") == number_picture("singko", "ceb")

    def test_a_pronoun_word_is_drawn_as_its_concept(self) -> None:
        assert drawn_picture("hun", "no", "she") == pronoun_picture("hun", "no", "she")
        assert drawn_picture("siya", "tl", "he or she") == pronoun_picture("siya", "tl", "he or she")

    def test_a_spatial_word_still_wins_over_nothing_and_keeps_working(self) -> None:
        assert drawn_picture("under", "no", "under") is not None
        assert is_spatial_word("under", "no")

    def test_an_ordinary_word_has_no_drawing(self) -> None:
        assert drawn_picture("hus", "no", "house") is None


class TestFunctionWordVeto:
    """A pronoun word is a picture card, never a cloze (the user, 2026-09-28)."""

    @pytest.mark.parametrize(("code", "word"), [(code, word) for code, words in _TABLES.items() for word in words])
    def test_a_pronoun_word_is_not_a_function_word(self, code, word) -> None:
        assert is_pronoun_word(word, code)
        assert not is_function_word(word, code)

    @pytest.mark.parametrize(("code", "word"), [(code, word) for code, words in _TABLES.items() for word in words])
    def test_the_veto_outranks_a_closed_class_pos_tag(self, code, word) -> None:
        # The mint's fork passes the deck's UPOS, and PRON is in no/sl's `pos`
        # set — so without the veto every one of these is a cloze, and the
        # pre-stage drills the case marker instead of the referent.
        assert not is_function_word(word, code, upos="PRON")

    @pytest.mark.parametrize(("word", "code", "why"), _CLOSES)
    def test_the_deliberate_clozes_stay_clozes(self, word, code, why) -> None:
        assert not is_pronoun_word(word, code), why
        assert is_function_word(word, code, upos="PRON"), why

    @pytest.mark.parametrize(("word", "code", "why"), _ENCLITICS)
    def test_a_possessive_enclitic_is_not_a_picture_word(self, word, code, why) -> None:
        # The rule the user set on ka/ikaw the same day: the enclitic is drilled,
        # the free form gets the picture. Asserted on the TOKEN because that is
        # the only level at which the choice exists — and `ka` is additionally in
        # ceb's include list, where a listing would be dead data.
        assert not is_pronoun_word(word, code), why
        assert pronoun_picture(word, code, "his") is None

    # The pronoun veto is gated on the tag, where the spatial one is not: Norwegian
    # `den` and `de` are also the articles ("den store bilen", "de store husene"),
    # the most frequent function words in the deck. Tagged DET they must stay on
    # the cloze route, or every article becomes a vocab card and a gloss of "the"
    # sends it to a photo search. Untagged (the table lemmatizer emits no UPOS)
    # the veto applies: without a tag, none of these is in the include list, so
    # nothing changes there that did not already hold.
    @pytest.mark.parametrize("word", ["den", "de"])
    def test_a_pronoun_homograph_tagged_det_stays_a_function_word(self, word) -> None:
        assert is_function_word(word, "no", upos="DET")

    @pytest.mark.parametrize("word", ["den", "de"])
    def test_the_same_word_tagged_pron_is_vetoed(self, word) -> None:
        assert not is_function_word(word, "no", upos="PRON")

    @pytest.mark.parametrize("upos", [None, ""])
    def test_an_untagged_pronoun_is_vetoed(self, upos) -> None:
        assert not is_function_word("jeg", "no", upos=upos)

    def test_a_spatial_word_is_vetoed_whatever_its_tag(self) -> None:
        # The contrast that makes the gate a decision, not an accident: spatial
        # words were vetoed unconditionally (hvj0) and still are.
        assert not is_function_word("under", "no", upos="ADP")
