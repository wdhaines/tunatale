"""A Fluent Forever style base list as picture cards (u8nz, 2026-09-26).

Oracles fixed before the code: only vouched-for rows mint; an existing card, a
function word, or an unconfirmed row does not; and each card's queue position is
``1_000_000 + 2 * rank + ord`` — after every card already waiting, in list order,
whatever chunk it was minted in.

List order is the most used word first with themes spread out (2026-10-05): the
thematic order served dog, cat, fish, bird and cow on one day.
"""

from __future__ import annotations

import gzip
from pathlib import Path

import pytest

from app.models.srs_item import Direction, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs import base_list as bl
from app.srs.database import SRSDatabase

TSV = """# a comment line
# another
category\tenglish\tcebuano\tstatus
animal\tdog\tiro\tdictionary
animal\tcat\tiring\tdictionary
location\thotel\thotel\tunconfirmed
beverage\twater\ttubig\tdictionary
misc\tthe\tang\tdictionary
location\tschool\teskwelahan\tvariant
""".splitlines(keepends=True)


def _words() -> list[bl.BaseWord]:
    return bl.load_base_list(TSV)


def test_load_ranks_rows_in_file_order_and_skips_comments():
    words = _words()
    assert [(w.rank, w.text, w.english, w.status) for w in words][:2] == [
        (0, "iro", "dog", "dictionary"),
        (1, "iring", "cat", "dictionary"),
    ]
    assert len(words) == 6
    assert words[5].category == "location"


def test_a_rank_offset_header_starts_the_ranks_there():
    """A second list (the Cebuano frequency seed) must queue BEHIND the first.
    Positions are ``BACK_BASE + 2 * rank``, so without an offset its rank 0
    would land beside the 625 list's rank 0."""
    words = bl.load_base_list(["#rank_offset=1000\n", *TSV])
    assert [w.rank for w in words] == [1000, 1001, 1002, 1003, 1004, 1005]


def test_an_ordinary_comment_is_not_an_offset():
    assert _words()[0].rank == 0


def test_plan_splits_the_list():
    plan = bl.plan_base_list(_words(), have=frozenset({"tubig"}), function_word=lambda w: w == "ang")
    assert [w.text for w in plan.to_add] == ["iro", "iring", "eskwelahan"]
    assert [w.text for w in plan.already_have] == ["tubig"]
    assert [w.text for w in plan.function_words] == ["ang"]
    assert [w.text for w in plan.unconfirmed] == ["hotel"]


def test_an_accepted_unconfirmed_word_mints():
    plan = bl.plan_base_list(_words(), have=frozenset(), function_word=lambda w: False, accept=frozenset({"hotel"}))
    assert "hotel" in [w.text for w in plan.to_add]
    assert plan.unconfirmed == []


def test_mint_adds_new_picture_cards_once():
    db = SRSDatabase(":memory:")
    words = _words()[:2]
    assert bl.mint_base_words(db, words, language_code="ceb", list_name="FF 625") == 2
    assert bl.mint_base_words(db, words, language_code="ceb", list_name="FF 625") == 0
    iro = db.get_collocation("iro")
    assert (iro.syntactic_unit.translation, iro.syntactic_unit.note) == ("dog", "FF 625 · animal")
    assert iro.syntactic_unit.card_type == "vocab"
    assert {d: ds.state for d, ds in iro.directions.items()} == {
        Direction.RECOGNITION: SRSState.NEW,
        Direction.PRODUCTION: SRSState.NEW,
    }


def test_back_positions_follow_rank_and_ord_for_minted_new_cards_only():
    db = SRSDatabase(":memory:")
    words = _words()
    bl.mint_base_words(db, words[:2], language_code="ceb", list_name="FF 625")
    # iring is minted; iro is not yet (no Anki ids) and must be left out.
    iring = db.get_collocation("iring")
    db.set_anki_ids(iring.guid, 7, {Direction.RECOGNITION: 70, Direction.PRODUCTION: 71})
    assert sorted(bl.back_positions(db, words, language_code="ceb")) == [
        (70, bl.BACK_BASE + 2 * 1 + 0),
        (71, bl.BACK_BASE + 2 * 1 + 1),
    ]
    # A card already introduced keeps its place.
    iring = db.get_collocation("iring")
    ds = iring.directions[Direction.RECOGNITION]
    ds.state, ds.reps = SRSState.LEARNING, 1
    db.update_direction(iring.guid, Direction.RECOGNITION, ds)
    assert bl.back_positions(db, words, language_code="ceb") == [(71, bl.BACK_BASE + 3)]


def test_a_card_that_predates_the_list_keeps_its_place():
    """Live, 2026-09-26: the first --position moved 23 cards that were already
    waiting (lola, tita...) into their list slots, behind the list's own cards.
    "After the waiting cards" means only cards the LIST added move."""
    db = SRSDatabase(":memory:")
    db.add_collocation(SyntacticUnit("iring", "cat", 1, 1, "user"), "ceb")
    iring = db.get_collocation("iring")
    db.set_anki_ids(iring.guid, 7, {Direction.RECOGNITION: 70, Direction.PRODUCTION: 71})
    bl.mint_base_words(db, _words()[:2], language_code="ceb", list_name="FF 625")
    assert bl.back_positions(db, _words(), language_code="ceb") == []


def test_a_row_the_user_accepted_mints():
    """The 72 unconfirmed Cebuano rows were accepted on 2026-09-26; the decision
    lives in the data file as status "accepted", not in a flag someone must repeat."""
    words = bl.load_base_list(["category\tenglish\tcebuano\tstatus\n", "location\thotel\thotel\taccepted\n"])
    plan = bl.plan_base_list(words, have=frozenset(), function_word=lambda w: False)
    assert [w.text for w in plan.to_add] == ["hotel"]


def test_a_never_reviewed_card_buried_for_the_day_still_takes_its_slot():
    """Live, 2026-10-05: 15 list cards were buried as siblings of that day's
    reviews. Left out of the plan, they would have come back the next morning at
    their OLD slots, level with the words newly ranked there."""
    db = SRSDatabase(":memory:")
    words = _words()
    bl.mint_base_words(db, words[:2], language_code="ceb", list_name="FF 625")
    iring = db.get_collocation("iring")
    db.set_anki_ids(iring.guid, 7, {Direction.RECOGNITION: 70, Direction.PRODUCTION: 71})
    iring = db.get_collocation("iring")
    recognition, production = iring.directions[Direction.RECOGNITION], iring.directions[Direction.PRODUCTION]
    recognition.state, recognition.reps = SRSState.LEARNING, 1
    production.state, production.bury_kind = SRSState.BURIED, "sched"
    db.update_direction(iring.guid, Direction.RECOGNITION, recognition)
    db.update_direction(iring.guid, Direction.PRODUCTION, production)
    assert bl.back_positions(db, words, language_code="ceb") == [(71, bl.BACK_BASE + 3)]
    # The control: buried after a review it is an introduced card, and stays put.
    production.reps = 1
    db.update_direction(iring.guid, Direction.PRODUCTION, production)
    assert bl.back_positions(db, words, language_code="ceb") == []


def test_a_deck_with_no_anki_is_positioned_by_guid_and_direction():
    """The second learner's deck (2026-10-05): the same list, no Anki ids at all,
    so ``back_positions`` plans nothing for it."""
    db = SRSDatabase(":memory:")
    words = _words()
    bl.mint_base_words(db, words[:2], language_code="ceb", list_name="FF 625")
    assert bl.back_positions(db, words, language_code="ceb") == []  # the control
    iro, iring = db.get_collocation("iro").guid, db.get_collocation("iring").guid
    plan = bl.tt_only_positions(db, words, language_code="ceb")
    assert sorted(plan, key=lambda p: p[2]) == [
        (iro, Direction.RECOGNITION, bl.BACK_BASE),
        (iro, Direction.PRODUCTION, bl.BACK_BASE + 1),
        (iring, Direction.RECOGNITION, bl.BACK_BASE + 2),
        (iring, Direction.PRODUCTION, bl.BACK_BASE + 3),
    ]
    with db._get_conn() as conn:
        assert bl.apply_tt_only_positions(conn, plan) == 4
        assert bl.apply_tt_only_positions(conn, plan) == 0  # nothing left to move
        conn.commit()
    assert db.get_collocation("iring").directions[Direction.PRODUCTION].anki_due == bl.BACK_BASE + 3
    # A started card is left where it is, as in an Anki-backed deck.
    recognition = db.get_collocation("iro").directions[Direction.RECOGNITION]
    recognition.state, recognition.reps = SRSState.LEARNING, 1
    db.update_direction(iro, Direction.RECOGNITION, recognition)
    assert (iro, Direction.RECOGNITION, bl.BACK_BASE) not in bl.tt_only_positions(db, words, language_code="ceb")


def test_a_deck_linked_to_anki_is_refused_by_the_tt_only_plan():
    """Moving only TunaTale's copy would leave Anki serving the old order until
    the next pull put it back."""
    db = SRSDatabase(":memory:")
    words = _words()
    bl.mint_base_words(db, words[:2], language_code="ceb", list_name="FF 625")
    iring = db.get_collocation("iring")
    db.set_anki_ids(iring.guid, 7, {Direction.RECOGNITION: 70, Direction.PRODUCTION: 71})
    with pytest.raises(ValueError, match="iring"):
        bl.tt_only_positions(db, words, language_code="ceb")


def _themed(*rows: tuple[str, str]) -> list[bl.BaseWord]:
    return [bl.BaseWord(rank, category, text, text, "dictionary") for rank, (category, text) in enumerate(rows)]


def _texts(words: list[bl.BaseWord]) -> list[str]:
    return [w.text for w in words]


def test_the_most_used_word_comes_first_and_ranks_follow():
    words = _themed(("animal", "iro"), ("food", "pan"), ("home", "balay"))
    ordered = bl.frequency_order(words, {"iro": 5, "pan": 50, "balay": 500})
    assert [(w.rank, w.text) for w in ordered] == [(0, "balay"), (1, "pan"), (2, "iro")]


def test_counts_are_looked_up_by_the_normalised_word():
    words = _themed(("animal", "iro"), ("language", "Iningles"))
    assert _texts(bl.frequency_order(words, {"iro": 5, "iningles": 50})) == ["Iningles", "iro"]


def test_words_the_corpus_does_not_have_go_last_in_their_old_order():
    words = _themed(("clothing", "bestida"), ("animal", "iro"), ("food", "keso"))
    assert _texts(bl.frequency_order(words, {"iro": 5})) == ["iro", "bestida", "keso"]


def test_no_two_neighbours_share_a_theme_while_another_theme_waits():
    """The complaint: four animals in a row. By count alone iro, iring and isda
    lead; each animal now waits for the most used word of another theme."""
    words = _themed(("animal", "iro"), ("animal", "iring"), ("animal", "isda"), ("food", "pan"), ("home", "balay"))
    counts = {"iro": 90, "iring": 80, "isda": 70, "pan": 60, "balay": 50}
    assert _texts(bl.frequency_order(words, counts)) == ["iro", "pan", "iring", "balay", "isda"]


def test_one_theme_left_at_the_end_simply_runs_out():
    words = _themed(("number", "usa"), ("number", "duha"), ("animal", "iro"), ("number", "tulo"))
    assert _texts(bl.frequency_order(words, {"usa": 9, "duha": 8, "iro": 7, "tulo": 6})) == [
        "usa",
        "iro",
        "duha",
        "tulo",
    ]


def test_ordering_an_ordered_list_changes_nothing():
    words = _themed(("animal", "iro"), ("animal", "iring"), ("food", "keso"), ("food", "pan"), ("home", "balay"))
    counts = {"iro": 90, "iring": 90, "balay": 3}
    once = bl.frequency_order(words, counts)
    assert _texts(once) == ["iro", "balay", "iring", "keso", "pan"]
    assert bl.frequency_order(once, counts) == once


_CEB_DATA = Path(__file__).resolve().parents[1] / "app/plugins/languages/ceb/data"


def _committed_cebuano() -> tuple[list[bl.BaseWord], dict[str, int]]:
    with (_CEB_DATA / "base625.tsv").open(encoding="utf-8") as fh:
        words = bl.load_base_list(fh)
    with gzip.open(_CEB_DATA / "cebuano_frequency.tsv.gz", "rt", encoding="utf-8") as fh:
        counts = bl.load_counts(fh)
    return words, counts


def test_the_committed_cebuano_list_is_in_frequency_order():
    """A row added or moved by hand must go through scripts/order_base_list.py."""
    words, counts = _committed_cebuano()
    assert len(words) == 506 and counts["sa"] > 1_000_000  # both files really loaded
    assert bl.frequency_order(words, counts) == words


def test_the_committed_cebuano_list_never_serves_a_theme_twice_running_before_its_tail():
    """Measured 2026-10-05: 479 same-theme neighbours in thematic order, 4 after,
    all four in the closing run of decade numbers the corpus never uses."""
    words, _ = _committed_cebuano()
    pairs = [i for i, (a, b) in enumerate(zip(words, words[1:], strict=False)) if a.category == b.category]
    assert pairs == [501, 502, 503, 504]
    assert {words[i].category for i in pairs} == {"number"}
