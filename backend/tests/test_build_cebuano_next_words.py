"""The Cebuano "next words" seed list builder (tunatale-u8nz.6).

Frequency counts surfaces, not senses, so the builder only PROPOSES: every row
it writes is ``unconfirmed`` (not mintable) unless the hand-kept review overlay
vouches for it. These tests pin that contract against tiny inputs.
"""

from __future__ import annotations

import pytest

from app.srs.base_list import MINTABLE, load_base_list
from app.srs.lemma_table import Reading
from scripts.build_cebuano_next_words import (
    candidates,
    first_glosses,
    load_review,
    render,
    short_gloss,
)

# ── short_gloss: a card-sized English word from a dictionary gloss ───────────


@pytest.mark.parametrize(
    ("gloss", "upos", "want"),
    [
        ("human being; person", "NOUN", "human being"),
        ("good, nice", "ADJ", "good"),
        ("case (actual event, situation or fact)", "NOUN", "case"),
        ("to give, to provide, to tender", "VERB", "give"),
        ("to walk", "VERB", "walk"),
        # Only a verb loses its "to": a noun gloss that starts with it keeps it.
        ("today", "ADV", "today"),
        ("to-do list", "NOUN", "to-do list"),
        # The 625 list says "dog", not "a dog".
        ("a harvest; the yield of harvesting", "NOUN", "harvest"),
        ("an answer", "NOUN", "answer"),
        ("the government; the body with the power", "NOUN", "government"),
        ("theatre", "NOUN", "theatre"),
        ("another", "ADJ", "another"),
    ],
)
def test_short_gloss(gloss, upos, want):
    assert short_gloss(gloss, upos) == want


# ── first_glosses: the first real sense per (word, UPOS) ─────────────────────


def test_first_glosses_skips_form_of_and_unmapped_pos():
    entries = [
        {"word": "walay", "pos": "verb", "senses": [{"glosses": ["form of wala"], "form_of": [{"word": "wala"}]}]},
        {"word": "tawo", "pos": "noun", "senses": [{"glosses": ["human being; person"]}, {"glosses": ["visitor"]}]},
        {"word": "tawo", "pos": "verb", "senses": [{"tags": ["no-gloss"]}, {"glosses": ["to be born"]}]},
        {"word": "ug", "pos": "conj", "senses": [{"glosses": ["and"]}]},
        {"word": "apan", "pos": "noun", "senses": [{"glosses": ["grasshopper"], "alt_of": [{"word": "x"}]}]},
    ]
    assert first_glosses(entries) == {
        ("tawo", "NOUN"): "human being; person",
        ("tawo", "VERB"): "to be born",
        ("ug", "CCONJ"): "and",
    }


# ── candidates: which frequent lemmas are proposed ────────────────────────────

_READINGS = {
    "tawo": [Reading("NOUN", "tawo", True)],
    "lakaw": [Reading("VERB", "lakaw", True)],
    "naglakaw": [Reading("VERB", "lakaw", True)],
    "ug": [Reading("CCONJ", "ug", True)],
    "davao": [Reading("PROPN", "davao", True)],
    "iring": [Reading("NOUN", "iring", True)],
    "balay": [Reading("NOUN", "balay", True)],
}
_GLOSS = {("tawo", "NOUN"): "person", ("lakaw", "VERB"): "to walk", ("iring", "NOUN"): "cat"}


def _cands(ranked, *, skip=frozenset(), limit=10):
    return candidates(
        ranked,
        readings=lambda s: _READINGS.get(s, []),
        glosses=_GLOSS,
        skip=lambda w: w in skip,
        limit=limit,
    )


def test_candidates_are_content_roots_with_a_gloss_in_frequency_order():
    ranked = ["ug", "davao", "mga", "naglakaw", "tawo", "lakaw", "balay", "iring"]
    # Pinned exactly: a function word (ug), a name (davao), an unknown surface
    # (mga), an affixed FORM (naglakaw — its root is proposed at its own row),
    # and a root with no gloss (balay) are all left out.
    assert _cands(ranked) == [("NOUN", "tawo", "person"), ("VERB", "lakaw", "to walk"), ("NOUN", "iring", "cat")]


def test_candidates_honour_skip_and_limit():
    ranked = ["tawo", "lakaw", "iring"]
    assert _cands(ranked, skip=frozenset({"tawo"}), limit=1) == [("VERB", "lakaw", "to walk")]


# ── review overlay + render ───────────────────────────────────────────────────

_REVIEW = """# cebuano\tenglish\tstatus\tnote
cebuano\tenglish\tstatus\tnote
tawo\tperson\treviewed\t
lakaw\t\tdropped\tfixture: pretend it is a name
"""


def test_load_review_reads_decisions_and_skips_comments():
    assert load_review(_REVIEW.splitlines(keepends=True)) == {
        "tawo": ("person", "reviewed"),
        "lakaw": ("", "dropped"),
    }


def test_load_review_refuses_an_unknown_status():
    """A typo must not silently leave a row unmintable (or worse, mintable)."""
    bad = "cebuano\tenglish\tstatus\tnote\ntawo\tperson\treviewd\t\n"
    with pytest.raises(ValueError, match="reviewd"):
        load_review(bad.splitlines(keepends=True))


def test_render_is_a_base_list_the_seeder_can_load():
    cands = [("NOUN", "tawo", "human being; person"), ("VERB", "lakaw", "to walk"), ("NOUN", "iring", "cat")]
    review = {"tawo": ("person", "reviewed"), "lakaw": ("", "dropped")}
    text = render(cands, review, rank_offset=1000, source="test")
    words = load_base_list(text.splitlines(keepends=True))
    # Dropped rows are gone; a reviewed row mints with the reviewer's English;
    # an unreviewed row is proposed with the short gloss and does NOT mint.
    assert [(w.rank, w.text, w.english, w.status, w.category) for w in words] == [
        (1000, "tawo", "person", "reviewed", "noun"),
        (1001, "iring", "cat", "unconfirmed", "noun"),
    ]
    assert "reviewed" in MINTABLE and "unconfirmed" not in MINTABLE


def test_render_keeps_the_dictionary_gloss_for_the_reviewer():
    text = render([("NOUN", "tawo", "human being; person")], {}, rank_offset=0, source="test")
    assert "human being; person" in text


# ── the committed list and overlay ────────────────────────────────────────────


def _committed():
    from scripts.build_cebuano_next_words import BASE_LIST, OUT, REVIEW

    with BASE_LIST.open(encoding="utf-8") as fh:
        base = load_base_list(fh)
    with OUT.open(encoding="utf-8") as fh:
        nxt = load_base_list(fh)
    with REVIEW.open(encoding="utf-8") as fh:
        review = load_review(fh)
    return base, nxt, review


def test_the_seed_queues_strictly_behind_the_625():
    base, nxt, _ = _committed()
    assert min(w.rank for w in nxt) > max(w.rank for w in base)


def test_the_committed_list_carries_every_review_decision():
    """next_words.tsv is generated: a review edited without a rebuild would
    leave a dropped word proposed, or a fixed gloss unfixed."""
    _, nxt, review = _committed()
    by_word = {w.text: w for w in nxt}
    for word, (english, status) in review.items():
        if status == "dropped":
            assert word not in by_word, word
        elif word in by_word:
            assert (by_word[word].english, by_word[word].status) == (english, "reviewed"), word


def test_only_reviewed_rows_are_mintable():
    _, nxt, _ = _committed()
    assert {w.status for w in nxt} <= {"reviewed", "unconfirmed"}
