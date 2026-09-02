"""The review-vocabulary block and its pressure dial (bd tunatale-ow7t).

Two things are under test and only one of them is prose:

  1. THE EMPTY-SET PROMPT IS UNCHANGED, BYTE FOR BYTE. Story cassettes are keyed
     on sha256(system + user); the epic names silent cassette invalidation as
     this work's failure mode. Every existing story cassette was recorded with
     `(none yet)` in this slot, so the no-review-words prompt must still produce
     exactly that, at every pressure setting.

  2. THE DIAL ACTUALLY MOVES, and the coherence floor does not move with it.
     The floor is the one thing the user made unconditional — "at the cost of
     theme (but not coherence)" — and the obvious way to get it wrong is to
     state it only on the aggressive setting, where the risk is most visible.
"""

import pytest

from app.generation.prompts import COHERENCE_FLOOR, build_review_block
from app.generation.story import build_story_prompts
from app.models.strategy import ContentStrategy, ReviewPressure
from tests.test_story import _make_curriculum_day

# The exact bytes every existing story cassette was recorded with. Written out
# rather than imported from the template, so that editing the template cannot
# quietly move the goalposts this test exists to hold.
EMPTY_SLOT = "**Review Collocations to Include:**\n(none yet)\n"

WORDS = ["sjelden", "å bidra", "likevel"]


@pytest.fixture(params=list(ContentStrategy))
def strategy(request):
    return request.param


class TestEmptySetIsUnchanged:
    @pytest.mark.parametrize("pressure", list(ReviewPressure))
    def test_no_review_words_renders_exactly_the_old_literal(self, language, strategy, pressure):
        user = build_story_prompts(
            _make_curriculum_day(), language, strategy, "A2", review_pressure=pressure
        ).user_prompt
        assert EMPTY_SLOT in user

    def test_pressure_leaks_nothing_when_there_is_nothing_to_review(self, language, strategy):
        """The dial must be invisible with an empty set — otherwise the pressure
        parameter alone would re-key every cassette recorded before it existed."""
        prompts = {
            build_story_prompts(_make_curriculum_day(), language, strategy, "A2", review_pressure=p)[1]
            for p in ReviewPressure
        }
        assert len(prompts) == 1


class TestTheBlockCarriesTheWords:
    """Renderer-level only. That the words reach the prompt from the DB, at both
    call sites and in the right language, is `test_review_wiring.py`'s job — this
    class stays pure so a DB or routing failure cannot masquerade as a wording
    failure here."""

    def test_words_appear_in_the_order_given(self):
        block = build_review_block(WORDS)
        positions = [block.index(w) for w in WORDS]
        assert positions == sorted(positions), "the selector's urgency order must survive rendering"

    def test_each_word_is_its_own_bullet(self):
        block = build_review_block(WORDS)
        for word in WORDS:
            assert f"- {word}" in block


class TestTheDial:
    @pytest.mark.parametrize("pressure", list(ReviewPressure))
    def test_every_setting_states_the_coherence_floor(self, pressure):
        """The floor is unconditional. Stating it only on the aggressive setting
        is the failure this test exists for — that is where the risk is most
        visible, which is exactly why it gets remembered there and nowhere else."""
        assert COHERENCE_FLOOR in build_review_block(WORDS, pressure)

    def test_the_three_settings_say_different_things(self):
        blocks = {build_review_block(WORDS, p) for p in ReviewPressure}
        assert len(blocks) == len(ReviewPressure), "a dial whose settings render alike is not a dial"

    def test_natural_licenses_omission(self):
        """The user's low end is today's thematic unity, and "how/IF to
        incorporate" is the load-bearing word: a setting that cannot decline is
        not the low end of anything."""
        block = build_review_block(WORDS, ReviewPressure.NATURAL).lower()
        assert "candidates, not requirements" in block
        assert "including none of them" in block

    def test_insistent_says_theme_may_give_way(self):
        block = build_review_block(WORDS, ReviewPressure.INSISTENT).lower()
        assert "theme" in block
        assert COHERENCE_FLOOR in build_review_block(WORDS, ReviewPressure.INSISTENT)

    def test_natural_is_the_default(self):
        """Today's behaviour is the low end, so an unspecified call must not
        silently start bending stories toward the review list."""
        assert build_review_block(WORDS) == build_review_block(WORDS, ReviewPressure.NATURAL)

    def test_an_empty_word_list_renders_the_placeholder_at_every_setting(self):
        for pressure in ReviewPressure:
            assert build_review_block([], pressure) == "(none yet)"
