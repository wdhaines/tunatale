"""Tests for build_planner_turn_prompt and PLANNER_SYSTEM_PROMPT."""

from app.generation.prompts import PLANNER_SYSTEM_PROMPT, build_planner_turn_prompt
from app.models.curriculum import CurriculumDay

_D16_DAYS = [
    CurriculumDay(
        day=i,
        title=f"Day {i} Title",
        focus=f"Focus {i}",
        collocations=[f"coll_{i}_a", f"coll_{i}_b"],
        learning_objective=f"Objective {i}",
        story_guidance=f"Guidance {i}",
    )
    for i in range(1, 17)
]


class TestPlannerSystemPrompt:
    def test_is_string(self):
        assert isinstance(PLANNER_SYSTEM_PROMPT, str)
        assert len(PLANNER_SYSTEM_PROMPT) > 100

    def test_mentions_json_fence(self):
        assert "```json" in PLANNER_SYSTEM_PROMPT

    def test_mentions_collocations(self):
        assert "collocations" in PLANNER_SYSTEM_PROMPT

    def test_english_language_directive(self):
        assert "Converse in English" in PLANNER_SYSTEM_PROMPT

    def test_collocations_target_language_exception(self):
        assert 'The "collocations" array must contain' in PLANNER_SYSTEM_PROMPT

    def test_english_gloss_requirement(self):
        assert "English gloss" in PLANNER_SYSTEM_PROMPT

    def test_collocations_bare_target_language(self):
        assert "bare target-language phrases only" in PLANNER_SYSTEM_PROMPT


class TestBuildPlannerTurnPrompt:
    def test_golden_prompt(self):
        snapshot = (
            "Learner vocabulary snapshot:\n"
            "- Tracked collocations: 5\n"
            "- Currently learning: 2\n"
            "- New (not yet introduced): 3\n"
            "Known (sample of 2/2): dober_dan, hvala\n"
            "Learning (sample of 1/1): learning_item\n"
            "Struggling: (none yet)"
        )
        feedback = [{"day": 3, "note": "too fast"}, {"day": 5, "note": "perfect level"}]
        chat = [
            {"role": "user", "content": "Let's start planning my trip to Ljubljana"},
            {"role": "planner", "content": "Great idea! Let me help you plan."},
            {"role": "user", "content": "I want to learn about food first"},
            {"role": "planner", "content": "Food is a great starting point!"},
            {"role": "event", "content": "Committed days 1-5."},
            {"role": "user", "content": "Now let's add some sightseeing"},
            {"role": "planner", "content": "Perfect, Ljubljana has wonderful sights."},
            {"role": "user", "content": "Can we add a day about the castle?"},
            {"role": "planner", "content": "Absolutely, the castle is a must-see."},
            {"role": "event", "content": "Committed days 6-10."},
            {"role": "user", "content": "I'd like to practice shopping"},
            {"role": "planner", "content": "Shopping vocabulary is very practical."},
            {"role": "user", "content": "And a day about transportation"},
            {"role": "planner", "content": "Public transport vocab is essential for getting around."},
        ]

        result = build_planner_turn_prompt(
            topic="Exploring Ljubljana",
            cefr_level="A2",
            language_name="Slovene",
            language_code="sl",
            days=_D16_DAYS,
            learner_snapshot=snapshot,
            feedback=feedback,
            chat=chat,
            batch_size=3,
            start_day=17,
        )
        # fmt: off
        expected = (
            "Topic: Exploring Ljubljana\n"
            "CEFR Level: A2\n"
            "Language: Slovene (sl)\n"
            "\n"
            "## Committed Plan\n"
            "\n"
            "Day 1: Day 1 Title\n"
            "Day 2: Day 2 Title\n"
            "\n"
            "Day 3 \u2014 Day 3 Title\n"
            "  Focus: Focus 3\n"
            "  Collocations: coll_3_a, coll_3_b\n"
            "  Learning Objective: Objective 3\n"
            "  Story Guidance: Guidance 3\n"
            "\n"
            "Day 4 \u2014 Day 4 Title\n"
            "  Focus: Focus 4\n"
            "  Collocations: coll_4_a, coll_4_b\n"
            "  Learning Objective: Objective 4\n"
            "  Story Guidance: Guidance 4\n"
            "\n"
            "Day 5 \u2014 Day 5 Title\n"
            "  Focus: Focus 5\n"
            "  Collocations: coll_5_a, coll_5_b\n"
            "  Learning Objective: Objective 5\n"
            "  Story Guidance: Guidance 5\n"
            "\n"
            "Day 6 \u2014 Day 6 Title\n"
            "  Focus: Focus 6\n"
            "  Collocations: coll_6_a, coll_6_b\n"
            "  Learning Objective: Objective 6\n"
            "  Story Guidance: Guidance 6\n"
            "\n"
            "Day 7 \u2014 Day 7 Title\n"
            "  Focus: Focus 7\n"
            "  Collocations: coll_7_a, coll_7_b\n"
            "  Learning Objective: Objective 7\n"
            "  Story Guidance: Guidance 7\n"
            "\n"
            "Day 8 \u2014 Day 8 Title\n"
            "  Focus: Focus 8\n"
            "  Collocations: coll_8_a, coll_8_b\n"
            "  Learning Objective: Objective 8\n"
            "  Story Guidance: Guidance 8\n"
            "\n"
            "Day 9 \u2014 Day 9 Title\n"
            "  Focus: Focus 9\n"
            "  Collocations: coll_9_a, coll_9_b\n"
            "  Learning Objective: Objective 9\n"
            "  Story Guidance: Guidance 9\n"
            "\n"
            "Day 10 \u2014 Day 10 Title\n"
            "  Focus: Focus 10\n"
            "  Collocations: coll_10_a, coll_10_b\n"
            "  Learning Objective: Objective 10\n"
            "  Story Guidance: Guidance 10\n"
            "\n"
            "Day 11 \u2014 Day 11 Title\n"
            "  Focus: Focus 11\n"
            "  Collocations: coll_11_a, coll_11_b\n"
            "  Learning Objective: Objective 11\n"
            "  Story Guidance: Guidance 11\n"
            "\n"
            "Day 12 \u2014 Day 12 Title\n"
            "  Focus: Focus 12\n"
            "  Collocations: coll_12_a, coll_12_b\n"
            "  Learning Objective: Objective 12\n"
            "  Story Guidance: Guidance 12\n"
            "\n"
            "Day 13 \u2014 Day 13 Title\n"
            "  Focus: Focus 13\n"
            "  Collocations: coll_13_a, coll_13_b\n"
            "  Learning Objective: Objective 13\n"
            "  Story Guidance: Guidance 13\n"
            "\n"
            "Day 14 \u2014 Day 14 Title\n"
            "  Focus: Focus 14\n"
            "  Collocations: coll_14_a, coll_14_b\n"
            "  Learning Objective: Objective 14\n"
            "  Story Guidance: Guidance 14\n"
            "\n"
            "Day 15 \u2014 Day 15 Title\n"
            "  Focus: Focus 15\n"
            "  Collocations: coll_15_a, coll_15_b\n"
            "  Learning Objective: Objective 15\n"
            "  Story Guidance: Guidance 15\n"
            "\n"
            "Day 16 \u2014 Day 16 Title\n"
            "  Focus: Focus 16\n"
            "  Collocations: coll_16_a, coll_16_b\n"
            "  Learning Objective: Objective 16\n"
            "  Story Guidance: Guidance 16\n"
            "\n"
            "## Learner Snapshot\n"
            "\n"
            "Learner vocabulary snapshot:\n"
            "- Tracked collocations: 5\n"
            "- Currently learning: 2\n"
            "- New (not yet introduced): 3\n"
            "Known (sample of 2/2): dober_dan, hvala\n"
            "Learning (sample of 1/1): learning_item\n"
            "Struggling: (none yet)\n"
            "\n"
            "## Feedback\n"
            "\n"
            "- Day 3: too fast\n"
            "- Day 5: perfect level\n"
            "\n"
            "## Conversation\n"
            "\n"
            "(... older messages elided ...)\n"
            "USER: I want to learn about food first\n"
            "PLANNER: Food is a great starting point!\n"
            "EVENT: Committed days 1-5.\n"
            "USER: Now let's add some sightseeing\n"
            "PLANNER: Perfect, Ljubljana has wonderful sights.\n"
            "USER: Can we add a day about the castle?\n"
            "PLANNER: Absolutely, the castle is a must-see.\n"
            "EVENT: Committed days 6-10.\n"
            "USER: I'd like to practice shopping\n"
            "PLANNER: Shopping vocabulary is very practical.\n"
            "USER: And a day about transportation\n"
            "PLANNER: Public transport vocab is essential for getting around.\n"
            "\n"
            "If proposing, propose exactly 3 days starting at day 17.\n"
            "Respond in English \u2014 title, focus, learning_objective, and "
            "story_guidance in English \u2014 even though the committed plan and "
            "conversation above are in Slovene. The collocations array "
            "is in Slovene: bare phrases, no English glosses."
        )
        # fmt: on
        assert result == expected

    def test_empty_chat_renders_none_yet(self):
        """Backlog #6: an empty conversation renders '(none yet)', not a blank section."""
        result = build_planner_turn_prompt(
            topic="t",
            cefr_level="A1",
            language_name="T",
            language_code="t",
            days=[],
            learner_snapshot="s",
            feedback=[],
            chat=[],
            batch_size=1,
            start_day=1,
        )
        conversation = result.split("## Conversation")[1].split("If proposing,")[0]
        assert "(none yet)" in conversation

    def test_section_order(self):
        """Sections appear in the fixed order, never reordered."""
        result = build_planner_turn_prompt(
            topic="t",
            cefr_level="A1",
            language_name="T",
            language_code="t",
            days=[],
            learner_snapshot="s",
            feedback=[],
            chat=[],
            batch_size=1,
            start_day=1,
        )
        sections = [
            "Topic:",
            "CEFR Level:",
            "Language:",
            "## Committed Plan",
            "## Learner Snapshot",
            "## Feedback",
            "## Conversation",
            "If proposing,",
        ]
        pos = -1
        for section in sections:
            idx = result.index(section)
            assert idx > pos, f"{section} out of order"
            pos = idx

    def test_14_day_truncation(self):
        """16 days → oldest 2 are title-only, last 14 are full blocks."""
        result = build_planner_turn_prompt(
            topic="t",
            cefr_level="A1",
            language_name="T",
            language_code="t",
            days=_D16_DAYS,
            learner_snapshot="s",
            feedback=[],
            chat=[],
            batch_size=1,
            start_day=1,
        )
        assert "Day 1: Day 1 Title" in result
        assert "Day 2: Day 2 Title" in result
        assert "Day 3 \u2014 Day 3 Title" in result
        assert "Day 16 \u2014 Day 16 Title" in result

    def test_14_days_shows_all_full(self):
        """Exactly 14 days → all full blocks, no title-only lines."""
        days14 = [
            CurriculumDay(
                day=i,
                title=f"D{i}",
                focus=f"F{i}",
                collocations=["a"],
                learning_objective=f"O{i}",
                story_guidance=f"G{i}",
            )
            for i in range(1, 15)
        ]
        result = build_planner_turn_prompt(
            topic="t",
            cefr_level="A1",
            language_name="T",
            language_code="t",
            days=days14,
            learner_snapshot="s",
            feedback=[],
            chat=[],
            batch_size=1,
            start_day=1,
        )
        assert "Day 1 \u2014" in result, "Day 1 should be a full block"
        assert "Day 14 \u2014" in result
        # No older title-only section
        assert result.count("Day 1: D1") == 0

    def test_12_message_truncation(self):
        """14 messages → oldest 2 elided with marker, last 12 shown."""
        chat = [{"role": "user", "content": f"msg{i}"} for i in range(14)]
        result = build_planner_turn_prompt(
            topic="t",
            cefr_level="A1",
            language_name="T",
            language_code="t",
            days=[],
            learner_snapshot="s",
            feedback=[],
            chat=chat,
            batch_size=1,
            start_day=1,
        )
        assert "(... older messages elided ...)" in result
        assert "USER: msg2" in result
        assert "USER: msg13" in result
        assert "USER: msg0\n" not in result
        assert "USER: msg1\n" not in result

    def test_elision_marker_absent_when_12_or_fewer(self):
        """Exactly 12 messages → no elision marker, all shown."""
        chat = [{"role": "user", "content": f"msg{i}"} for i in range(12)]
        result = build_planner_turn_prompt(
            topic="t",
            cefr_level="A1",
            language_name="T",
            language_code="t",
            days=[],
            learner_snapshot="s",
            feedback=[],
            chat=chat,
            batch_size=1,
            start_day=1,
        )
        assert "(... older messages elided ...)" not in result
        assert "USER: msg0" in result
        assert "USER: msg11" in result

    def test_empty_everything(self):
        """All-empty inputs produce a well-formed prompt."""
        result = build_planner_turn_prompt(
            topic="test",
            cefr_level="A1",
            language_name="T",
            language_code="t",
            days=[],
            learner_snapshot="s",
            feedback=[],
            chat=[],
            batch_size=5,
            start_day=1,
        )
        assert "(none yet)" in result
        assert "(none)" in result
        assert "If proposing, propose exactly 5 days starting at day 1." in result

    def test_feedback_sorted_by_day(self):
        """Feedback entries are sorted by day regardless of input order."""
        days = [
            CurriculumDay(day=3, title="t", focus="f", collocations=["a"], learning_objective="o"),
            CurriculumDay(day=5, title="t", focus="f", collocations=["a"], learning_objective="o"),
        ]
        feedback_a = [{"day": 5, "note": "b"}, {"day": 3, "note": "a"}]
        r_a = build_planner_turn_prompt(
            topic="t",
            cefr_level="A1",
            language_name="T",
            language_code="t",
            days=days,
            learner_snapshot="s",
            feedback=feedback_a,
            chat=[],
            batch_size=1,
            start_day=1,
        )
        assert "- Day 3: a" in r_a
        assert "- Day 5: b" in r_a
        day3_idx = r_a.index("- Day 3: a")
        day5_idx = r_a.index("- Day 5: b")
        assert day3_idx < day5_idx

    def test_determinism(self):
        """Same inputs produce identical output."""
        args = dict(
            topic="t",
            cefr_level="A1",
            language_name="T",
            language_code="t",
            days=_D16_DAYS,
            learner_snapshot="snapshot",
            feedback=[{"day": 3, "note": "a"}, {"day": 1, "note": "b"}],
            chat=[{"role": "user", "content": "hi"}, {"role": "planner", "content": "hello"}],
            batch_size=3,
            start_day=5,
        )
        r1 = build_planner_turn_prompt(**args)
        r2 = build_planner_turn_prompt(**args)
        assert r1 == r2

    def test_determinism_reordered_feedback(self):
        """Non-semantic reordering of feedback yields same output."""
        base = dict(
            topic="t",
            cefr_level="A1",
            language_name="T",
            language_code="t",
            days=[
                CurriculumDay(day=3, title="t", focus="f", collocations=["a"], learning_objective="o"),
                CurriculumDay(day=5, title="t", focus="f", collocations=["a"], learning_objective="o"),
            ],
            learner_snapshot="s",
            chat=[],
            batch_size=1,
            start_day=1,
        )
        fb_a = [{"day": 5, "note": "b"}, {"day": 3, "note": "a"}]
        fb_b = [{"day": 3, "note": "a"}, {"day": 5, "note": "b"}]
        r_a = build_planner_turn_prompt(**base, feedback=fb_a)
        r_b = build_planner_turn_prompt(**base, feedback=fb_b)
        assert r_a == r_b

    def test_determinism_reordered_chat(self):
        """Chat order IS semantic — only list identity matters, not reordering."""
        base = dict(
            topic="t",
            cefr_level="A1",
            language_name="T",
            language_code="t",
            days=[],
            learner_snapshot="s",
            feedback=[],
            batch_size=1,
            start_day=1,
        )
        chat_a = [{"role": "user", "content": "a"}, {"role": "planner", "content": "b"}]
        chat_b = [{"role": "planner", "content": "b"}, {"role": "user", "content": "a"}]
        r_a = build_planner_turn_prompt(**base, chat=chat_a)
        r_b = build_planner_turn_prompt(**base, chat=chat_b)
        assert r_a != r_b  # order matters in chat

    def test_proposal_json_not_replayed(self):
        """PLANNER messages in chat are prose-only, so no JSON appears in transcript."""
        chat = [
            {"role": "planner", "content": "Here are some ideas for your trip"},
            {"role": "user", "content": "Great! Can you propose?"},
        ]
        result = build_planner_turn_prompt(
            topic="t",
            cefr_level="A1",
            language_name="T",
            language_code="t",
            days=[],
            learner_snapshot="s",
            feedback=[],
            chat=chat,
            batch_size=1,
            start_day=1,
        )
        assert "Here are some ideas for your trip" in result
        assert '{"days"' not in result

    def test_empty_days_shows_none_yet(self):
        result = build_planner_turn_prompt(
            topic="t",
            cefr_level="A1",
            language_name="T",
            language_code="t",
            days=[],
            learner_snapshot="s",
            feedback=[],
            chat=[],
            batch_size=1,
            start_day=1,
        )
        assert "(none yet)" in result

    def test_empty_feedback_shows_none(self):
        result = build_planner_turn_prompt(
            topic="t",
            cefr_level="A1",
            language_name="T",
            language_code="t",
            days=[],
            learner_snapshot="s",
            feedback=[],
            chat=[],
            batch_size=1,
            start_day=1,
        )
        assert "(none)" in result

    def test_closing_instruction(self):
        result = build_planner_turn_prompt(
            topic="t",
            cefr_level="A1",
            language_name="T",
            language_code="t",
            days=[],
            learner_snapshot="s",
            feedback=[],
            chat=[],
            batch_size=7,
            start_day=3,
        )
        assert "If proposing, propose exactly 7 days starting at day 3." in result
        assert "Respond in English" in result
        assert "committed plan and conversation above are in T" in result
        assert "collocations array is in T:" in result

    def test_emoji_in_user_message(self):
        """User messages with special chars pass through unchanged."""
        chat = [{"role": "user", "content": "Živjo! Kako si? 123"}]
        result = build_planner_turn_prompt(
            topic="t",
            cefr_level="A1",
            language_name="T",
            language_code="t",
            days=[],
            learner_snapshot="s",
            feedback=[],
            chat=chat,
            batch_size=1,
            start_day=1,
        )
        assert "Živjo! Kako si? 123" in result

    def test_orphaned_feedback_day_filtered_out(self):
        """Feedback for a day that no longer exists is excluded from the prompt."""
        days = [CurriculumDay(day=2, title="t", focus="f", collocations=["a"], learning_objective="o")]
        result = build_planner_turn_prompt(
            topic="t",
            cefr_level="A1",
            language_name="T",
            language_code="t",
            days=days,
            learner_snapshot="s",
            feedback=[{"day": 2, "note": "still exists"}, {"day": 9, "note": "orphaned"}],
            chat=[],
            batch_size=1,
            start_day=1,
        )
        assert "- Day 2: still exists" in result
        assert "Day 9" not in result
