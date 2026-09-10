"""The gloss pass: a second LLM call that keeps the story call inside Groq's budget.

bd tunatale-yet7. ``dialogue_glosses`` was 58% of the story completion (measured:
3,213 of 5,511 tokens on the 2026-09-02 Norwegian session, 200 entries at 16.1
tokens each), which pushed review-session generation past the free-tier per-request
ceiling and 502'd. The array now comes from its own call and is spliced into the
story JSON before ``build_lesson_from_story`` ever sees it — so the builder, the
stored ``story`` blob, and the paste round-trip are all unchanged in shape.
"""

import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.generation.glossing import (
    build_gloss_prompt,
    dialogue_lines_from_story,
    ensure_dialogue_glosses,
    gloss_max_tokens,
    parse_gloss_array,
)
from app.generation.story import StoryGenerator
from app.llm.client import LLMError
from app.models.curriculum import CurriculumDay
from app.models.strategy import ContentStrategy

pytestmark = pytest.mark.anyio


def _make_curriculum_day() -> CurriculumDay:
    return CurriculumDay(
        day=1,
        title="Ordering Coffee",
        focus="Café vocabulary",
        collocations=["dober dan"],
        learning_objective="Order a coffee",
        story_guidance="Scene at a Ljubljana café",
    )


def _story(include_glosses: bool = False) -> dict:
    data: dict = {
        "title": "Ordering Coffee",
        "key_phrases": [{"phrase": "dober dan", "translation": "good day"}],
        "scenes": [
            {
                "label": "At the Riverside Café",
                "lines": [
                    {"speaker": "female-1", "text": "Dober dan!", "translation": "Good day!"},
                    {"speaker": "male-1", "text": "Prosim kavo.", "translation": "A coffee please."},
                ],
            }
        ],
    }
    if include_glosses:
        data["dialogue_glosses"] = [{"word": "dober", "translation": "good"}]
    return data


_GLOSS_REPLY = json.dumps(
    [
        {"word": "dober", "translation": "good"},
        {"word": "dan", "translation": "day"},
        {"word": "prosim", "translation": "please"},
        {"word": "kavo", "translation": "coffee"},
    ]
)


def _two_call_client(story: dict, gloss_reply: str = _GLOSS_REPLY) -> MagicMock:
    """An LLM whose first completion is the story and whose second is the glosses."""
    client = MagicMock()
    client.complete = AsyncMock(side_effect=[json.dumps(story), gloss_reply])
    return client


class TestDialogueLinesFromStory:
    def test_collects_l2_lines_in_order(self):
        assert dialogue_lines_from_story(_story()) == ["Dober dan!", "Prosim kavo."]

    def test_empty_when_no_scenes(self):
        assert dialogue_lines_from_story({"title": "x"}) == []

    def test_skips_blank_text(self):
        story = _story()
        story["scenes"][0]["lines"].append({"speaker": "male-1", "text": "   "})
        assert dialogue_lines_from_story(story) == ["Dober dan!", "Prosim kavo."]

    def test_survives_a_non_dict_scene(self):
        """Reachable, not defensive decoration: on the import paths this runs BEFORE
        ``validate_story``, so a hand-pasted story's junk arrives here first."""
        story = _story()
        story["scenes"].insert(0, "not a scene")
        assert dialogue_lines_from_story(story) == ["Dober dan!", "Prosim kavo."]

    def test_survives_a_non_dict_line(self):
        story = _story()
        story["scenes"][0]["lines"].insert(0, "not a line")
        assert dialogue_lines_from_story(story) == ["Dober dan!", "Prosim kavo."]


class TestGlossPrompt:
    def test_carries_the_dialogue(self):
        prompt = build_gloss_prompt(["Dober dan!"], "Slovene")
        assert "Dober dan!" in prompt
        assert "Slovene" in prompt

    def test_specifies_the_verbs_only_base_key(self):
        """``base`` feeds verb_base_glosses, which fronts production cards.

        A gloss prompt that omits it would build lessons whose verb cards have no
        infinitive front — silently, since the key is optional everywhere.
        """
        prompt = build_gloss_prompt(["Dober dan!"], "Slovene")
        assert "base" in prompt
        assert "VERBS ONLY" in prompt

    def test_asks_for_single_words_only(self):
        """Multi-word entries are where the model loses its JSON syntax.

        Both live captures of the malformed response broke on a PHRASE entry --
        `{"word":"stemningen i koret er god igjen":...}` and
        `{"word":"tirsdag":"tirsdag"}` -- while every single-word entry in the same
        response was well formed. Pinned for the same reason as the base key above:
        dropping the instruction is silent, and shows up only as glosses that
        quietly went missing a day later.
        """
        prompt = build_gloss_prompt(["Dober dan!"], "Slovene")
        assert "SINGLE word" in prompt


class TestGlossMaxTokensIsSizedFromTheWork:
    """y0bk.5 — the cap is sized from the dialogue, not from what is left.

    Claiming the whole remaining budget made the call reserve ~7,800 of Groq's
    8,000-token-per-minute free-tier bucket (prompt ~900 + cap 6,934), so it could
    only land when nothing else was in flight. It is not: the vocab-media and
    image-query calls run concurrently on the same bucket, and the gloss call 429'd
    twice on 2026-09-08 while they were going.
    """

    def _lines(self, unique_words: int) -> list[str]:
        return [" ".join(f"ord{i}" for i in range(unique_words))]

    def test_reserves_far_less_than_the_whole_bucket(self):
        """A realistic dialogue must leave room for the pipeline beside it.

        ~206 unique words is what the measured 2026-09-08 session had.
        """
        assert gloss_max_tokens(self._lines(206)) < 5500

    def test_still_clears_what_a_real_array_needed(self):
        """MEASURED, not guessed. Live captures: 217 entries -> 2,037 completion
        tokens (9.4/entry) and 241 -> 2,497 (10.4/entry); the pre-split historical
        worst case was 16.1/entry with multi-word entries. The cap must clear the
        worst of those with room, or this trades 429s for truncation — which is
        the failure bd tunatale-yet7 was filed for.
        """
        assert gloss_max_tokens(self._lines(206)) > 206 * 16.1

    def test_never_exceeds_what_the_budget_allows(self):
        """The old ceiling still binds: a dialogue big enough that the work-based
        estimate would exceed the remaining budget is clamped, not allowed through
        to a 413."""
        lines = ["Dober dan, kako ste danes?"] * 400
        prompt_tokens = len(build_gloss_prompt(lines, "Slovene")) // 4
        assert prompt_tokens + gloss_max_tokens(lines) < 8000

    def test_a_tiny_dialogue_still_gets_a_floor(self):
        """One line must not produce a cap so small the array cannot finish."""
        assert gloss_max_tokens(["Hei!"]) >= 256


class TestGlossMaxTokens:
    def test_sized_from_the_free_tier_budget(self):
        """Must clear the 3,213 tokens the measured 200-entry array needed.

        regloss_lessons hardcodes 2048, which would have moved the truncation
        rather than fixing it — the trap this sizing exists to avoid.

        ⚠️ The input changed with y0bk.5 and the reason is worth keeping. It was
        `["Dober dan!"] * 50` — fifty IDENTICAL lines, i.e. two unique words. That
        stood in for "a 200-entry array" only because the old cap was derived from
        the leftover budget, where line count moved the number and vocabulary did
        not. Sizing from the work inverts that: an array has one entry per unique
        WORD, so the old input now correctly asks for a small cap. Two hundred
        distinct words is what a 200-entry array actually looks like.
        """
        assert gloss_max_tokens([" ".join(f"ord{i}" for i in range(200))]) > 3213

    def test_leaves_room_for_the_prompt(self):
        """prompt + completion must stay under Groq's 8000/request reservation."""
        lines = ["Dober dan, kako ste danes?"] * 400
        prompt_tokens = len(build_gloss_prompt(lines, "Slovene")) // 4
        assert prompt_tokens + gloss_max_tokens(lines) < 8000


class TestGeneratorRunsTheGlossPass:
    async def test_second_call_supplies_the_glosses(self, language):
        client = _two_call_client(_story())
        lesson = await StoryGenerator(llm_client=client).generate(
            curriculum_day=_make_curriculum_day(), language=language, strategy=ContentStrategy.WIDER
        )
        assert client.complete.await_count == 2
        glosses = lesson.generation_metadata["token_glosses"]
        assert glosses.get("dober") == "good"
        assert glosses.get("kavo") == "coffee"

    async def test_glosses_land_in_the_stored_story_blob(self, language):
        """The splice happens before the build, so the paste round-trip is unchanged."""
        client = _two_call_client(_story())
        lesson = await StoryGenerator(llm_client=client).generate(
            curriculum_day=_make_curriculum_day(), language=language, strategy=ContentStrategy.WIDER
        )
        stored = lesson.generation_metadata["story"]
        assert [g["word"] for g in stored["dialogue_glosses"]] == ["dober", "dan", "prosim", "kavo"]

    async def test_base_key_reaches_verb_base_glosses(self, language):
        reply = json.dumps([{"word": "prosim", "translation": "please", "base": "ask"}])
        client = _two_call_client(_story(), gloss_reply=reply)
        lesson = await StoryGenerator(llm_client=client).generate(
            curriculum_day=_make_curriculum_day(), language=language, strategy=ContentStrategy.WIDER
        )
        assert lesson.generation_metadata["verb_base_glosses"].get("prosim") == "ask"

    async def test_no_second_call_when_the_story_already_glossed(self, language):
        """The paste route and legacy cassettes carry their own glosses — never re-ask."""
        client = _two_call_client(_story(include_glosses=True))
        lesson = await StoryGenerator(llm_client=client).generate(
            curriculum_day=_make_curriculum_day(), language=language, strategy=ContentStrategy.WIDER
        )
        assert client.complete.await_count == 1
        assert lesson.generation_metadata["token_glosses"].get("dober") == "good"

    async def test_no_second_call_when_there_is_no_dialogue(self, language):
        story = _story()
        story["scenes"] = []
        client = _two_call_client(story)
        await StoryGenerator(llm_client=client).generate(
            curriculum_day=_make_curriculum_day(), language=language, strategy=ContentStrategy.WIDER
        )
        assert client.complete.await_count == 1


class TestEnsureDialogueGlosses:
    """The shared entry point, used by generation and by both paste-import paths."""

    async def test_no_llm_is_a_no_op(self, language):
        """A deployment or test with no LLM on app.state must not 500 an import.

        The attribute lookup at the call sites is a getattr default, so `None`
        reaches here as a normal value rather than an AttributeError.
        """
        story = _story()
        await ensure_dialogue_glosses(story, None, language)
        assert "dialogue_glosses" not in story

    async def test_fills_glosses_in_place(self, language):
        story = _story()
        client = MagicMock()
        client.complete = AsyncMock(return_value=_GLOSS_REPLY)
        await ensure_dialogue_glosses(story, client, language)
        assert [g["word"] for g in story["dialogue_glosses"]] == ["dober", "dan", "prosim", "kavo"]

    async def test_empty_gloss_array_leaves_the_key_absent(self, language, caplog):
        """An empty array is a failed pass, not a story with no words in it."""
        story = _story()
        client = MagicMock()
        client.complete = AsyncMock(return_value="[]")
        await ensure_dialogue_glosses(story, client, language)
        assert "dialogue_glosses" not in story
        assert "no usable entries" in caplog.text


class TestGlossPassRetriesOnce:
    """y0bk.2 — the gloss pass retries once when the first response is empty."""

    async def test_empty_then_good_retries_and_succeeds(self, language, caplog):
        story = _story()
        client = MagicMock()
        client.complete = AsyncMock(side_effect=["[]", '[{"word":"a","translation":"b"}]'])
        await ensure_dialogue_glosses(story, client, language)
        assert client.complete.await_count == 2
        assert story["dialogue_glosses"] == [{"word": "a", "translation": "b"}]
        assert "retrying once" in caplog.text

    async def test_empty_twice_logs_and_degrades(self, language, caplog):
        story = _story()
        client = MagicMock()
        client.complete = AsyncMock(side_effect=["[]", "[]"])
        await ensure_dialogue_glosses(story, client, language)
        assert client.complete.await_count == 2
        assert "dialogue_glosses" not in story
        assert "retry also returned no usable entries" in caplog.text

    async def test_good_first_time_skips_retry(self, language):
        story = _story()
        client = MagicMock()
        client.complete = AsyncMock(return_value='[{"word":"a","translation":"b"}]')
        await ensure_dialogue_glosses(story, client, language)
        assert client.complete.await_count == 1

    async def test_exception_does_not_retry(self, language, caplog):
        story = _story()
        client = MagicMock()
        client.complete = AsyncMock(side_effect=LLMError("boom"))
        await ensure_dialogue_glosses(story, client, language)
        assert client.complete.await_count == 1
        assert "dialogue_glosses" not in story
        assert "Gloss pass failed" in caplog.text


class TestGlossFailureDegrades:
    """A gloss failure must not 502 a story that is already paid for and correct.

    The lesson loses hover translations — degraded, and ``_missing_log`` says so
    loudly — but the expensive, valid story survives. This is the one asymmetry
    worth encoding: the story call cannot be retried cheaply; the gloss call can.
    """

    async def test_unparseable_gloss_reply_still_builds_the_lesson(self, language, caplog):
        client = _two_call_client(_story(), gloss_reply="not json at all")
        lesson = await StoryGenerator(llm_client=client).generate(
            curriculum_day=_make_curriculum_day(), language=language, strategy=ContentStrategy.WIDER
        )
        assert lesson.title == "Ordering Coffee"
        assert lesson.generation_metadata["token_glosses"] == {}
        assert "gloss pass" in caplog.text.lower()

    async def test_gloss_call_raising_still_builds_the_lesson(self, language, caplog):
        client = MagicMock()
        client.complete = AsyncMock(side_effect=[json.dumps(_story()), RuntimeError("groq down")])
        lesson = await StoryGenerator(llm_client=client).generate(
            curriculum_day=_make_curriculum_day(), language=language, strategy=ContentStrategy.WIDER
        )
        assert lesson.title == "Ordering Coffee"
        assert "gloss pass" in caplog.text.lower()


_FIXTURES = Path(__file__).resolve().parent / "fixtures"


class TestParseGlossArrayResilience:
    """parse_gloss_array must survive a malformed entry and recover the rest."""

    def test_malformed_fixture_returns_237_entries(self):
        raw = (_FIXTURES / "gloss_response_malformed.json").read_text(encoding="utf-8")
        result = parse_gloss_array(raw)
        assert len(result) == 237

    def test_wellformed_fixture_returns_235_via_fast_path(self):
        raw = (_FIXTURES / "gloss_response_wellformed.json").read_text(encoding="utf-8")
        result = parse_gloss_array(raw)
        assert len(result) == 235

    def test_fence_wrapped_array_still_parses(self):
        arr = [{"word": "a", "translation": "b"}]
        fenced = "```json\n" + json.dumps(arr) + "\n```"
        assert parse_gloss_array(fenced) == arr

    def test_dialogue_glosses_wrapper_still_parses(self):
        arr = [{"word": "a", "translation": "b"}]
        wrapped = json.dumps({"dialogue_glosses": arr})
        assert parse_gloss_array(wrapped) == arr

    def test_non_dict_members_still_dropped(self):
        raw = json.dumps([{"word": "a", "translation": "b"}, 42, "str", None])
        assert parse_gloss_array(raw) == [{"word": "a", "translation": "b"}]

    def test_unrecoverable_garbage_returns_empty(self):
        assert parse_gloss_array("totally not json {{{") == []

    def test_truncated_response_keeps_the_complete_entries(self):
        """`finish_reason=length` is a live failure here, not a hypothetical.

        The gloss call sizes its own cap, so a cut-off array is exactly the shape
        the epic's TPM bead is about. A truncated tail must cost its own entry and
        nothing else.
        """
        raw = json.dumps([{"word": w, "translation": w.upper()} for w in "abc"])
        assert len(parse_gloss_array(raw[: int(len(raw) * 0.8)])) == 2

    def test_dropped_count_ignores_a_brace_inside_a_translation(self, caplog):
        """The log line is the ONLY signal a partial parse happened — see y0bk.3.

        A raw `text.count("{")` denominator counts braces inside translation
        values, which `raw_decode` swallows as part of a good entry, and reports
        losses that did not occur.
        """
        raw = '[{"word":"a","translation":"the { brace"},{"word":"b":"malformed"},{"word":"c","translation":"d"}]'
        with caplog.at_level(logging.WARNING):
            assert len(parse_gloss_array(raw)) == 2
        assert "dropped 1" in caplog.text


class TestParseGlossArrayStripsMarkup:
    """bd tunatale-vayt: the model can put HTML inside a gloss.

    Seen live on 2026-09-10, recorded verbatim in tests/cassettes/e2e.json
    (sha256:b6897cfc57d5e773): {"word": "vz<span></span>amem", ...}. Kept as-is,
    it becomes a gloss keyed on a word no dialogue contains, and the real word
    goes unglossed with nothing naming the cause. Stripping recovers it.
    """

    def test_the_observed_entry_recovers_its_word(self):
        raw = json.dumps([{"word": "vz<span></span>amem", "translation": "I take", "base": "take"}])
        assert parse_gloss_array(raw) == [{"word": "vzamem", "translation": "I take", "base": "take"}]

    def test_every_text_field_is_stripped(self):
        raw = json.dumps(
            [
                {"word": "<b>hus</b>", "translation": "<i>house</i>"},
                {"word": "spiser", "translation": "eats", "base": "<em>eat</em>"},
                {"word": '<span style="font-style:italic">bok</span>', "translation": "book<br>"},
            ]
        )
        assert parse_gloss_array(raw) == [
            {"word": "hus", "translation": "house"},
            {"word": "spiser", "translation": "eats", "base": "eat"},
            {"word": "bok", "translation": "book"},
        ]

    def test_an_entry_that_was_only_markup_is_dropped(self):
        raw = json.dumps([{"word": "<br>", "translation": "x"}, {"word": "og", "translation": "and"}])
        assert parse_gloss_array(raw) == [{"word": "og", "translation": "and"}]

    def test_angle_brackets_that_are_not_tags_survive(self):
        """The false-positive direction: `<` followed by a space, a digit, or nothing tag-like is text."""
        entries = [
            {"word": "mindre", "translation": "less (<), as in a < b"},
            {"word": "elsker", "translation": "love <3"},
            {"word": "pil", "translation": "arrow -> or <-"},
            # A `<` with a `>` later on the line is where a naive `<[^>]+>` strips text.
            {"word": "mellom", "translation": "between, as in a < b > c"},
            {"word": "fram", "translation": "back <- and forward ->"},
        ]
        assert parse_gloss_array(json.dumps(entries)) == entries

    def test_the_recovery_path_strips_too(self):
        raw = '[{"word":"<b>hus</b>","translation":"house"},{"word":"b":"malformed"}]'
        assert parse_gloss_array(raw) == [{"word": "hus", "translation": "house"}]

    def test_stripping_is_logged_naming_what_changed(self, caplog):
        raw = json.dumps([{"word": "vz<span></span>amem", "translation": "I take"}])
        with caplog.at_level(logging.WARNING):
            parse_gloss_array(raw)
        assert "vz<span></span>amem" in caplog.text
        assert "vzamem" in caplog.text

    def test_clean_input_logs_nothing(self, caplog):
        with caplog.at_level(logging.WARNING):
            parse_gloss_array(json.dumps([{"word": "hus", "translation": "house"}]))
        assert "markup" not in caplog.text.lower()
