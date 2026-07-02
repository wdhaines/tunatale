"""Lesson authoring — Story-JSON export/import round-trip (docs/lesson-authoring.md).

Story JSON is source; the Lesson blob is a build artifact. Export reconstructs
(or returns the persisted) Story JSON; import rebuilds a Lesson through the
same build step generation uses (`build_lesson_from_story`).
"""

import copy

import pytest

from app.generation.story import build_lesson_from_story
from app.storage.lesson_io import (
    export_lesson,
    import_lesson,
    speaker_warnings,
    validate_story,
)
from app.storage.store import ContentStore


def _story() -> dict:
    return {
        "title": "Ordering Coffee",
        "key_phrases": [
            {"phrase": "dober dan", "translation": "good day"},
            {"phrase": "prosim kavo", "translation": "a coffee please"},
        ],
        "scenes": [
            {
                "label": "At the Riverside Café",
                "lines": [
                    {"speaker": "female-1", "text": "Dober dan!", "translation": "Good day!"},
                    {"speaker": "male-1", "text": "Prosim kavo.", "translation": "A coffee please."},
                ],
            },
            {
                "label": "Paying",
                "lines": [
                    {"speaker": "female-1", "text": "Hvala lepa.", "translation": "Thank you very much."},
                ],
            },
        ],
        "dialogue_glosses": [
            {"word": "dober", "translation": "good"},
            {"word": "dan", "translation": "day"},
            {"word": "prosim", "translation": "please"},
            {"word": "kavo", "translation": "coffee"},
            {"word": "hvala", "translation": "thanks"},
            {"word": "lepa", "translation": "very"},
        ],
        "morphology_focus": ["accusative singular"],
    }


@pytest.fixture
def store():
    with ContentStore(":memory:") as s:
        yield s


class TestValidateStory:
    def test_valid_story_passes(self):
        validate_story(_story())

    def test_story_must_be_a_dict(self):
        with pytest.raises(ValueError, match="story"):
            validate_story(["not", "a", "dict"])

    def test_requires_key_phrases_or_scenes(self):
        with pytest.raises(ValueError, match="key_phrases.*scenes|scenes.*key_phrases"):
            validate_story({"title": "Empty"})

    def test_key_phrase_missing_phrase(self):
        story = _story()
        del story["key_phrases"][1]["phrase"]
        with pytest.raises(ValueError, match=r"key_phrases\[1\].*phrase"):
            validate_story(story)

    def test_key_phrase_missing_translation(self):
        story = _story()
        del story["key_phrases"][0]["translation"]
        with pytest.raises(ValueError, match=r"key_phrases\[0\].*translation"):
            validate_story(story)

    def test_scene_missing_label(self):
        story = _story()
        del story["scenes"][1]["label"]
        with pytest.raises(ValueError, match=r"scenes\[1\].*label"):
            validate_story(story)

    def test_line_missing_speaker(self):
        story = _story()
        del story["scenes"][0]["lines"][1]["speaker"]
        with pytest.raises(ValueError, match=r"scenes\[0\].lines\[1\].*speaker"):
            validate_story(story)

    def test_line_missing_text(self):
        story = _story()
        del story["scenes"][0]["lines"][0]["text"]
        with pytest.raises(ValueError, match=r"scenes\[0\].lines\[0\].*text"):
            validate_story(story)

    def test_line_missing_translation(self):
        # build_translated_section hard-accesses line["translation"] — a story
        # without it would KeyError deep in the build, so validation owns it.
        story = _story()
        del story["scenes"][0]["lines"][0]["translation"]
        with pytest.raises(ValueError, match=r"scenes\[0\].lines\[0\].*translation"):
            validate_story(story)

    def test_scenes_must_be_a_list(self):
        with pytest.raises(ValueError, match="scenes.*list"):
            validate_story({"scenes": {"label": "x"}})

    def test_lines_must_be_a_list(self):
        story = _story()
        story["scenes"][0]["lines"] = "not a list"
        with pytest.raises(ValueError, match=r"scenes\[0\].lines.*list"):
            validate_story(story)

    def test_key_phrases_must_be_a_list(self):
        story = _story()
        story["key_phrases"] = "not a list"
        with pytest.raises(ValueError, match="key_phrases.*list"):
            validate_story(story)

    def test_scene_entry_must_be_a_dict(self):
        story = _story()
        story["scenes"][0] = "not a dict"
        with pytest.raises(ValueError, match=r"scenes\[0\]"):
            validate_story(story)

    def test_line_entry_must_be_a_dict(self):
        story = _story()
        story["scenes"][0]["lines"][0] = 42
        with pytest.raises(ValueError, match=r"scenes\[0\].lines\[0\]"):
            validate_story(story)

    def test_key_phrase_entry_must_be_a_dict(self):
        story = _story()
        story["key_phrases"][0] = "bare string"
        with pytest.raises(ValueError, match=r"key_phrases\[0\]"):
            validate_story(story)


class TestExportLesson:
    def test_missing_lesson_raises_key_error(self, store):
        with pytest.raises(KeyError):
            export_lesson(store, "no-such-lesson")

    def test_export_is_self_describing(self, store, language):
        lesson = build_lesson_from_story(_story(), language=language)
        store.save_lesson("l1", "c1", 2, lesson)
        out = export_lesson(store, "l1")
        assert out["curriculum_id"] == "c1"
        assert out["day"] == 2
        assert out["story"]["title"] == "Ordering Coffee"

    def test_export_reconstructs_scenes_and_lines(self, store, language):
        story = _story()
        lesson = build_lesson_from_story(story, language=language)
        del lesson.generation_metadata["story"]  # legacy lesson: no stored source
        store.save_lesson("l1", "c1", 1, lesson)
        got = export_lesson(store, "l1")["story"]
        assert [s["label"] for s in got["scenes"]] == ["At the Riverside Café", "Paying"]
        assert got["scenes"][0]["lines"] == [
            {"speaker": "female-1", "text": "Dober dan!", "translation": "Good day!"},
            {"speaker": "male-1", "text": "Prosim kavo.", "translation": "A coffee please."},
        ]
        assert got["key_phrases"] == story["key_phrases"]
        assert got["morphology_focus"] == ["accusative singular"]

    def test_export_recovers_translations_for_legacy_lessons(self, store, language):
        # Lessons generated before sentence_translations existed in metadata:
        # export falls back to pairing L2/EN phrases in the TRANSLATED section.
        lesson = build_lesson_from_story(_story(), language=language)
        del lesson.generation_metadata["story"]  # legacy lesson: no stored source
        del lesson.generation_metadata["sentence_translations"]
        store.save_lesson("l1", "c1", 1, lesson)
        got = export_lesson(store, "l1")["story"]
        assert got["scenes"][0]["lines"][0]["translation"] == "Good day!"

    def test_export_reconstructs_dialogue_glosses(self, store, language):
        lesson = build_lesson_from_story(_story(), language=language)
        del lesson.generation_metadata["story"]  # legacy lesson: no stored source
        store.save_lesson("l1", "c1", 1, lesson)
        got = export_lesson(store, "l1")["story"]
        glosses = {g["word"]: g["translation"] for g in got["dialogue_glosses"]}
        assert glosses["dober"] == "good"
        assert glosses["kavo"] == "coffee"

    def test_export_line_without_known_translation_gets_empty_string(self, store, language):
        lesson = build_lesson_from_story(_story(), language=language)
        del lesson.generation_metadata["story"]  # legacy lesson: no stored source
        del lesson.generation_metadata["sentence_translations"]["Dober dan!"]
        store.save_lesson("l1", "c1", 1, lesson)
        got = export_lesson(store, "l1")["story"]
        assert got["scenes"][0]["lines"][0]["translation"] == ""

    def test_export_tolerates_lesson_without_natural_speed_section(self, store, language):
        # Defensive: a hand-built Lesson with no sections still exports
        # (empty scenes) instead of crashing.
        from app.models.lesson import Lesson

        lesson = Lesson(title="Bare", language_code="sl", sections=[], narrator_voice="x")
        store.save_lesson("l1", "c1", 1, lesson)
        got = export_lesson(store, "l1")["story"]
        assert got["scenes"] == []
        assert got["title"] == "Bare"

    def test_export_skips_l2_phrase_before_any_scene_label(self, store, language):
        # Defensive: a malformed NATURAL_SPEED section whose first content
        # phrase is an L2 line (no scene opened yet) is skipped, not crashed on.
        from app.models.lesson import Lesson, Phrase, Section, SectionType

        section = Section(
            section_type=SectionType.NATURAL_SPEED,
            phrases=[
                Phrase(text="Natural Speed", voice_id="x", language_code="en", role="narrator"),
                Phrase(text="Dober dan!", voice_id="x", language_code="sl", role="female-1"),
                Phrase(text="Scene One", voice_id="x", language_code="en", role="narrator"),
                Phrase(text="Prosim kavo.", voice_id="x", language_code="sl", role="male-1"),
            ],
        )
        lesson = Lesson(title="Odd", language_code="sl", sections=[section], narrator_voice="x")
        store.save_lesson("l1", "c1", 1, lesson)
        got = export_lesson(store, "l1")["story"]
        assert got["scenes"] == [
            {
                "label": "Scene One",
                "lines": [{"speaker": "male-1", "text": "Prosim kavo.", "translation": ""}],
            }
        ]


class TestImportLesson:
    def test_import_saves_and_returns_new_id(self, store, language):
        file = {"curriculum_id": "c1", "day": 3, "story": _story()}
        lesson_id, lesson = import_lesson(store, file, language)
        assert lesson_id.startswith("ordering-coffee-")
        row = store.get_lesson_row(lesson_id)
        assert row is not None
        assert row["curriculum_id"] == "c1"
        assert row["day"] == 3
        assert lesson == build_lesson_from_story(_story(), language=language)

    def test_import_validates_first(self, store, language):
        bad = {"curriculum_id": "c1", "day": 1, "story": {"title": "Empty"}}
        with pytest.raises(ValueError):
            import_lesson(store, bad, language)
        # Nothing saved on failure
        assert store.get_lesson_days("c1") == []

    def test_reimport_appends_and_latest_wins(self, store, language):
        file = {"curriculum_id": "c1", "day": 3, "story": _story()}
        first_id, _ = import_lesson(store, file, language)
        edited = copy.deepcopy(file)
        edited["story"]["scenes"][0]["lines"][0]["text"] = "Dober večer!"
        edited["story"]["scenes"][0]["lines"][0]["translation"] = "Good evening!"
        second_id, _ = import_lesson(store, edited, language)
        assert first_id != second_id
        latest = store.get_latest_lesson_by_day("c1", 3)
        assert latest is not None
        assert latest[0] == second_id


class TestRoundTrip:
    def test_import_export_import_is_stable(self, store, language):
        """import(export(lesson)) rebuilds an identical Lesson."""
        original = build_lesson_from_story(_story(), language=language)
        store.save_lesson("l1", "c1", 1, original)
        exported = export_lesson(store, "l1")
        _, rebuilt = import_lesson(store, exported, language)
        assert rebuilt == original

    def test_export_prefers_exact_persisted_source(self, store, language):
        # The build lowercases speaker roles, so reconstruction could never
        # return "Female-1" — only the persisted exact source can.
        story = _story()
        story["scenes"][0]["lines"][0]["speaker"] = "Female-1"
        lesson = build_lesson_from_story(story, language=language)
        store.save_lesson("l1", "c1", 1, lesson)
        got = export_lesson(store, "l1")["story"]
        assert got == story
        assert got["scenes"][0]["lines"][0]["speaker"] == "Female-1"

    def test_export_after_import_is_byte_exact(self, store, language):
        file = {"curriculum_id": "c1", "day": 4, "story": _story()}
        lesson_id, _ = import_lesson(store, file, language)
        exported = export_lesson(store, lesson_id)
        assert exported["story"] == file["story"]
        assert exported["curriculum_id"] == "c1"
        assert exported["day"] == 4


class TestSpeakerWarnings:
    def test_known_speakers_are_silent(self, language):
        assert speaker_warnings(_story(), language) == []

    def test_unknown_speaker_warns_with_fallback_note(self, language):
        story = _story()
        story["scenes"][0]["lines"][0]["speaker"] = "male-9"
        warnings = speaker_warnings(story, language)
        assert len(warnings) == 1
        assert "male-9" in warnings[0]
        assert "narrator" in warnings[0]

    def test_duplicate_unknown_speaker_warns_once(self, language):
        story = _story()
        for line in story["scenes"][0]["lines"]:
            line["speaker"] = "robot-7"
        assert len(speaker_warnings(story, language)) == 1
