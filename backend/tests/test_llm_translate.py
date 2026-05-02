"""Tests for LLM translation helper (translate_term)."""

from unittest.mock import AsyncMock

import pytest

from app.llm.translate import translate_term


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
