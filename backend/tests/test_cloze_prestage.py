"""Pre-staging LLM cloze sentences off the sync's critical path (tunatale-keb0).

``choose_cloze_sentence`` declines when the note's own examples carry no
blankable form of the word, and ``_fallback_to_cloze`` counts that ``unservable``
— the word is closed-class, so there is no image path either, and it gets NO
production card at all. 27 words sit in that state on the live Norwegian deck.

This pass writes a sentence for them. Two constraints shape it, and both are
load-bearing:

- **It never opens the Anki collection.** So it cannot read a note's example
  sentences (``get_cloze_material`` is the Anki reader) and cannot judge them.
  It generates from the word and its gloss alone, which TT already holds.
- **The sync makes no network call.** The mint reads a cached sentence; the LLM
  round trip happens here, in a background task, exactly as
  ``prestage_production_images`` does for pictures.
"""

from __future__ import annotations

import asyncio

import pytest

from app.cards.cloze_prestage import prestage_cloze_sentences
from app.srs.database import SRSDatabase


class ScriptedLLM:
    """Generator, judge and translator from one double, told apart by the system prompt.

    Discriminated most-specific-first. ``"translation"`` would be the obvious
    needle for the translator and is WRONG: the generate prompt ends "no
    translation, no quotes, no comment", so it would swallow every generate
    call. ``"into English"`` appears in exactly one of the three.
    """

    def __init__(
        self,
        *,
        generated: dict[str, str] | None = None,
        fillers: dict[str, str] | None = None,
        translations: dict[str, str] | None = None,
    ):
        self._generated = generated or {}
        self._fillers = fillers or {}
        self._translations = translations or {}
        self.generate_calls = 0
        self.judge_calls = 0
        self.translate_calls = 0

    async def complete(self, prompt, system_prompt=None, temperature=0.7, max_tokens=256):
        system = system_prompt or ""
        if "into English" in system:
            self.translate_calls += 1
            for needle, reply in self._translations.items():
                if needle in prompt:
                    return reply
            return ""
        if "blank" in system:
            self.judge_calls += 1
            for needle, reply in self._fillers.items():
                if needle in prompt:
                    return reply
            return ""
        self.generate_calls += 1
        word = prompt.split(" (")[0].strip()
        return self._generated.get(word, "")


def _db_with_awaiting(words: list[tuple[str, str]]) -> SRSDatabase:
    """A DB holding *words* as ``(text, gloss)`` rows awaiting production.

    Reuses ``test_anki_promote_production._add_word`` rather than hand-rolling
    the shape: the selection query wants a linked note, a recognition direction
    in ``review`` and no production direction, and a fixture that quietly misses
    one of those returns an empty candidate list — which reads exactly like "the
    pass found nothing to do".
    """
    from tests.test_anki_promote_production import _add_word

    db = SRSDatabase(":memory:")
    for i, (text, gloss) in enumerate(words):
        _add_word(db, text, gloss, note_id=1000 + i, card_id=10_000 + i, image=False, disambig="preposition")
    return db


class TestPrestageClozeSentences:
    @pytest.mark.asyncio
    async def test_caches_a_generated_sentence_that_survives_the_judge(self):
        llm = ScriptedLLM(
            generated={"foran": "Bilen står foran huset, ikke bak det."},
            fillers={"Bilen står ___ huset, ikke bak det.": "foran"},
        )
        db = _db_with_awaiting([("foran", "in front of")])

        report = await prestage_cloze_sentences(db, llm, language_code="no", limit=5)

        assert report.written == 1
        cached = db.get_cached_cloze_sentence("foran", "no")
        assert cached is not None
        assert cached.sentence == "Bilen står foran huset, ikke bak det."
        assert cached.status == "determined"

    @pytest.mark.asyncio
    async def test_caches_a_translation_OF_THE_SENTENCE_alongside_it(self):
        """The supply side of tunatale-ml06.

        This table is the LLM tier's whole supply, and it had no translation
        column — so the mint had only the WORD's gloss in scope and wrote it
        into the sentence slot, making 18 of 109 live cloze cards show the same
        text twice. Generating the sentence without a translation of it is what
        made that substitution look like the only option.
        """
        llm = ScriptedLLM(
            generated={"foran": "Bilen står foran huset, ikke bak det."},
            fillers={"Bilen står ___ huset, ikke bak det.": "foran"},
            translations={"Bilen står foran huset": "The car is parked in front of the house, not behind it."},
        )
        db = _db_with_awaiting([("foran", "in front of")])

        report = await prestage_cloze_sentences(db, llm, language_code="no", limit=5)

        assert report.written == 1
        cached = db.get_cached_cloze_sentence("foran", "no")
        assert cached is not None
        assert cached.sentence_translation == "The car is parked in front of the house, not behind it."
        assert llm.translate_calls == 1

    @pytest.mark.asyncio
    async def test_a_sentence_whose_translation_fails_is_still_cached_with_an_empty_one(self):
        """A missing translation must not cost the card.

        These words have no image path, so declining reproduces the very
        `unservable` state this pass exists to clear — the same reasoning that
        keeps an underdetermined sentence. An empty slot is honest; what is NOT
        allowed is falling back to the word's gloss, which is the defect.
        """
        llm = ScriptedLLM(
            generated={"foran": "Bilen står foran huset, ikke bak det."},
            fillers={"Bilen står ___ huset, ikke bak det.": "foran"},
            translations={},  # the translator returns "" for everything
        )
        db = _db_with_awaiting([("foran", "in front of")])

        report = await prestage_cloze_sentences(db, llm, language_code="no", limit=5)

        assert report.written == 1
        cached = db.get_cached_cloze_sentence("foran", "no")
        assert cached is not None
        assert cached.sentence == "Bilen står foran huset, ikke bak det."
        assert cached.sentence_translation == ""
        assert cached.sentence_translation != "in front of", "the word gloss must never stand in"

    @pytest.mark.asyncio
    async def test_keeps_an_underdetermined_sentence_but_records_the_verdict(self):
        """A sentence with competitors still beats no card at all.

        These words are closed-class with no image path, so declining here
        reproduces exactly the `unservable` state this pass exists to clear. The
        verdict is stored so the UI can offer "try again" on the ones that need
        it.
        """
        llm = ScriptedLLM(
            generated={"foran": "Bilen står foran huset."},
            fillers={"Bilen står ___ huset.": "foran, bak, ved"},
        )
        db = _db_with_awaiting([("foran", "in front of")])

        report = await prestage_cloze_sentences(db, llm, language_code="no", limit=5)

        assert report.written == 1
        cached = db.get_cached_cloze_sentence("foran", "no")
        assert cached.status == "underdetermined"
        assert cached.competitors == ("bak", "ved")

    @pytest.mark.asyncio
    async def test_writes_nothing_when_the_generator_produces_nothing(self):
        llm = ScriptedLLM(generated={}, fillers={})
        db = _db_with_awaiting([("foran", "in front of")])

        report = await prestage_cloze_sentences(db, llm, language_code="no", limit=5)

        assert report.written == 0
        assert report.failed == 1
        assert db.get_cached_cloze_sentence("foran", "no") is None

    @pytest.mark.asyncio
    async def test_skips_a_word_already_cached(self):
        """Without this the same word costs a live LLM chain on every pass forever."""
        llm = ScriptedLLM(
            generated={"foran": "Bilen står foran huset."},
            fillers={"Bilen står ___ huset.": "foran"},
        )
        db = _db_with_awaiting([("foran", "in front of")])
        await prestage_cloze_sentences(db, llm, language_code="no", limit=5)
        before = llm.generate_calls

        report = await prestage_cloze_sentences(db, llm, language_code="no", limit=5)

        assert llm.generate_calls == before
        assert report.already_cached == 1

    @pytest.mark.asyncio
    async def test_respects_the_limit(self):
        """Bounds live LLM chains per pass, as PRESTAGE pacing does for images."""
        llm = ScriptedLLM(
            generated={w: f"En setning med {w} i." for w in ("foran", "bak", "under")},
            fillers={},
        )
        db = _db_with_awaiting([("foran", "a"), ("bak", "b"), ("under", "c")])

        report = await prestage_cloze_sentences(db, llm, language_code="no", limit=2)

        assert report.written + report.failed == 2

    @pytest.mark.asyncio
    async def test_one_soft_failure_does_not_abandon_the_batch(self):
        """An ordinary LLM error is absorbed by `generate_cloze_sentence` itself.

        It returns None rather than raising, so this exercises the None arm — NOT
        the `gather` exception arm, which needs a BaseException (below).
        """

        class Exploding(ScriptedLLM):
            async def complete(self, prompt, system_prompt=None, temperature=0.7, max_tokens=256):
                if "bak" in prompt and "blank" not in (system_prompt or ""):
                    raise RuntimeError("groq 500")
                return await super().complete(prompt, system_prompt, temperature, max_tokens)

        llm = Exploding(
            generated={"foran": "Bilen står foran huset.", "under": "Katten er under bordet."},
            fillers={},
        )
        db = _db_with_awaiting([("foran", "a"), ("bak", "b"), ("under", "c")])

        report = await prestage_cloze_sentences(db, llm, language_code="no", limit=5)

        assert report.written == 2, "the two good words must still be cached"
        assert report.failed == 1

    @pytest.mark.asyncio
    async def test_a_baseexception_in_one_word_does_not_abandon_the_batch(self):
        """The `return_exceptions=True` arm, and why it is not decoration.

        `generate_cloze_sentence` and `judge_cloze` both catch `Exception`, so
        only a BaseException reaches `gather` — a cancellation, which is exactly
        what a background task gets at server shutdown. Without
        `return_exceptions=True` that one cancellation abandons the whole batch
        mid-flight and, because `background_tasks.add_task` swallows what
        escapes, leaves no trace at all. That is tunatale-ouk.10, where one bad
        fetch discarded up to 19 good ones invisibly across six syncs.
        """

        class Cancelling(ScriptedLLM):
            async def complete(self, prompt, system_prompt=None, temperature=0.7, max_tokens=256):
                if "bak" in prompt and "blank" not in (system_prompt or ""):
                    raise asyncio.CancelledError
                return await super().complete(prompt, system_prompt, temperature, max_tokens)

        llm = Cancelling(
            generated={"foran": "Bilen står foran huset.", "under": "Katten er under bordet."},
            fillers={},
        )
        db = _db_with_awaiting([("foran", "a"), ("bak", "b"), ("under", "c")])

        report = await prestage_cloze_sentences(db, llm, language_code="no", limit=5)

        assert report.written == 2, "the two good words must still be cached"
        assert report.failed == 1
        assert db.get_cached_cloze_sentence("under", "no") is not None


class TestPrestageEdges:
    @pytest.mark.asyncio
    async def test_an_open_class_word_is_skipped(self):
        """Only closed-class words route to a cloze; a noun is waiting for a picture."""
        llm = ScriptedLLM(generated={}, fillers={})
        db = SRSDatabase(":memory:")
        from tests.test_anki_promote_production import _add_word

        _add_word(db, "hus", "house", note_id=1000, card_id=10000, image=False, disambig="noun")

        report = await prestage_cloze_sentences(db, llm, language_code="no", limit=5)

        assert report.skipped_open_class == 1
        assert llm.generate_calls == 0

    @pytest.mark.asyncio
    async def test_the_log_line_names_the_words_that_failed(self, caplog):
        """A count alone cannot be acted on; PRESTAGE_IMAGES learned this the hard way."""
        import logging

        class Cancelling(ScriptedLLM):
            async def complete(self, prompt, system_prompt=None, temperature=0.7, max_tokens=256):
                raise asyncio.CancelledError

        db = _db_with_awaiting([("foran", "in front of")])
        with caplog.at_level(logging.WARNING, logger="app.cards.cloze_prestage"):
            await prestage_cloze_sentences(db, Cancelling(), language_code="no", limit=5)

        assert "PRESTAGE_CLOZE" in caplog.text
        assert "foran" in caplog.text
        assert "CancelledError" in caplog.text
