"""The Wolff lexicon builder (tunatale-h0q3.2): its filters and its coverage report.

The real build reads the dictionary from ~/Books; the committed lexicon is the
oracle exercised by ``test_ceb_wolff_cards.py::test_the_committed_lexicon``.
"""

from __future__ import annotations

from app.plugins.languages.ceb.wolff_cards import WolffLexicon
from scripts.build_cebuano_wolff_lexicon import deck_words, is_entry, report


def test_appendix_headings_are_not_entries():
    assert is_entry("tulu numeral three.") is True
    assert is_entry("BIRDS") is False


def test_the_deck_lists_are_the_625_words_and_the_reviewed_next_words():
    words = deck_words()
    assert ("kini", "this (near me)") in words
    assert len(words) > 600
    assert all(ceb and en for ceb, en in words)


def test_report_counts_matches_and_lists_misses():
    rec = {
        "id": "bir",
        "heads": ["bir"],
        "text": "bir n beer.",
        "forms": [
            {
                "label": "bir",
                "wolff": "bir",
                "hb": "bir",
                "short": [],
                "xref": None,
                "senses": [{"pos": "n", "n": None, "cls": None, "gloss": "beer", "phrase": None, "ex": None}],
            }
        ],
    }
    out = report(WolffLexicon([rec]), [("bir", "beer"), ("kompyuter", "computer")])
    assert out.splitlines()[0] == "deck words: 2; matched 1 (50.0%)"
    assert "not-in-dictionary: 1" in out
    assert "  kompyuter 'computer'" in out
