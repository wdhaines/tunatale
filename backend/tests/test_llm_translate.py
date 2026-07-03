"""Tests for LLM translation helper (translate_term)."""

from unittest.mock import AsyncMock

import pytest

from app.llm.translate import generate_word_gloss, translate_term


class TestTranslateTerm:
    """Tests for the translate_term function."""

    @pytest.mark.asyncio
    async def test_returns_translation_for_word(self):
        """translate_term returns a non-empty string for a valid word."""
        mock_client = AsyncMock()
        mock_client.complete.return_value = "hello"

        result = await translate_term(mock_client, "dober dan", "sl", "en")
        assert result == "hello"
        mock_client.complete.assert_called_once()

    @pytest.mark.asyncio
    async def test_uses_correct_system_prompt(self):
        """translate_term sends a system prompt asking for a one-line gloss."""
        mock_client = AsyncMock()
        mock_client.complete.return_value = "good day"

        await translate_term(mock_client, "dober dan", "sl", "en")
        call_args = mock_client.complete.call_args
        system_prompt = (
            call_args.kwargs.get("system_prompt") or call_args[1].get("system_prompt")
            if len(call_args[0]) > 1
            else call_args.kwargs.get("system_prompt")
        )
        assert system_prompt is not None
        assert "gloss" in system_prompt.lower() or "translation" in system_prompt.lower()

    @pytest.mark.asyncio
    async def test_returns_empty_string_on_llm_failure(self):
        """translate_term returns empty string when LLM client raises an exception."""
        mock_client = AsyncMock()
        mock_client.complete.side_effect = Exception("LLM API error")

        result = await translate_term(mock_client, "dober dan", "sl", "en")
        assert result == ""

    @pytest.mark.asyncio
    async def test_calls_llm_with_correct_parameters(self):
        """translate_term calls LLM with the text as prompt and correct language params."""
        mock_client = AsyncMock()
        mock_client.complete.return_value = "good day"

        await translate_term(mock_client, "dober dan", "sl", "en")
        call_args = mock_client.complete.call_args
        # Check that the prompt is the text to translate
        prompt = call_args.kwargs.get("prompt", "")
        assert prompt == "dober dan"
        # Check that source language is in system prompt
        system_prompt = call_args.kwargs.get("system_prompt", "")
        assert "sl" in system_prompt
        assert "en" in system_prompt

    @pytest.mark.asyncio
    async def test_default_target_language_is_english(self):
        """translate_term uses 'en' as default target language."""
        mock_client = AsyncMock()
        mock_client.complete.return_value = "good day"

        await translate_term(mock_client, "dober dan", "sl")
        # Should not raise - default target_lang="en"

    @pytest.mark.asyncio
    async def test_translates_phrase_not_just_word(self):
        """translate_term works for multi-word phrases."""
        mock_client = AsyncMock()
        mock_client.complete.return_value = "how are you"

        result = await translate_term(mock_client, "kako si", "sl", "en")
        assert result == "how are you"


class TestGenerateWordGloss:
    """Tests for the part-of-speech-aware word gloss helper."""

    @pytest.mark.asyncio
    async def test_verb_lemma_uses_bare_form_prompt(self):
        """A verb lemma (no feature) → prompt is 'lemma (POS)' + bare-form instruction."""
        mock_client = AsyncMock()
        mock_client.complete.return_value = "show"

        result = await generate_word_gloss(
            mock_client, surface="pokazem", lemma="pokazati", source_lang="sl", pos="VERB"
        )
        assert result == "show"
        kwargs = mock_client.complete.call_args.kwargs
        assert kwargs["prompt"] == "pokazati (VERB)"  # POS threaded through (backlog 10)
        sp = kwargs["system_prompt"].lower()
        assert "without" in sp and "to" in sp  # bare-form instruction

    @pytest.mark.asyncio
    async def test_base_card_gloss_includes_pos_for_disambiguation(self):
        """A base-card gloss threads the POS so an ambiguous lemma is disambiguated.

        Guards backlog 10: 'hotel' (NOUN) must reach the LLM tagged as a noun,
        not glossed POS-blind as the verb 'to want'.
        """
        mock_client = AsyncMock()
        mock_client.complete.return_value = "hotel"

        await generate_word_gloss(mock_client, surface="hotel", lemma="hotel", source_lang="sl", pos="NOUN")
        assert mock_client.complete.call_args.kwargs["prompt"] == "hotel (NOUN)"

    @pytest.mark.asyncio
    async def test_base_card_gloss_omits_pos_when_absent(self):
        """With no POS supplied, the base-card prompt stays the bare lemma."""
        mock_client = AsyncMock()
        mock_client.complete.return_value = "stay"

        await generate_word_gloss(mock_client, surface="ostati", lemma="ostati", source_lang="sl")
        assert mock_client.complete.call_args.kwargs["prompt"] == "ostati"

    @pytest.mark.asyncio
    async def test_inflection_feature_glosses_the_form(self):
        """A morphology feature → prompt references the surface + feature/sentence."""
        mock_client = AsyncMock()
        mock_client.complete.return_value = "you will be"

        result = await generate_word_gloss(
            mock_client,
            surface="boste",
            lemma="biti",
            source_lang="sl",
            feature="verb:2pl",
            sentence="Kje boste ostali",
        )
        assert result == "you will be"
        kwargs = mock_client.complete.call_args.kwargs
        assert "boste" in kwargs["prompt"]
        assert "verb:2pl" in kwargs["prompt"]
        assert "Kje boste ostali" in kwargs["prompt"]

    @pytest.mark.asyncio
    async def test_strips_whitespace(self):
        mock_client = AsyncMock()
        mock_client.complete.return_value = "  stay\n"
        result = await generate_word_gloss(mock_client, surface="ostati", lemma="ostati", source_lang="sl", pos="VERB")
        assert result == "stay"

    @pytest.mark.asyncio
    async def test_fail_soft_returns_empty_on_error(self):
        mock_client = AsyncMock()
        mock_client.complete.side_effect = Exception("LLM down")
        result = await generate_word_gloss(
            mock_client, surface="boste", lemma="biti", source_lang="sl", feature="verb:2pl"
        )
        assert result == ""
