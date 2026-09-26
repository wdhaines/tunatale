"""A Fluent Forever style base list as picture cards (u8nz, 2026-09-26).

Oracles fixed before the code: only vouched-for rows mint; an existing card, a
function word, or an unconfirmed row does not; and each card's queue position is
``1_000_000 + 2 * rank + ord`` — after every card already waiting, in list order,
whatever chunk it was minted in.
"""

from __future__ import annotations

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
