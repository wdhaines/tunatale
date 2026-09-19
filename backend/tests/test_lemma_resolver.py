"""Publish-time resolution of a table lemmatizer's ambiguous words (tunatale-kbb.18)."""

from __future__ import annotations

import json
import logging

import pytest

from app.llm.call_sites import CallSite
from app.models.lesson import KeyPhraseInfo, Lesson, Phrase, Section, SectionType
from app.srs import lemma_resolver
from app.srs.lemma_resolver import (
    ambiguous_items,
    build_prompt,
    lesson_sentences,
    parse_reply,
    resolve_lesson_lemmas,
    resolve_sentences,
)
from app.srs.lemma_table import TableLemmatizer
from app.srs.lemmatizer import (
    LowercaseLemmatizer,
    TokenAnalysis,
    _deserialize_analyses,
    _serialize_analyses,
    model_version_for,
)
from tests.test_lemma_table import write_extract


class _FakeLLM:
    def __init__(self, replies: list[str] | None = None, error: Exception | None = None) -> None:
        self._replies = list(replies or [])
        self._error = error
        self.calls: list[dict] = []

    async def complete(self, prompt, system_prompt=None, temperature=0.7, max_tokens=256, call_site=""):
        self.calls.append(
            {"prompt": prompt, "system": system_prompt, "temperature": temperature, "call_site": call_site}
        )
        if self._error is not None:
            raise self._error
        return self._replies.pop(0)


@pytest.fixture
def lem(tmp_path) -> TableLemmatizer:
    return TableLemmatizer("no", write_extract(tmp_path / "lemmas.tsv.gz"))


def cached(srs_db, lem, sentence):
    row = srs_db.get_sentence_analysis(sentence, "no", model_version_for(lem))
    return None if row is None else [(a.surface, a.lemma, a.upos) for a in _deserialize_analyses(row)]


class TestItems:
    def test_only_words_whose_lemmas_differ_are_items(self, lem):
        # deg has two readings but one lemma (du): nothing to choose.
        items = ambiguous_items("Og så deg noe", lem)
        assert [(i, tok) for _s, i, tok, _r in items] == [(1, "så"), (3, "noe")]

    def test_prompt_lists_each_tag_with_its_lemma(self, lem):
        prompt = build_prompt(ambiguous_items("Og så stoppet vi.", lem))
        assert prompt == '1. "så" (word 2) in: Og så stoppet vi.\n   tags: VERB (se) | ADV (så)'


class TestParse:
    def test_keeps_valid_tags_only(self, lem):
        items = ambiguous_items("så noe", lem)
        assert parse_reply('Sure: {"1": "adv", "2": "NOUN"}', items) == {("så noe", 0): "ADV"}

    @pytest.mark.parametrize("reply", ["no json here", "[1, 2]", '{"1": ', '["ADV"]'])
    def test_garbage_chooses_nothing(self, lem, reply, caplog):
        with caplog.at_level(logging.WARNING):
            assert parse_reply(reply, ambiguous_items("så", lem)) == {}


class TestResolveSentences:
    async def test_writes_the_chosen_readings(self, lem, srs_db):
        llm = _FakeLLM(['{"1": "ADV"}'])
        n = await resolve_sentences(["Og så stoppet vi."], "no", "Norwegian", lem, model_version_for(lem), srs_db, llm)
        assert n == 1
        assert cached(srs_db, lem, "Og så stoppet vi.")[1] == ("så", "så", "ADV")
        call = llm.calls[0]
        assert call["call_site"] == CallSite.LEMMA_RESOLVE
        assert call["temperature"] == 0.0
        assert "Norwegian" in call["system"]

    async def test_an_unchosen_word_keeps_its_default(self, lem, srs_db):
        await resolve_sentences(["så"], "no", "Norwegian", lem, model_version_for(lem), srs_db, _FakeLLM(["{}"]))
        assert cached(srs_db, lem, "så") == [("så", "se", "VERB")]

    async def test_skips_unambiguous_and_already_cached_sentences(self, lem, srs_db):
        mv = model_version_for(lem)
        exact = _serialize_analyses([TokenAnalysis(surface="så", lemma="så", upos="ADV")])
        srs_db.set_sentence_analysis("Og så", "no", "9.9.9+f2", exact)  # the real model's row
        srs_db.set_sentence_analysis("så noe", "no", mv, exact)  # already resolved
        llm = _FakeLLM()
        n = await resolve_sentences(["Prisen", "Og så", "så noe", "Prisen"], "no", "Norwegian", lem, mv, srs_db, llm)
        assert n == 0
        assert llm.calls == []
        assert cached(srs_db, lem, "Prisen") is None

    async def test_batches_sentences_per_call(self, lem, srs_db, monkeypatch):
        monkeypatch.setattr(lemma_resolver, "SENTENCES_PER_CALL", 2)
        sentences = ["så 1", "så 2", "så 3"]
        llm = _FakeLLM(['{"1": "ADV", "2": "VERB"}', '{"1": "ADV"}'])
        assert await resolve_sentences(sentences, "no", "Norwegian", lem, model_version_for(lem), srs_db, llm) == 3
        assert len(llm.calls) == 2
        assert [cached(srs_db, lem, s)[0][1] for s in sentences] == ["så", "se", "så"]


def _lesson(language_code: str = "no") -> Lesson:
    return Lesson(
        title="Snø",
        language_code=language_code,
        sections=[
            Section(
                section_type=SectionType.NATURAL_SPEED,
                phrases=[
                    Phrase(text="Og så stoppet vi.", voice_id="v", language_code=language_code),
                    Phrase(text="And then we stopped.", voice_id="v", language_code="en"),
                ],
            )
        ],
        key_phrases=[KeyPhraseInfo(phrase="noe viktig", translation="something important")],
    )


class TestResolveLesson:
    def test_lesson_sentences_are_its_l2_phrases_and_key_phrases(self):
        assert lesson_sentences(_lesson()) == ["Og så stoppet vi.", "noe viktig"]

    async def test_resolves_the_lesson(self, lem, srs_db):
        llm = _FakeLLM(['{"1": "ADV", "2": "PRON"}'])
        assert await resolve_lesson_lemmas(_lesson(), srs_db, llm, lemmatizer=lem) == 2
        assert cached(srs_db, lem, "noe viktig")[0] == ("noe", "noe", "PRON")

    @pytest.mark.parametrize("missing", ["db", "llm"])
    async def test_needs_a_db_and_an_llm(self, lem, srs_db, missing):
        db = None if missing == "db" else srs_db
        llm = None if missing == "llm" else _FakeLLM()
        assert await resolve_lesson_lemmas(_lesson(), db, llm, lemmatizer=lem) == 0

    async def test_other_engines_need_no_resolution(self, srs_db):
        llm = _FakeLLM()
        assert await resolve_lesson_lemmas(_lesson(), srs_db, llm, lemmatizer=LowercaseLemmatizer()) == 0
        assert llm.calls == []

    async def test_resolves_the_real_engine_by_default(self, srs_db):
        # The CI/test pin is lowercase, so the factory gives a non-table engine.
        assert await resolve_lesson_lemmas(_lesson(), srs_db, _FakeLLM()) == 0

    async def test_an_llm_failure_never_breaks_publishing(self, lem, srs_db, caplog):
        with caplog.at_level(logging.WARNING):
            n = await resolve_lesson_lemmas(_lesson(), srs_db, _FakeLLM(error=RuntimeError("429")), lemmatizer=lem)
        assert n == 0
        assert "default readings stand" in caplog.text


def test_reply_json_is_what_the_prompt_asks_for(lem):
    """The parse contract, stated once: item number (string) -> tag."""
    items = ambiguous_items("så", lem)
    assert parse_reply(json.dumps({"1": "VERB"}), items) == {("så", 0): "VERB"}


class _RecordingTarget:
    """A ContentTarget that records what the analysis cache held at write time."""

    def __init__(self, srs_db, lem) -> None:
        self._srs_db = srs_db
        self._lem = lem
        self.cached_at_write = None

    def write(self, lesson) -> str:
        self.cached_at_write = cached(self._srs_db, self._lem, "Og så stoppet vi.")
        return "lesson-1"

    def invalidate_audio(self, content_id) -> None:
        raise AssertionError("not a replace")

    async def schedule_render(self, content_id, lesson) -> None:
        return None


async def test_publish_lesson_resolves_before_the_write(lem, srs_db):
    """The seam every writer shares runs the resolver, awaited, before the write."""
    from app.generation import publishing

    target = _RecordingTarget(srs_db, lem)
    await publishing.publish_lesson(
        _lesson(),
        target=target,
        srs_db=srs_db,
        lemmatizer_kwargs={"lemmatizer": lem, "model_version": model_version_for(lem)},
        replace=False,
        llm=_FakeLLM(['{"1": "ADV", "2": "PRON"}']),
    )
    for task in list(publishing._background_tasks):
        await task
    assert target.cached_at_write is not None
    assert target.cached_at_write[1] == ("så", "så", "ADV")
