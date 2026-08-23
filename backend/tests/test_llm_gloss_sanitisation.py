"""Sanitising an LLM gloss reply before it reaches the back of a card.

A card's translation comes from ``token_glosses`` in the lesson's generation
metadata, and the generation-time LLM drops words when asked to gloss a whole
dialogue at once (``generation/story.py::_missing_log``; a live run 2026-08-22
logged *"LLM omitted 3 word(s) from dialogue_glosses (sl): dan dober in"*). The
word then reaches ``/listen`` with no gloss and gets a card with nothing on its
back — the `ferskt` shape (bd `tunatale-1wiw`).

Asking for ONE word in ONE sentence is a far easier task than the whole-dialogue
gloss that dropped it, which is why the retry is worth making rather than being
a re-roll of the same dice.

The retry reuses the EXISTING ``llm/translate.py::generate_word_gloss`` rather
than a parallel per-word call — but that helper returned ``result.strip()``
verbatim, so the prompt's "no quotes, no explanation" was the only thing between
a chatty reply and a flashcard. An instruction, not a guarantee. These tests pin
the guarantee.

``""`` is the established "no opinion" sentinel: both pre-existing callers
already guard with ``if gloss:``, so rejecting a bad reply costs them their
fallback and nothing else.

The double is passed as an argument, never patched (`.claude/rules/testing.md`
mock boundaries).
"""

from __future__ import annotations

from app.llm.translate import generate_word_gloss, parse_gloss_response, translate_term


class _FakeLLM:
    """Minimal async LLM double: returns a canned response or raises."""

    def __init__(self, response: str | None = None, error: Exception | None = None) -> None:
        self._response = response
        self._error = error
        self.prompts: list[str] = []
        self.system_prompts: list[str | None] = []

    async def complete(self, prompt, system_prompt=None, temperature=0.7, max_tokens=256):
        self.prompts.append(prompt)
        self.system_prompts.append(system_prompt)
        if self._error is not None:
            raise self._error
        assert self._response is not None
        return self._response


class TestParseResponse:
    """The reply lands on a flashcard's back verbatim, so it must be a gloss —
    not a sentence, not a preamble, not the model narrating."""

    def test_takes_a_plain_gloss(self):
        assert parse_gloss_response("fresh") == "fresh"

    def test_strips_quotes_and_surrounding_whitespace(self):
        assert parse_gloss_response('  "fresh"  ') == "fresh"

    def test_strips_a_label_prefix(self):
        assert parse_gloss_response("English: fresh") == "fresh"
        assert parse_gloss_response("Meaning - fresh") == "fresh"

    def test_takes_only_the_first_line(self):
        assert parse_gloss_response("fresh\n\n(from Norwegian fersk)") == "fresh"

    def test_keeps_a_multi_word_gloss(self):
        assert parse_gloss_response("to look after") == "to look after"

    def test_declines_an_empty_or_none_reply(self):
        assert parse_gloss_response("") == ""
        assert parse_gloss_response("   ") == ""
        assert parse_gloss_response("NONE") == ""
        assert parse_gloss_response("none") == ""

    def test_strips_a_trailing_full_stop(self):
        """A one-word reply often arrives punctuated; that alone is not prose."""
        assert parse_gloss_response("fresh.") == "fresh"

    def test_keeps_a_realistic_phrasal_gloss(self):
        """The cap must not be so tight it rejects a legitimate phrasal gloss."""
        assert parse_gloss_response("to put something off until later") == "to put something off until later"
        assert parse_gloss_response("the day before yesterday") == "the day before yesterday"

    def test_declines_a_reply_that_is_prose_rather_than_a_gloss(self):
        """A model that explains instead of answering must not put its
        explanation on the card."""
        assert parse_gloss_response("I'm sorry, but I cannot determine the meaning of this word.") == ""

    def test_declines_a_reply_that_translates_the_whole_sentence(self):
        """The hardest case: fluent, correctly punctuated English about the right
        subject, with nothing but its length to give it away."""
        assert parse_gloss_response("Yes, and the ski track was completely fresh.") == ""

    def test_declines_one_absurdly_long_token(self):
        assert parse_gloss_response("a" * 200) == ""


class TestGenerateWordGloss:
    async def test_returns_the_sanitised_gloss(self):
        assert (
            await generate_word_gloss(_FakeLLM("fresh."), surface="ferskt", lemma="fersk", source_lang="no") == "fresh"
        )

    async def test_an_llm_error_yields_the_no_opinion_sentinel(self):
        """The retry runs in a BackgroundTask, where an exception is invisible —
        no response carries it. It must never escape."""
        llm = _FakeLLM(error=RuntimeError("429 rate limited"))
        assert await generate_word_gloss(llm, surface="ferskt", lemma="fersk", source_lang="no") == ""

    async def test_a_chatty_reply_yields_the_no_opinion_sentinel(self):
        """Previously this landed on the card verbatim."""
        llm = _FakeLLM("I'm sorry, but I cannot determine the meaning of this word.")
        assert await generate_word_gloss(llm, surface="ferskt", lemma="fersk", source_lang="no") == ""

    async def test_the_sentence_is_passed_through_for_sense_disambiguation(self):
        llm = _FakeLLM("fresh")
        await generate_word_gloss(
            llm, surface="ferskt", lemma="fersk", source_lang="no", feature="adj", sentence="Sporet er helt ferskt."
        )
        assert "Sporet er helt ferskt." in llm.prompts[0]


class TestTranslateTermIsNotGlossSanitised:
    """`translate_term` translates terms and short PHRASES. The gloss word-cap
    would silently truncate legitimate output to "" — a different job, and the
    two functions end with the same line, which is how it nearly got applied to
    both."""

    async def test_a_long_phrase_translation_survives(self):
        phrase = "Yes, and the ski track was completely fresh."
        assert await translate_term(_FakeLLM(phrase), "…", "no") == phrase
