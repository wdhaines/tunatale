"""The converter's oracle script (tunatale-h0q3.2).

Pins its parsing and its verdicts against tiny hand-written inputs. The real
inputs (the dictionary, the course pages) live under ``~/Books`` and are not in
the repo; the report they produce is in the PR.
"""

from __future__ import annotations

import collections

from app.plugins.languages.ceb.handbook_spelling import build_lexicon
from scripts.wolff_handbook_oracle import (
    Dictionary,
    compare,
    course_tokens,
    entry_paragraphs,
    headwords,
    letters_key,
    summarize,
)

DICTIONARY = """front matter, with a stray maáyu that is not an entry

=== A ===

áyu v {1a} do s.t. well. ma- a {1} good. Maáyu ning librúha, This book is good. Ang maáyu, The good one.

tuu, túu_1 n right hand.

tulu num three. Tulu ka bátà, Three children.

túlù v drip.

-un suffix forming verbs.

Abángan n Mr. Abangan, a name.

=== B ===

the end

*** END OF THE PROJECT GUTENBERG EBOOK
"""


def _dictionary():
    return Dictionary(entry_paragraphs(DICTIONARY), english={"this", "book", "is", "good", "three"})


def _lex(d):
    return build_lexicon(freq={}, entries=d.entries, es_words=[], en_sounds=[], english_common=set())


def test_entries_are_the_body_paragraphs():
    paras = entry_paragraphs(DICTIONARY)
    assert paras[0].startswith("áyu v")
    assert paras[-1] == "the end"
    assert not any("front matter" in p for p in paras)


def test_headwords_split_variants_drop_numbers_and_skip_affixes():
    assert headwords("tuu, túu_1 n right hand.") == ["tuu", "túu"]
    assert headwords("-un suffix forming verbs.") == []
    assert headwords("áyu v {1a} do s.t. well.") == ["áyu"]


def test_letters_key_ignores_accents_hyphens_and_e_o():
    assert letters_key("Màáyo") == letters_key("maáyu") == "maayu"
    assert letters_key("tán-aw") == "tanaw"


def test_written_forms_leave_out_names_and_english():
    d = _dictionary()
    assert {"Maáyu", "maáyu"} <= d.forms["maayu"]  # sentence-initial is no name
    assert "librúha" in d.forms["libruha"]
    assert "book" not in d.forms.get("buuk", set())
    assert "Abángan" in d.forms["abangan"]  # also a headword, so not taken for a name
    assert "maayu" not in d.heads


def test_course_words_come_from_cebuano_elements_only(tmp_path):
    (tmp_path / "p001.html").write_text(
        '<table class="build"><tr><td class="ceb">màáyo</td><td class="en">good</td></tr></table>\n'
        '<p class="conv"><b>A.</b> Màáyong búntag.</p>\n'
        '<p class="ex"><i class="cebx">Túlò</i> \'drip\' <i class="ipa">tuːloʔ</i></p>\n'
        '<ul class="items"><li data-n="1">Tuló ka bátà.</li></ul>\n'
        '<p class="en">Good morning.</p>\n',
        encoding="utf-8",
    )
    words = course_tokens([tmp_path])
    assert words == collections.Counter(
        {"màáyo": 1, "Màáyong": 1, "búntag": 1, "Túlò": 1, "Tuló": 1, "ka": 1, "bátà": 1}
    )


def test_compare_gives_each_course_word_a_verdict():
    d = _dictionary()
    course = collections.Counter({"Tuló": 3, "ayô": 2, "Màáyo": 2, "tulô": 1, "ug": 5, "tuló": 1})
    rows = {r["course"]: r for r in compare(course, d.forms, _lex(d), d.freq)}
    assert rows["Tuló"]["status"] == "one-of"  # tulu 'three' beside túlù 'drip'
    assert rows["Màáyo"]["status"] == "agree"
    assert rows["ayô"]["status"] == "disagree"  # áyu -> áyo
    assert rows["tulô"]["status"] == "none-of"
    assert "ug" not in rows  # spelled by sense
    assert "tuló" not in rows  # the same word as Tuló, already compared
    heads = {r["course"]: r["status"] for r in compare(course, d.heads, _lex(d), d.freq)}
    assert "Màáyo" not in heads  # maáyu is a sub-entry, not a headword


def test_a_course_word_without_marks_is_reported_apart():
    d = _dictionary()
    rows = compare(collections.Counter({"Maayo": 1, "tuu": 1}), d.forms, _lex(d), d.freq)
    assert [r["status"] for r in rows] == ["course-unmarked", "course-unmarked"]
    lex = build_lexicon(freq={}, entries={"uu": "yes"}, es_words=[], en_sounds=[], english_common=set())
    rows = compare(collections.Counter({"Oo": 1}), {"uu": {"úu"}}, lex, collections.Counter())
    assert rows[0]["status"] == "agree"  # the Handbook's oo carries no mark either


def test_summary_counts_single_and_multiple_spellings():
    rows = [
        {"status": "agree"},
        {"status": "agree"},
        {"status": "disagree"},
        {"status": "one-of"},
        {"status": "none-of"},
        {"status": "course-unmarked"},
    ]
    assert summarize(rows) == (
        "single spelling: 2/3 agree (66.7%); several spellings: course among them 1/2; course leaves it unmarked: 1"
    )
