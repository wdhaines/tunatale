"""Turning a language's pronoun clozes into picture cards, history kept (tunatale-37c1).

Cebuano ``ko`` and ``ka`` were clozes from 2026-09-28; the owner moved them to
picture-plus-sentence cards on 2026-10-16 and said to keep their review history
(the same call as the number clozes, 2026-09-03). ``mo``, ``ko`` and ``ta`` also
gain a second note, the owner sense keyed ``genitive``.

What these tests are about:
- The selector is DERIVED from the pronoun table (``is_pronoun_word``), never a
  typed list, the lesson of the number migration (three words silently omitted).
- The new subject card has the SAME guid as the cloze it replaces
  (``compute_guid(text, lang, "")``), so it must not be planned while the cloze
  row still exists: the mint would find the old note by guid and link to it.
- History is captured from the cloze's production direction before the delete,
  and written only onto a production direction that already has its Anki card.
- The ta gloss is brought to the table's both-senses gloss (the owner's R2) and
  flagged dirty so the push carries it.

The graves, the TT delete and the inheritance write are the number migration's
own primitives (``migrate_number_clozes``), tested there.
"""

from __future__ import annotations

from app.common.guid import compute_guid
from app.models.srs_item import SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.plugins.anki_sync.convert_pronoun_clozes import (
    gloss_updates,
    inheritance_target,
    plan_new_cards,
    plan_pronoun_clozes,
)
from app.plugins.anki_sync.migrate_number_clozes import delete_tt_clozes
from app.srs.database import SRSDatabase

LANG = "ceb"


def _add(db: SRSDatabase, text: str, *, card_type="vocab", translation="t", disambig="", sentence="") -> int:
    db.add_collocation(
        SyntacticUnit(
            text=text,
            translation=translation,
            word_count=1,
            difficulty=1,
            source="user",
            card_type=card_type,
            disambig_key=disambig,
            source_sentence=sentence,
        ),
        LANG,
    )
    return db.get_collocation_id_by_guid(compute_guid(text, LANG, disambig))


def _grade(db: SRSDatabase, coll_id: int, *, reps: int, state: SRSState, card_id: int | None = None) -> None:
    with db._get_conn() as conn:
        conn.execute(
            "UPDATE collocation_directions SET reps = ?, state = ?, stability = 12.5, anki_card_id = ?"
            " WHERE collocation_id = ? AND direction = 'production'",
            (reps, state.value, card_id, coll_id),
        )
        conn.commit()


def _deck() -> tuple[SRSDatabase, dict[str, int]]:
    """The live shape on 2026-10-16: ko and ka clozes, ta/mo/ako vocab, a content cloze."""
    db = SRSDatabase(":memory:")
    ko = _add(db, "ko", card_type="cloze", translation="I; my, by me", sentence="Asa man ang libro {{c1::ko}}?")
    ka = _add(db, "ka", card_type="cloze", translation="you", sentence="Asa {{c1::ka}} padulong?")
    _grade(db, ko, reps=6, state=SRSState.REVIEW, card_id=55)
    _add(db, "ta", translation="we (including you)")
    _add(db, "mo", translation="you (plural); your (one person)")
    _add(db, "ako", translation="I")
    _add(db, "sa", card_type="cloze", translation="to", sentence="Moadto ko {{c1::sa}} merkado.")
    return db, {"ko": ko, "ka": ka}


class TestPlanPronounClozes:
    def test_selects_the_clozes_whose_word_is_now_a_picture_word(self) -> None:
        db, ids = _deck()
        with db._get_conn() as conn:
            plan = plan_pronoun_clozes(conn, LANG)
        assert [(p.text, p.cloze_id) for p in plan] == [("ka", ids["ka"]), ("ko", ids["ko"])]

    def test_captures_history_and_only_where_there_is_some(self) -> None:
        db, ids = _deck()
        with db._get_conn() as conn:
            plan = {p.text: p for p in plan_pronoun_clozes(conn, LANG)}
        assert plan["ko"].inherited is not None
        assert (plan["ko"].inherited.state, plan["ko"].inherited.reps) == ("review", 6)
        assert plan["ko"].inherited.values["stability"] == 12.5
        assert "anki_card_id" not in plan["ko"].inherited.values, "the old card's identity stays behind"
        assert plan["ka"].inherited is None, "a never-graded cloze carries the schema defaults, not history"

    def test_a_language_without_pronoun_words_plans_nothing(self) -> None:
        db, ids = _deck()
        with db._get_conn() as conn:
            assert plan_pronoun_clozes(conn, "en") == []


class TestPlanNewCards:
    def test_nothing_is_planned_while_a_cloze_holds_the_guid(self) -> None:
        """The new ko card's guid IS the cloze's: planning it now would link the mint to the old note."""
        db, ids = _deck()
        with db._get_conn() as conn:
            units = plan_new_cards(conn, LANG, converted=["ka", "ko"])
        assert ("ko", "") not in {(u.text, u.disambig_key) for u in units}
        assert ("ka", "") not in {(u.text, u.disambig_key) for u in units}

    def test_after_the_retire_step_the_subject_and_owner_cards_are_planned(self) -> None:
        db, ids = _deck()
        with db._get_conn() as conn:
            delete_tt_clozes(conn, [ids["ko"], ids["ka"]])
            conn.commit()
            units = plan_new_cards(conn, LANG, converted=["ka", "ko"])
        assert {(u.text, u.disambig_key, u.translation) for u in units} == {
            ("ko", "", "I; my, by me"),
            ("ka", "", "you (one person)"),
            ("ko", "genitive", "my; by me"),
            ("mo", "genitive", "your; by you (one person)"),
            ("ta", "genitive", "our (including you); by us"),
            # The preposed 'my' (2026-10-18): ako's second sense, beside the deck's ako.
            ("ako", "possessive", "my, mine; by me"),
        }
        assert all(u.card_type == "vocab" and u.lemma == u.text for u in units)

    def test_an_owner_card_needs_its_subject_card(self) -> None:
        """No second sense without a first: a deck with no ta card gets no owner ta."""
        db = SRSDatabase(":memory:")
        _add(db, "mo", translation="you (plural)")
        with db._get_conn() as conn:
            units = plan_new_cards(conn, LANG)
        assert {(u.text, u.disambig_key) for u in units} == {("mo", "genitive")}

    def test_kitas_pronoun_sense_is_planned_beside_the_verb_card(self) -> None:
        """tunatale-z4ya: kita's first card is the verb 'see', and that is enough
        of a first sense for the inclusive 'we' to be planned beside it."""
        db = SRSDatabase(":memory:")
        _add(db, "kita", translation="see")
        with db._get_conn() as conn:
            units = plan_new_cards(conn, LANG)
            glosses = gloss_updates(conn, LANG)
        assert {(u.text, u.disambig_key, u.translation) for u in units} == {("kita", "pronoun", "we (including you)")}
        assert glosses == [], "the verb card's gloss is not the pronoun table's to write"

    def test_a_second_run_plans_nothing(self) -> None:
        db, ids = _deck()
        with db._get_conn() as conn:
            delete_tt_clozes(conn, [ids["ko"], ids["ka"]])
            conn.commit()
            units = plan_new_cards(conn, LANG, converted=["ka", "ko"])
        for unit in units:
            db.add_collocation(unit, LANG)
        with db._get_conn() as conn:
            assert plan_new_cards(conn, LANG, converted=["ka", "ko"]) == []


class TestGlossUpdates:
    def test_a_subject_card_with_a_second_sense_takes_the_both_senses_gloss(self) -> None:
        db, ids = _deck()
        ta = db.get_collocation_id_by_guid(compute_guid("ta", LANG, ""))
        ako = db.get_collocation_id_by_guid(compute_guid("ako", LANG, ""))
        with db._get_conn() as conn:
            assert gloss_updates(conn, LANG) == [
                (ako, "ako", "I", "I; my, mine"),
                (ta, "ta", "we (including you)", "we (including you); our (including you)"),
            ]

    def test_a_word_without_a_second_sense_is_left_to_the_owner(self) -> None:
        """ikaw's gloss is the owner's business: only the R2 words are brought in line."""
        db = SRSDatabase(":memory:")
        _add(db, "ikaw", translation="you")
        with db._get_conn() as conn:
            assert gloss_updates(conn, LANG) == []


class TestInheritanceTarget:
    def _minted(self, db: SRSDatabase, *, card_id: int | None) -> int:
        coll = _add(db, "ko", translation="I; my, by me")
        _grade(db, coll, reps=0, state=SRSState.NEW, card_id=card_id)
        return coll

    def test_the_minted_subject_card_is_the_target(self) -> None:
        db = SRSDatabase(":memory:")
        coll = self._minted(db, card_id=777)
        with db._get_conn() as conn:
            assert inheritance_target(conn, LANG, "ko") == coll

    def test_an_unminted_card_is_not_a_target_yet(self) -> None:
        """Writing review state onto a production direction with no Anki card would
        push nothing and leave TT and Anki disagreeing; the caller syncs again."""
        db = SRSDatabase(":memory:")
        self._minted(db, card_id=None)
        with db._get_conn() as conn:
            assert inheritance_target(conn, LANG, "ko") is None

    def test_the_owner_card_is_never_the_target(self) -> None:
        db = SRSDatabase(":memory:")
        _add(db, "ko", translation="my; by me", disambig="genitive")
        with db._get_conn() as conn:
            assert inheritance_target(conn, LANG, "ko") is None
