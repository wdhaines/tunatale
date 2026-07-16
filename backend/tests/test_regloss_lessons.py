"""Tests for the one-shot legacy-lesson re-gloss migration.

The migration translates each lesson's actual dialogue *surfaces in context* and
rewrites token_glosses with surface keys (+ a sentence-aware lemma fallback),
replacing the old POS-blind, conjugation-collapsed lemma keys. Story text and
audio are untouched.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

from app.languages import get_language
from app.models.lesson import Lesson, Phrase, Section, SectionType
from app.srs.lemmatizer import LowercaseLemmatizer
from app.storage.regloss_lessons import (
    build_regloss_prompt,
    dialogue_lines,
    parse_gloss_array,
    regloss_all,
    regloss_lesson,
)
from app.storage.store import ContentStore


def _lesson(l2_lines: list[str] | None = None, narrator: bool = False) -> Lesson:
    phrases: list[Phrase] = []
    if narrator:
        phrases.append(Phrase(text="Scene: market", voice_id="narrator", language_code="en", role="narrator"))
    for text in l2_lines or []:
        phrases.append(Phrase(text=text, voice_id="female-1", language_code="sl", role="female-1"))
    return Lesson(
        title="Day 1",
        language_code="sl",
        sections=[Section(section_type=SectionType.NATURAL_SPEED, phrases=phrases)],
        generation_metadata={"token_glosses": {"biti": "am"}},  # legacy lemma-keyed garbage
    )


def _fake_llm(payload) -> MagicMock:
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    llm = MagicMock()
    llm.complete = AsyncMock(return_value=raw)
    return llm


class TestDialogueLines:
    def test_collects_l2_natural_speed_lines(self):
        lesson = _lesson(["Kje je banka?", "Tam."], narrator=True)
        assert dialogue_lines(lesson) == ["Kje je banka?", "Tam."]

    def test_skips_non_natural_speed_sections(self):
        lesson = Lesson(
            title="t",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.KEY_PHRASES,
                    phrases=[Phrase(text="dober dan", voice_id="v", language_code="sl", role="female-1")],
                )
            ],
        )
        assert dialogue_lines(lesson) == []

    def test_empty_when_only_narrator(self):
        assert dialogue_lines(_lesson([], narrator=True)) == []


class TestBuildPrompt:
    def test_contains_lines_and_language(self):
        prompt = build_regloss_prompt(["Kje je banka?"], "Slovene")
        assert "Slovene" in prompt
        assert "Kje je banka?" in prompt
        assert "boste" in prompt  # the in-context example instruction


class TestParseGlossArray:
    def test_parses_bare_array(self):
        out = parse_gloss_array('[{"word": "kje", "translation": "where"}]')
        assert out == [{"word": "kje", "translation": "where"}]

    def test_parses_fenced_array(self):
        out = parse_gloss_array('```json\n[{"word": "kje", "translation": "where"}]\n```')
        assert out == [{"word": "kje", "translation": "where"}]

    def test_unwraps_dialogue_glosses_object(self):
        raw = json.dumps({"dialogue_glosses": [{"word": "kje", "translation": "where"}]})
        assert parse_gloss_array(raw) == [{"word": "kje", "translation": "where"}]

    def test_filters_non_dict_entries(self):
        assert parse_gloss_array('[{"word": "a", "translation": "b"}, "junk", 5]') == [
            {"word": "a", "translation": "b"}
        ]


class TestReglossLesson:
    async def test_surface_keyed_with_lemma_fallback(self):
        lesson = _lesson(["Boste kavo?"])
        llm = _fake_llm([{"word": "boste", "translation": "you will"}, {"word": "kavo", "translation": "coffee"}])
        result = await regloss_lesson(lesson, llm, LowercaseLemmatizer(), get_language("sl"))
        # Surface keys carry the specific translation...
        assert result["boste"] == "you will"
        assert result["kavo"] == "coffee"
        # ...and the (lowercase) lemma fallback is added from the dialogue analysis.
        # LowercaseLemmatizer lemma == surface, so no extra keys appear here.
        assert result == {"boste": "you will", "kavo": "coffee"}

    async def test_lemma_fallback_added_when_lemma_differs(self):
        """A real lemmatizer maps boste→biti; the lemma key is added (first surface wins)."""

        class _Lem:
            def lemmatize(self, w, code):
                return {"boste": "biti", "bom": "biti"}.get(w, w)

            def analyze_sentence(self, sentence, code):
                from app.srs.lemmatizer import TokenAnalysis

                out = []
                for tok in sentence.split():
                    key = tok.strip("?.,").lower()
                    out.append(
                        TokenAnalysis(surface=tok.strip("?.,"), lemma={"boste": "biti", "bom": "biti"}.get(key, key))
                    )
                return out

        lesson = _lesson(["Boste bom"])
        llm = _fake_llm([{"word": "boste", "translation": "you will"}, {"word": "bom", "translation": "I will"}])
        result = await regloss_lesson(lesson, llm, _Lem(), get_language("sl"))
        assert result["boste"] == "you will"
        assert result["bom"] == "I will"
        # setdefault: first surface (boste) wins the shared lemma "biti"
        assert result["biti"] == "you will"

    async def test_skips_entries_missing_word_or_translation(self):
        lesson = _lesson(["Kje"])
        llm = _fake_llm(
            [
                {"word": "kje", "translation": "where"},
                {"word": "", "translation": "blank-word"},
                {"word": "x", "translation": ""},
                {"translation": "no-word-key"},
            ]
        )
        result = await regloss_lesson(lesson, llm, LowercaseLemmatizer(), get_language("sl"))
        assert result == {"kje": "where"}

    async def test_word_absent_from_dialogue_gets_no_lemma_fallback(self):
        """An LLM gloss for a word not in the dialogue still gets a surface key but
        no lemma fallback (it's not in the sentence-aware surface→lemma map)."""

        class _Lem(LowercaseLemmatizer):
            def lemmatize(self, w, code):  # would add a fallback key if ever consulted
                raise AssertionError("out-of-dialogue words must not be single-word lemmatized")

        lesson = _lesson(["Kje"])
        llm = _fake_llm([{"word": "kje", "translation": "where"}, {"word": "bonus", "translation": "extra"}])
        result = await regloss_lesson(lesson, llm, _Lem(), get_language("sl"))
        assert result == {"kje": "where", "bonus": "extra"}

    async def test_returns_none_without_dialogue(self):
        lesson = _lesson([], narrator=True)
        llm = _fake_llm([])
        assert await regloss_lesson(lesson, llm, LowercaseLemmatizer(), get_language("sl")) is None
        llm.complete.assert_not_called()


class TestReglossAll:
    async def test_rewrites_and_counts_dialogue_lessons(self):
        store = ContentStore(":memory:")
        store.save_lesson("l1", "c1", 1, _lesson(["Kje je banka?"]))
        store.save_lesson("l2", "c1", 2, _lesson([], narrator=True))  # no dialogue → skipped
        llm = _fake_llm([{"word": "kje", "translation": "where"}, {"word": "banka", "translation": "bank"}])

        count = await regloss_all(store, llm, LowercaseLemmatizer(), get_language("sl"))

        assert count == 1
        glosses = store.get_lesson("l1").generation_metadata["token_glosses"]
        # Surface-keyed, replacing the legacy "biti": "am" garbage wholesale.
        assert glosses == {"kje": "where", "banka": "bank"}

    async def test_empty_gloss_result_does_not_overwrite(self):
        store = ContentStore(":memory:")
        store.save_lesson("l1", "c1", 1, _lesson(["Kje"]))
        llm = _fake_llm([])  # LLM returned nothing usable

        count = await regloss_all(store, llm, LowercaseLemmatizer(), get_language("sl"))

        assert count == 0
        # untouched legacy data still present (not clobbered with an empty map)
        assert store.get_lesson("l1").generation_metadata["token_glosses"] == {"biti": "am"}
        store.close()
