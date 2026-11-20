"""Short forms are first class on a Cebuano card (tunatale-0rlg, the owner 2026-10-29).

"We treat inday, day as its own card. Why do the pronouns differently? ... I
just want it to be consistent. Short forms are for sure a first-class concept
in Cebuano." So every word in a short/long pair says so on one line, the
``Short form`` field, under the word wherever the card writes it.

One rule decides how many cards a pair has. Forms that are interchangeable
share a card (``dili`` and ``di``), and that card names its short form. A short
form with its own grammar has its own card (the pronoun clitics: Wolff,
Beginning Cebuano 3 B 4, a predicate pronoun is the long form; ``mo`` shortens
both ``kamo`` and ``nimo``), and that card names the word it shortens, while
the long word's card names it back.

The line goes through the registry (``dictionary_note_fields``), the hook the
mint, the backfill and TT's own extras all read, so the three agree. Its marks
are Wolff's, as on the Spelling line: the pair is spelled as one front would
be ('dílì, dì').
"""

from __future__ import annotations

import pytest

from app.languages import dictionary_note_fields
from app.plugins.languages.ceb import short_form_pairs


def _line(word: str, english: str, disambig: str = "") -> str | None:
    return dictionary_note_fields("ceb", word, english, disambig).get("Short form")


@pytest.mark.parametrize(
    ("word", "english", "line"),
    [
        ("dili", "no; not", "short form: dì"),
        ("unya", "later", "short form: nyà"),
        ("manong", "title for an elder brother; older man", "short form: nong"),
        ("inday", "Inday", "short form: day"),
        ("usab", "also", "short form: sad"),
    ],
)
def test_a_card_shared_by_two_interchangeable_forms_names_its_short_form(word, english, line):
    assert _line(word, english) == line


@pytest.mark.parametrize(
    ("word", "disambig", "english", "line"),
    [
        ("mo", "", "you (plural)", "short for kamó"),
        ("mo", "genitive", "your; by you (one person)", "short for nímo"),
        ("ko", "", "I", "short for akó"),
        ("ko", "genitive", "my; by me", "short for nákò"),
        ("ta", "", "we (including you)", "short for kitá"),
        ("ta", "genitive", "our (including you); by us", "short for nátò"),
        ("ka", "", "you (one person)", "short for ikáw"),
        ("mi", "", "we (not including you)", "short for kamí"),
    ],
)
def test_a_short_pronoun_names_the_word_it_shortens(word, disambig, english, line):
    """The sense picks the long word: the owner ``mo`` is short nimo, not kamo."""
    assert _line(word, english, disambig) == line


@pytest.mark.parametrize(
    ("word", "disambig", "english", "line"),
    [
        ("kamo", "", "you (plural)", "short form: mo"),
        ("nimo", "", "your; by you (one person)", "short form: mo"),
        ("ako", "", "I", "short form: ko"),
        ("nako", "", "my; by me", "short form: ko"),
        ("kita", "pronoun", "we (including you)", "short form: ta"),
        ("nato", "", "our (including you); by us", "short form: ta"),
        ("ikaw", "", "you (one person)", "short form: ka"),
        ("kami", "", "we (not including you)", "short form: mi"),
    ],
)
def test_a_long_pronoun_names_its_short_form(word, disambig, english, line):
    assert _line(word, english, disambig) == line


@pytest.mark.parametrize(
    ("word", "disambig", "english"),
    [
        ("kita", "", "see"),  # the verb kítà: only the pronoun kitá shortens to ta
        ("ako", "possessive", "my, mine; by me"),  # preposed ákò has no short form
        ("namo", "", "our (not including you); by us"),  # Wolff: námù has none
        ("balay", "", "house"),
        ("puwede", "", "can"),  # a spelling, not a short form
    ],
)
def test_a_word_in_no_short_pair_has_no_line(word, disambig, english):
    assert _line(word, english, disambig) is None


@pytest.mark.parametrize(
    ("word", "english", "line"),
    [
        ("to", "that one", "short for kádto"),  # card 919: the model said 'kato'
        ("bay", "friend; buddy", "short for abáy"),
        ("naa", "there is; is at (a place)", "short for anâa"),
        ("nia", "is here (near you and me)", "short for anîa"),
        ("sakto", "correct, right", "short for eksákto"),  # 'saktu short for iksaktu'
    ],
)
def test_a_short_form_in_no_table_takes_wolffs_pointer(word, english, line):
    """tunatale-iiwm.11: where the tables say nothing, Wolff's own 'short for' pointer
    is the line, so the card, not the model's gloss, says what the word shortens. It
    is the same pointer the Details layer already calls 'Short form of'."""
    assert _line(word, english) == line


@pytest.mark.parametrize(
    ("word", "english"),
    [
        ("maayo", "good"),  # 'maáyu see áyu' lands on a sub-entry spelled the same
        ("palihug", "please"),  # 'see *líhug' points at a root, not a fuller word
        ("kadto", "that one"),  # the long word: Wolff's 'short form: tu' is not named back
        ("kawhaan", "twenty"),  # 'see duha' points at the root, not a fuller word
        ("gilayon", "immediately"),  # 'see dáyun', the same
        ("lalawigan", "province"),  # 'see lawig'
        ("pwede", "can"),  # 'pwídi = puydi' is another spelling
        ("xyzzy", "nothing at all"),  # not in Wolff
    ],
)
def test_wolff_gives_no_line_for_a_word_that_shortens_nothing(word, english):
    assert _line(word, english) is None


def test_another_spelling_is_not_called_a_short_form_anywhere_on_the_card():
    """'pwídi = puydi' is a spelling pointer: neither the line nor the Details layer may
    call puwede a short form of púydi. The control is naa, whose '=' pointer anáa
    confirms by listing náa as its short form."""
    puwede = dictionary_note_fields("ceb", "puwede", "can")
    assert "Short form" not in puwede
    assert "Short form of" not in puwede["Details"]
    assert "Short form of <b>anâa</b>" in dictionary_note_fields("ceb", "naa", "there is")["Details"]


def test_a_root_pointer_is_not_called_a_short_form_in_the_details():
    """The Details layer and the line read one rule: kawhaan 'see duha' showed
    'Short form of duhá' before tunatale-iiwm.11."""
    assert "Short form of" not in dictionary_note_fields("ceb", "kawhaan", "twenty")["Details"]
    assert "Short form of <b>kádto</b>" in dictionary_note_fields("ceb", "to", "that one")["Details"]


def test_a_form_wolff_does_not_settle_is_written_as_the_table_spells_it():
    """The marks are Wolff's when he settles the pair; the line never waits on him.
    The control is the same word with a gloss he settles."""
    assert _line("mo", "zzz no such sense") == "short for kamo"
    assert _line("inday", "zzz no such sense") == "short form: day"
    assert _line("mo", "you (plural)") == "short for kamó"


def test_a_comma_front_not_yet_renamed_is_its_long_form():
    assert _line("dili, di", "no; not") == "short form: dì"


def test_a_word_class_in_the_key_is_the_words_own_sense():
    assert _line("dili", "no; not", "adverb") == "short form: dì"
    assert _line("kita", "see", "verb") is None


class TestThePairs:
    """The pairs come from the two tables that already hold them: the spelling
    variants (``kind = short``) and the pronoun table (``short_forms``)."""

    def test_interchangeable_pairs_are_the_short_rows_of_the_spelling_table(self):
        shared = {(p.short, p.long) for p in short_form_pairs() if p.shares_card}
        assert shared == {
            ("di", "dili"),
            ("jud", "gyud"),
            ("nya", "unya"),
            ("sad", "usab"),
            ("nong", "manong"),
            ("nang", "manang"),
            ("day", "inday"),
            ("gyud", "gayud"),  # gayud's row is kind=long: gyud is its short form
        }

    def test_a_plain_spelling_is_not_a_short_form(self):
        words = {w for p in short_form_pairs() for w in (p.short, p.long)}
        assert not words & {"puwede", "pwede", "mesa", "lamesa", "igsoon", "sayis", "pod"}

    def test_pronoun_pairs_carry_their_senses(self):
        own = {(p.short, p.short_key, p.long, p.long_key) for p in short_form_pairs() if not p.shares_card}
        assert own == {
            ("mo", "", "kamo", ""),
            ("mo", "genitive", "nimo", ""),
            ("ko", "", "ako", ""),
            ("ko", "genitive", "nako", ""),
            ("ta", "", "kita", "pronoun"),
            ("ta", "genitive", "nato", ""),
            ("ka", "", "ikaw", ""),
            ("mi", "", "kami", ""),
        }
