"""Starter cards from a related language (u8nz.7): find, discount, spread, mint, seed.

Every oracle here is a literal the design fixed before the code existed:

* cognate = same spelling + a shared content word in the English gloss;
  near-cognate = within ``near_cognate_distance`` edits + a shared gloss word;
  same spelling with no shared gloss word = false friend, never minted.
* discounts: cognate x0.5 both directions, near-cognate recognition x0.25 and
  production NOT seeded; only a REVIEW/KNOWN source direction with stability
  >= 21 days carries over; capped at 60 days.
* spread: due from day 1, at most ``per_day`` a day, siblings on different days,
  every direction strictly inside its interval.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime

import pytest

from app.models.srs_item import Direction, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs import cognate_seed as cs
from app.srs.database import SRSDatabase

REC, PROD = Direction.RECOGNITION, Direction.PRODUCTION


def _entry(word: str, *glosses: str) -> str:
    return json.dumps({"word": word, "lang_code": "ceb", "senses": [{"glosses": [g]} for g in glosses]})


DICTIONARY = cs.Dictionary(
    cs.load_dictionary(
        [
            _entry("tubig", "water"),
            _entry("balay", "house; home"),
            _entry("gabii", "night; evening"),
            _entry("Amerikana", "American woman"),
            _entry("asawa", "spouse; wife; husband"),
            _entry("tulo", "three"),
            _entry("mahal", "expensive"),
            # Near-cognate DECOYS measured in the first live dry run (2026-09-26):
            # a spelling one edit away sharing ONE word with a long gloss.
            _entry("kainom", "a person whom one is having a drink with"),
            _entry("agaw", "a cousin", "to take away from someone's possession; to usurp"),
            _entry("haya", "a wake; a period after a person's death"),
            _entry("paliya", "Momordica charantia, a vine in the family Cucurbitaceae; bitter melon"),
            _entry("taas", "long; tall; high"),
            _entry("kakita", "able to see"),
            _entry("iningles", "English (language)"),
            "",
            json.dumps({"lang_code": "ceb", "senses": []}),  # a headless row is skipped
        ]
    )
)


def _word(text: str, translation: str, rec: float | None = 40.0, prod: float | None = 30.0, **kw) -> cs.KnownWord:
    state = kw.get("state", SRSState.REVIEW)
    dirs = {}
    if rec is not None:
        dirs[REC] = cs.KnownDirection(state, rec, kw.get("difficulty", 4.2))
    if prod is not None:
        dirs[PROD] = cs.KnownDirection(state, prod, kw.get("difficulty", 4.2))
    return cs.KnownWord(text, translation, dirs)


class TestGloss:
    def test_function_words_do_not_make_glosses_match(self):
        assert not cs.glosses_compatible("to go", ["to eat"])

    def test_a_shared_content_word_does(self):
        assert cs.glosses_compatible("the house", ["house; home"])

    def test_one_and_two_letter_fragments_are_not_words(self):
        assert not cs.glosses_compatible("that’s why", ["a person's death"])

    def test_equivalent_glosses_allow_one_extra_word_either_way(self):
        assert cs.glosses_equivalent("to see", ["able to see"])
        assert cs.glosses_equivalent("American (f)", ["American woman"])
        assert not cs.glosses_equivalent("whom", ["a person whom one is having a drink with"])
        assert not cs.glosses_equivalent("(f)", ["f"])

    def test_plurals_match_their_singular(self):
        assert cs.glosses_compatible("houses", ["house"])


class TestClassify:
    def test_same_spelling_and_meaning_is_a_cognate(self):
        m = DICTIONARY.classify(_word("Tubig!", "water"))
        assert (m.relation, m.target_text) == (cs.Relation.COGNATE, "Tubig")

    def test_a_close_spelling_with_the_meaning_is_a_near_cognate(self):
        m = DICTIONARY.classify(_word("bahay", "house"))
        assert (m.relation, m.target_text) == (cs.Relation.NEAR_COGNATE, "balay")

    def test_same_spelling_different_meaning_is_a_false_friend(self):
        m = DICTIONARY.classify(_word("Amerikana", "coat"))
        assert m.relation is cs.Relation.FALSE_FRIEND
        assert m.target_glosses == ("American woman",)

    @pytest.mark.parametrize(
        ("text", "gloss"),
        [
            ("Kanino?", "Whom?"),  # kainom: "a person WHOM one is having a drink with"
            ("agad", "right away"),  # agaw: "to take AWAY from someone's possession"
            ("kaya", "therefore / that’s why"),  # haya: only the "s" of "that’s" and "person's"
            ("pamilya", "family"),  # paliya: "a vine in the FAMILY Cucurbitaceae"
        ],
    )
    def test_a_near_cognate_needs_the_gloss_not_one_shared_word(self, text, gloss):
        """A near-cognate's spelling is weak evidence, so its meaning must be
        near-identical: some gloss segment equal to the translation, give or take
        one word. The loose shared-word test let eight coincidences through."""
        assert DICTIONARY.classify(_word(text, gloss)).relation is cs.Relation.UNRELATED

    @pytest.mark.parametrize(
        ("text", "gloss", "target"),
        [("mataas", "high", "taas"), ("makita", "to see", "kakita"), ("Ingles", "English", "iningles")],
    )
    def test_a_segment_of_the_gloss_is_enough(self, text, gloss, target):
        m = DICTIONARY.classify(_word(text, gloss))
        assert (m.relation, m.target_text) == (cs.Relation.NEAR_COGNATE, target)

    def test_two_edits_in_five_letters_is_too_far(self):
        """tatlo/tulo both mean three, but the learner cannot tell that from chance."""
        assert DICTIONARY.classify(_word("tatlo", "three")).relation is cs.Relation.UNRELATED

    def test_a_word_with_no_counterpart_is_unrelated(self):
        m = DICTIONARY.classify(_word("kumain", "to eat"))
        assert (m.relation, m.target_text) == (cs.Relation.UNRELATED, None)

    @pytest.mark.parametrize(
        ("word", "limit"), [("ab", 0), ("gabi", 1), ("bahay", 1), ("salamat", 2), ("pagkakaibigan", 2)]
    )
    def test_near_cognate_distance(self, word, limit):
        assert cs.near_cognate_distance(word) == limit

    def test_edit_distance(self):
        assert cs.edit_distance("bahay", "balay") == 1
        assert cs.edit_distance("gabi", "gabii") == 1
        assert cs.edit_distance("", "abc") == 3


class TestSeedFor:
    def test_a_cognate_carries_half_of_each_direction(self):
        assert cs.seed_for(cs.Relation.COGNATE, REC, cs.KnownDirection(SRSState.REVIEW, 40.0, 4.2)) == (20.0, 4.2)
        assert cs.seed_for(cs.Relation.COGNATE, PROD, cs.KnownDirection(SRSState.KNOWN, 30.0, None)) == (15.0, 5.0)

    def test_a_near_cognate_carries_a_quarter_of_recognition_and_no_production(self):
        known = cs.KnownDirection(SRSState.REVIEW, 40.0, 4.2)
        assert cs.seed_for(cs.Relation.NEAR_COGNATE, REC, known) == (10.0, 4.2)
        assert cs.seed_for(cs.Relation.NEAR_COGNATE, PROD, known) is None

    def test_the_head_start_is_capped(self):
        known = cs.KnownDirection(SRSState.KNOWN, 36500.0, 1.0)
        assert cs.seed_for(cs.Relation.COGNATE, REC, known) == (60.0, 1.0)

    @pytest.mark.parametrize(
        "known",
        [
            None,
            cs.KnownDirection(SRSState.REVIEW, 20.9, 4.0),
            cs.KnownDirection(SRSState.LEARNING, 40.0, 4.0),
            cs.KnownDirection(SRSState.NEW, 40.0, 4.0),
            cs.KnownDirection(SRSState.SUSPENDED, 40.0, 4.0),
            cs.KnownDirection(SRSState.REVIEW, None, 4.0),
        ],
    )
    def test_only_genuinely_known_directions_carry_over(self, known):
        assert cs.seed_for(cs.Relation.COGNATE, REC, known) is None

    def test_nothing_carries_for_other_relations(self):
        known = cs.KnownDirection(SRSState.REVIEW, 40.0, 4.0)
        assert cs.seed_for(cs.Relation.FALSE_FRIEND, REC, known) is None


class TestSchedule:
    def _starters(self, n: int, per_day: int = 10) -> list[cs.Starter]:
        matches = [
            cs.Match(_word(f"w{i:03}", "water", rec=40.0, prod=30.0), cs.Relation.COGNATE, f"w{i:03}") for i in range(n)
        ]
        starters, unplaced = cs.schedule(matches, per_day=per_day)
        assert unplaced == 0
        return starters

    def test_no_day_is_a_wall(self):
        starters = self._starters(40)
        load = Counter(s.offset_days for st in starters for s in st.seeds.values())
        assert sum(load.values()) == 80
        assert max(load.values()) == 10
        assert min(load) == 1  # nothing due today: today's queue is already built

    def test_siblings_fall_due_on_different_days(self):
        for st in self._starters(40):
            assert st.seeds[REC].offset_days != st.seeds[PROD].offset_days

    def test_siblings_with_the_same_interval_still_split(self):
        match = cs.Match(_word("tubig", "water", rec=40.0, prod=40.0), cs.Relation.COGNATE, "tubig")
        [starter], _ = cs.schedule([match])
        assert {d: s.offset_days for d, s in starter.seeds.items()} == {REC: 1, PROD: 2}

    def test_every_direction_is_due_strictly_inside_its_interval(self):
        for st in self._starters(40):
            for s in st.seeds.values():
                assert 1 <= s.offset_days < round(s.stability)

    def test_short_intervals_are_placed_first(self):
        """A near-cognate's 10-day interval must not be crowded out by cognates
        that could wait: in list order, 100 cognate directions would fill days 1-10
        at ten a day and leave it nothing inside its 9 usable days."""
        cognates = [cs.Match(_word(f"w{i:03}", "water"), cs.Relation.COGNATE, f"w{i:03}") for i in range(50)]
        near = cs.Match(_word("bahay", "house", rec=40.0, prod=None), cs.Relation.NEAR_COGNATE, "balay")
        starters, unplaced = cs.schedule([*cognates, near], per_day=10)
        assert unplaced == 0
        assert starters[-1].seeds[REC].offset_days <= 9

    def test_a_direction_with_no_free_day_stays_new(self):
        near = [
            cs.Match(_word(f"n{i}", "house", rec=21.0, prod=None), cs.Relation.NEAR_COGNATE, f"n{i}") for i in range(5)
        ]
        # 21 x 0.25 = 5.25 → a 5-day interval → days 1-4 only; one a day fits four.
        starters, unplaced = cs.schedule(near, per_day=1)
        assert unplaced == 1
        assert [bool(st.seeds) for st in starters] == [True, True, True, True, False]


class TestPlan:
    def test_the_plan_sorts_classifies_and_deduplicates(self):
        words = [
            _word("bahay", "house", rec=90.0),
            _word("tubig", "water", rec=30.0, prod=None),
            _word("Tubig", "water (drink)", rec=22.0, prod=None),
            _word("Amerikana", "coat"),
            _word("asawa", "wife", rec=None, prod=None),
            _word("kumain", "to eat"),
            _word("gabi", "night", rec=10.0, prod=10.0),
        ]
        p = cs.plan(words, DICTIONARY)
        assert [(st.match.target_text, st.match.relation) for st in p.starters] == [
            ("tubig", cs.Relation.COGNATE),
            ("asawa", cs.Relation.COGNATE),
            ("balay", cs.Relation.NEAR_COGNATE),
            ("gabii", cs.Relation.NEAR_COGNATE),
        ]
        assert [m.word.translation for m in p.duplicates] == ["water (drink)"]
        assert [m.word.text for m in p.false_friends] == ["Amerikana"]
        assert p.unrelated == 1
        seeded = {st.match.target_text: set(st.seeds) for st in p.starters}
        # asawa and gabii are minted as NEW: the learner does not know the source well.
        assert seeded == {"tubig": {REC}, "asawa": set(), "balay": {REC}, "gabii": set()}


class TestMintAndSeed:
    """The wiring: known words in, NEW cards minted, minted cards seeded."""

    def _source(self) -> SRSDatabase:
        db = SRSDatabase(":memory:")
        for text, gloss in [
            ("tubig", "water"),
            ("bahay", "house"),
            ("kumain", "to eat"),
            ("salamat", "thanks"),
            ("asawa", "wife"),
        ]:
            db.add_collocation(SyntacticUnit(text, gloss, 1, 1, "test"), "tl")
        db.add_collocation(SyntacticUnit("magandang umaga", "good morning", 2, 1, "test"), "tl")
        for text in ("tubig", "bahay"):
            item = db.get_collocation(text)
            for d in (REC, PROD):
                ds = item.directions[d]
                ds.state, ds.stability, ds.difficulty, ds.reps = SRSState.REVIEW, 40.0, 4.2, 3
                db.update_direction(item.guid, d, ds)
        return db

    def test_known_words_are_the_single_word_vocab_cards(self):
        words = {w.text: w for w in cs.known_words(self._source())}
        assert set(words) == {"tubig", "bahay", "kumain", "salamat", "asawa"}
        assert words["tubig"].directions[REC] == cs.KnownDirection(SRSState.REVIEW, 40.0, 4.2)

    def test_mint_then_sync_then_seed(self):
        p = cs.plan(cs.known_words(self._source()), DICTIONARY)
        target = SRSDatabase(":memory:")

        # asawa is a cognate the learner has not studied: minted, never seeded.
        assert cs.mint(target, p.starters, language_code="ceb", source_name="Tagalog") == 3
        assert cs.mint(target, p.starters, language_code="ceb", source_name="Tagalog") == 0  # idempotent
        tubig = target.get_collocation("tubig")
        assert tubig.syntactic_unit.translation == "water"
        assert tubig.syntactic_unit.note == "Tagalog cognate: tubig"
        assert tubig.directions[REC].state == SRSState.NEW

        now = datetime.now(UTC)
        # Before the sync mints them in Anki, nothing can be seeded.
        before = cs.seed(target, p.starters, language_code="ceb", now=now)
        assert (before.seeded, before.not_minted) == (0, 3)

        for i, text in enumerate(("tubig", "balay")):
            target.set_anki_ids(target.get_collocation(text).guid, 100 + i, {REC: 1000 + 2 * i, PROD: 1001 + 2 * i})
        after = cs.seed(target, p.starters, language_code="ceb", now=now)
        assert (after.seeded, after.not_minted, after.already_started) == (3, 0, 0)
        tubig = target.get_collocation("tubig")
        assert {d: tubig.directions[d].state for d in (REC, PROD)} == {REC: SRSState.REVIEW, PROD: SRSState.REVIEW}
        assert tubig.directions[REC].stability == 20.0
        balay = target.get_collocation("balay")
        assert (balay.directions[REC].state, balay.directions[REC].stability) == (SRSState.REVIEW, 10.0)
        assert balay.directions[PROD].state == SRSState.NEW
        assert target.get_collocation("asawa").directions[REC].state == SRSState.NEW

        again = cs.seed(target, p.starters, language_code="ceb", now=now)
        assert (again.seeded, again.already_started) == (0, 3)
