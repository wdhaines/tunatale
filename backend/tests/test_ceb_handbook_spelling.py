"""Wolff's dictionary spelling -> the Handbook spelling (tunatale-h0q3.2).

Wolff's 1972 dictionary leaves stress to rule and the glottal stop between
vowels unwritten. The course the owner reads (Wolff's 1966 lessons, respelled to
Raisanen & Raisanen's Handbook, owner-QA'd for Lessons 1-3) marks both. The
rows below are the oracle, from two sources:

* the dictionary's own examples of its rules (intro §3.0, §3.21-3.24):
  ``káun [kā́ʔun]``, ``nagdá``, ``tan-aw [tánʔaw]``, ``mugbù [múgbuʔ]``,
  ``mala [malá]``, ``mandǎr [mandár]``, ``bàbà /báʔbaʔ/``, ``làhib /láʔhib/``,
  ``palya [pálya]`` beside ``paliya [paliyá]``;
* the respelled Lessons 1-3, for words both books spell: ``màáyo``, ``nâa``,
  ``dìáy``, ``kùán``, ``bùók``, ``dínhi``, ``mòádto``, ``eskína``, ``líbro``.

The stress rule for two like vowels in a row is the dictionary's stated one
(open penult, so the final syllable): ``buuk`` -> ``bùók`` and ``maay`` ->
``màáy`` agree with the course; ``taas`` -> ``tàás`` does not (the course has
``tâas``). Those disagreements go to the owner rather than into a rule that
the dictionary itself does not state.
"""

from __future__ import annotations

import pytest

from app.plugins.languages.ceb.handbook_spelling import (
    Loan,
    build_lexicon,
    choose_vowels,
    convert_text,
    handbook_spelling,
    load_course_spellings,
    spanish_to_cebuano,
)

# A small lexicon standing in for the real one (Wolff's headwords, the
# FineWeb-2 news counts, wordfreq's Spanish list, CMU-derived English sounds).
ENTRIES = {
    "adtu": "go there",
    "amirika": "america",
    "draybir": "driver",
    "duktur": "doctor, physician",
    "iskina": "street corner",
    "kaun": "eat",
    "kiri": "this (near speaker)",
    "kuan": "pause word",
    "libru": "book",
    "buuk": "piece, single one",
    "maayu": "good",
    "mistir": "mister, title before a man's name",
    "tan-aw": "see",
    "tawag": "call",
    "ugaling": "self",
}
FREQ = {
    "amerika": 120,
    "doktor": 300,
    "drayber": 40,
    "eskina": 25,
    "libro": 210,
    "mister": 50,
    "buok": 175,
    "book": 106,
}
ES_WORDS = ["esquina", "doctor", "libro", "américa", "book"]
EN_SOUNDS = [("driver", "drayber", 900), ("kerry", "keri", 9000), ("mister", "mister", 3000)]
ENGLISH_COMMON = {"book", "driver", "doctor"}


@pytest.fixture(scope="module")
def lex():
    return build_lexicon(
        freq=FREQ,
        entries=ENTRIES,
        es_words=ES_WORDS,
        en_sounds=EN_SOUNDS,
        english_common=ENGLISH_COMMON,
    )


DICTIONARY_RULES = [
    # long vowel = stress; the unwritten glottal between vowels goes on the
    # vowel before it (circumflex when that vowel is stressed)
    ("káun", "kâon"),
    ("nagdá", "nagdá"),
    # no long vowel, closed penult: stress on the penult
    ("tan-aw", "tán-aw"),
    ("mugbù", "múgbò"),
    ("làhib", "lâhib"),
    ("bàbà", "bâbà"),
    ("dinhi", "dínhi"),
    ("palya", "pálya"),
    ("kutsilyu", "kutsílyo"),
    # no long vowel, open penult: the final syllable
    ("mala", "malá"),
    ("paliya", "paliyá"),
    ("walà", "walâ"),
    ("tulu", "tuló"),
    # the wedge marks a stressed final after a closed penult
    ("mandǎr", "mandár"),
]

COURSE_SPELLINGS = [
    ("maáyu", "màáyo"),
    ("Maáyu", "Màáyo"),
    ("náa", "nâa"),
    ("anáa", "anâa"),
    ("bátà", "bátà"),
    ("túlù", "túlò"),
    ("diay", "dìáy"),
    ("kuan", "kùán"),
    ("búntag", "búntag"),
    # two like vowels follow the open-penult rule too
    ("buuk", "bùók"),
    ("maay", "màáy"),
    # affixes keep o; the root's last syllable takes o
    ("muadtu", "mòádto"),
    ("tawgun", "táwgon"),
    ("kaugalíngun", "kàugalíngon"),
    # Spanish loans keep the e/o Cebuano writers use
    ("iskína", "eskína"),
    ("libru", "líbro"),
    ("duktur", "dóktor"),
    ("Amirika", "Ameriká"),
    # an English loan the entry names, spelled as the corpus spells it
    ("draybir", "dráyber"),
]

SPECIAL = [
    # monosyllables carry no stress mark; a glottal stop is still marked
    ("dyip", "dyip"),
    ("bir", "bir"),
    ("dì", "dì"),
    ("sâ", "sà"),
    # Wolff's homograph number is not part of the word
    ("mísa_1", "mísa"),
    # 'oo' is long, not glottal (owner, 2026-10-16); ug/kun depend on sense,
    # and the particle gud keeps its u (owner, Lesson 3)
    ("uu", "oo"),
    ("Úu", "Oo"),
    ("ug", "ug"),
    ("kun", "kun"),
    ("gud", "gud"),
    # Wolff's title 'Mr.' as he spells it, and the interjection uy (owner, Lesson 1)
    ("Mistir", "Místir"),
    ("uy", "uy"),
    # mau is màó; its short form maw keeps its w (Lesson 3: 'maw (short for màó)')
    ("mau", "màó"),
    ("maw", "maw"),
    # a spelling with e or o is not Wolff's: only the marks are converted
    ("José", "José"),
]


@pytest.mark.parametrize(("wolff", "handbook"), DICTIONARY_RULES + COURSE_SPELLINGS + SPECIAL)
def test_converts_to_the_handbook_spelling(lex, wolff, handbook):
    assert handbook_spelling(wolff, lex).text == handbook


def test_reports_how_the_vowels_were_chosen(lex):
    assert handbook_spelling("iskína", lex).how == "loan-es"
    assert handbook_spelling("draybir", lex).how == "loan-en"
    assert handbook_spelling("maáyu", lex).how == "native"
    assert handbook_spelling("uu", lex).how == "override"
    assert handbook_spelling("nagdá", lex).how == "unknown"


def test_a_spanish_list_word_that_is_plain_english_is_no_loan(lex):
    # wordfreq's Spanish list carries English 'book'; without the guard, buuk
    # 'piece' came out bòók (the respell tool needed a hand fix for it)
    assert "buuk" not in lex.loans
    assert lex.loans["iskina"] == Loan("eskina", "es", "esquina")


def test_an_english_sound_match_needs_the_entry_to_name_the_word(lex):
    # kiri 'this' sounds like 'Kerry', but its entry does not name it
    assert choose_vowels("kiri", lex) == ("kiri", "native")
    assert choose_vowels("draybir", lex) == ("drayber", "loan-en")


def test_unknown_roots_follow_the_corpus_when_it_is_clear():
    lex = build_lexicon(
        freq={"tsokolate": 60, "tsukulati": 2},
        entries={},
        es_words=[],
        en_sounds=[],
        english_common=set(),
    )
    assert choose_vowels("tsukulati", lex) == ("tsokolate", "unknown-corpus")
    assert choose_vowels("bulawan", lex) == ("bulawan", "unknown")


# --- the loan and affix rules ported from raisanen.py, one row per branch ----------------------


def _lex(entries=(), freq=None, es=(), en=()):
    return build_lexicon(
        freq=freq or {},
        entries={e: e for e in entries} if not isinstance(entries, dict) else entries,
        es_words=list(es),
        en_sounds=list(en),
        english_common=set(),
    )


def test_spanish_words_with_letters_cebuano_cannot_spell_are_no_loans():
    assert spanish_to_cebuano("d'oro") is None
    assert spanish_to_cebuano("esquina") == "eskina"
    lex = _lex(es=["d'oro", "casa", "casas"])
    assert "kasa" in lex.loans
    assert "kasas" not in lex.loans  # a plural would collide with contracted -s (iskínas = eskina sa)


def test_english_sounds_past_the_rank_cap_or_too_short_are_no_loans():
    lex = _lex(en=[("rare", "rari", 40000), ("ok", "ok", 10), ("x-ray", "iks-ray", 10), ("table", "teybol", 10)])
    assert set(lex.loans) == {"tiybul"}


def test_an_english_loan_root_in_r_may_be_wolffs_headword_in_d():
    payer = [("payer", "beyar", 100)]
    assert choose_vowels("biyar", _lex({"biyad": "one who pays, payer"}, en=payer)) == ("beyar", "loan-en-source")
    assert choose_vowels("biyar", _lex({"biyad": "one who pays"}, en=payer)) == ("biyar", "native")


def test_an_english_sound_match_outside_wolff_needs_four_letters():
    en = [("driver", "drayber", 900), ("echo", "eko", 900)]
    assert choose_vowels("draybir", _lex(en=en)) == ("drayber", "loan-en-source")
    assert choose_vowels("iku", _lex(en=en)) == ("iko", "unknown")  # ikú is no 'echo'


def test_loans_follow_the_corpus_then_the_source():
    lex = _lex(
        {"pulis": "policeman", "kamisita": "undershirt", "kudu": "elbow", "duktur": "doctor"},
        freq={"pulis": 80, "polis": 1, "doktor": 300},
        es=["polis", "camiseta", "codo", "doctor"],
    )
    assert choose_vowels("pulis", lex) == ("pulis", "loan-es-convention")  # writers settled on i/u
    assert choose_vowels("kamisita", lex) == ("kamiseta", "loan-es-source")  # corpus silent, long root
    assert choose_vowels("kudu", lex) == ("kudo", "native")  # corpus silent, short look-alike
    # the whole word is unknown to the corpus, so the bare root decides
    assert choose_vowels("nagduktur", lex) == ("nagdoktor", "loan-es")


# --- whole words vs lemmas (tunatale-h0q3.38) -------------------------------------------------
#
# freq counts by LEMMA: a form the lemma table knows is folded into its root, so
# magsige (307 as written) is not a key at all and the misspelling magsigi (48)
# was the only whole word left. ``words`` counts the word as written. The counts
# below are the committed tables' (FineWeb-2 ceb).


def _two_tables(entries, freq, words, es=()):
    return build_lexicon(
        freq=freq,
        words=words,
        entries={e: e for e in entries},
        es_words=list(es),
        en_sounds=[],
        english_common=set(),
    )


def test_whole_words_beat_the_lemma_table_when_they_are_clear():
    lex = _two_tables(["sigi"], freq={"sige": 1324, "magsigi": 48}, words={"magsige": 307, "magsigi": 48}, es=["sigue"])
    assert choose_vowels("magsigi", lex) == ("magsige", "loan-es")


def test_a_close_whole_word_count_defers_to_the_bare_root_as_a_word():
    # mosiguro 8 vs moseguro 9 is no evidence. The lemma table credits 'seguro'
    # with 1019 because it chose that spelling for the lemma; as words, writers
    # use siguro four times as often (935 vs 230).
    lex = _two_tables(
        ["siguru"],
        freq={"seguro": 1019, "siguro": 935},
        words={"siguro": 935, "seguro": 230, "mosiguro": 8, "moseguro": 9},
        es=["seguro"],
    )
    assert choose_vowels("musiguru", lex) == ("mosiguro", "loan-es-convention")
    assert choose_vowels("siguru", lex) == ("siguro", "loan-es-convention")


def test_with_no_word_evidence_the_lemma_table_decides_the_root():
    lex = _two_tables(["tumar"], freq={"tumar": 40, "tomar": 2}, words={}, es=["tomar"])
    assert choose_vowels("mitumar", lex) == ("mitumar", "loan-es-convention")


def test_an_unknown_word_follows_whole_words_at_ten_and_twice_the_rule():
    lex = _two_tables([], freq={}, words={"kwentada": 16, "politikanhon": 25, "pulitikanhon": 10, "ded": 9})
    assert choose_vowels("kwintada", lex) == ("kwentada", "unknown-corpus")
    assert choose_vowels("pulitikanhun", lex) == ("politikanhon", "unknown-corpus")
    # English in Wolff's examples: 9 is under the floor (ded is a name or a typo)
    assert choose_vowels("did", lex) == ("did", "unknown")


def test_an_unknown_word_with_too_many_spellings_takes_the_rule():
    # ten i/u letters are 1024 e/o spellings: the corpus is not searched
    lex = _two_tables([], freq={}, words={"kolekolekolekolekolo": 500})
    assert choose_vowels("kulikulikulikulikulu", lex) == ("kulikulikulikulikulo", "unknown")


@pytest.mark.parametrize(
    ("base", "settled"),
    [
        # Whole-word 'belen' is the name Belen (59) and 'boboi' a nickname: a lowercased
        # count cannot tell them from the word. bilin 'left behind' is native (lemma
        # bilin 704 vs belen 67; the Wolff book writes bílin), as is bùbù 'pour'.
        ("bilin", "bilin"),
        ("hibilin", "hibilin"),
        ("nanghibilin", "nanghibilin"),
        ("bubui", "buboi"),
        # bíli 'price' + -ng ('Biling makalípung', a dizzyingly high price), not Belén + g
        ("biling", "biling"),
        # síging is sígi + -ng; the analyser reads the fish net sígin (+ g). Writers
        # write sige (1324 vs 42) and magsige.
        ("siging", "sigeng"),
        # túru 'bull', Spanish toro: turo 'point, teach' shares the letters (6 vs 6)
        ("turu", "toro"),
        ("muturu", "motoro"),
    ],
)
def test_words_the_corpus_counts_as_another_word_are_settled(base, settled):
    assert choose_vowels(base, _lex()) == (settled, "override")


def test_affixes_hyphens_doubling_and_clitics():
    lex = _lex(
        {"una": "first", "mag-una": "go first", "tawu": "person", "libru": "book"},
        freq={"libro": 210},
        es=["libro"],
    )
    assert choose_vowels("mag-una", lex) == ("mag-una", "native")  # the headword beats mag- + una
    assert choose_vowels("nag-una", lex) == ("nag-una", "native")
    assert choose_vowels("tawutawu", lex) == ("tawotawo", "native+doubled")
    assert choose_vowels("librug", lex) == ("librog", "loan-es")  # libru + contracted ug
    # contracted sa / ug follows a real word, not a loan look-alike that is no headword
    assert choose_vowels("librug", _lex(freq={"libro": 210}, es=["libro"])) == ("librog", "unknown")


#: The analyser's ties and nasal prefixes (tunatale-h0q3.30). Each obscure headword
#: here really is one of Wolff's (naku 'oh my', pangus, himul ...), and each won
#: its word before: o landed in a syllable that is not the root's last.
_ROOT_SPLIT_LEX = {
    "entries": {
        "kuha": "take",
        "naku": "oh my",
        "duha": "two",
        "mudu": "x",
        "pangu": "x",
        "kusi": "kick",
        "pangus": "x",
        "buhi": "alive",
        "nimu": "x",
        "bulag": "separate",
        "himul": "x",
        "lubi": "coconut",
        "abut": "reach",
        "ku": "I",
        "pisti": "plague",
    },
    "freq": {
        "kuha": 8364,
        "naku": 110,
        "duha": 14855,
        "pangu": 244,
        "kusi": 7,
        "pangus": 128,
        "buhi": 1718,
        "nimo": 4499,
        "bulag": 305,
        "lubi": 265,
        "abot": 900,
        "ko": 90000,
    },
    "es": ["peste"],
}


@pytest.mark.parametrize(
    ("word", "vowels"),
    [
        ("nakuha", "nakuha"),  # na- + kuha, not naku + -ha: fewer suffix letters wins the tie
        ("muduha", "moduha"),  # mu- + duha; the prefix mu- is written mo-
        ("panguha", "panguha"),  # paN- + kuha (N + k -> ng), not pangu + -ha
        ("pangusi", "pangusi"),  # paN- + kusi, not pangus + -i
        ("manimuhi", "manimuhi"),  # paniN- + buhi (N + b -> m), not nimu + -hi
        ("mahimulag", "mahimulag"),  # mahiN- + bulag, not himul + -a + ug
        ("kalubinhan", "kalubinhan"),  # ka- + lubi + -nhan
        ("kuha", "kuha"),  # a bare headword beats ku + -ha, however common ku is
        ("abutun", "aboton"),  # control: the root's o survives a suffix
    ],
)
def test_the_root_split_puts_o_only_in_the_roots_last_syllable(word, vowels):
    assert choose_vowels(word, _lex(**_ROOT_SPLIT_LEX)) == (vowels, "native")


def test_a_nasal_prefix_never_restores_a_loan_look_alike():
    # pam + (p)isti would read as Spanish peste and write paméste: a guess, so no split.
    assert choose_vowels("pamisti", _lex(**_ROOT_SPLIT_LEX)) == ("pamisti", "unknown")


def test_words_without_vowels_and_stray_marks():
    lex = _lex()
    assert handbook_spelling("ng", lex).text == "ng"
    assert handbook_spelling("́bir", lex).text == "bir"


# --- where the two books spell one word differently, the course wins (owner, 2026-10-18) -------


def test_the_course_spelling_wins_over_the_converted_one():
    lex = build_lexicon(
        freq={},
        entries={},
        es_words=[],
        en_sounds=[],
        english_common=set(),
        course_spellings={"sígi": "sigé", "unyà": "unyâ"},
    )
    assert handbook_spelling("sígi", lex) == ("sigé", "course")
    assert handbook_spelling("Sígi", lex).text == "Sigé"
    assert handbook_spelling("unyà", lex).text == "unyâ"
    assert handbook_spelling("unya", lex).text == "únya"  # that spelling only, not its letters


def test_the_committed_course_spellings():
    table = load_course_spellings()
    assert table["sígi"] == "sigé"
    assert table["dúdung"] == "dódong"
    # every row is a real override: the converter alone would spell it otherwise
    lex = build_lexicon(freq={}, entries={}, es_words=[], en_sounds=[], english_common=set())
    for wolff, course in table.items():
        assert handbook_spelling(wolff, lex).text != course, wolff


# --- example sentences ------------------------------------------------------------------------


def test_a_sentence_converts_word_by_word_keeping_punctuation(lex):
    assert convert_text("Maáyu ning libru, tan-aw!", lex) == "Màáyo ning líbro, tán-aw!"
    assert convert_text("Náa ba dihà ang yáwi?", lex) == "Nâa ba dihâ ang yáwi?"


def test_names_mid_sentence_are_left_as_written(lex):
    # Wolff's examples name people (Piyuks, Hwan); their spelling is not his to respell
    assert convert_text("Unsay ímung natan-awan ni Piyuks?", lex) == "Únsay ímong natan-awán ni Piyuks?"
