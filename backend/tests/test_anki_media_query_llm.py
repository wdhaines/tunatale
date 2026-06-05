"""Tests for LLM-generated, sense-disambiguated image search queries."""

from __future__ import annotations

from app.anki.media.query_llm import (
    IMAGE_QUERY_MODEL_VERSION,
    build_image_query_prompt,
    generate_image_query,
    parse_image_query_response,
)


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


# ── build_image_query_prompt ──────────────────────────────────────────────────


class TestBuildPrompt:
    def test_includes_word_and_english(self):
        p = build_image_query_prompt("sodišče", "court")
        assert "sodišče" in p
        assert "court" in p

    def test_includes_grammar_when_present(self):
        p = build_image_query_prompt("teči", "to run", grammar="verb, imperfective")
        assert "verb, imperfective" in p

    def test_omits_grammar_line_when_blank(self):
        p = build_image_query_prompt("teči", "to run", grammar="   ")
        assert "Grammar" not in p

    def test_includes_example_sentence_when_present(self):
        p = build_image_query_prompt("sodišče", "court", source_sentence="Šel je na sodišče.")
        assert "Šel je na sodišče." in p

    def test_omits_sentence_line_when_blank(self):
        p = build_image_query_prompt("sodišče", "court", source_sentence="")
        assert "Example" not in p


# ── parse_image_query_response ────────────────────────────────────────────────


class TestParseResponse:
    def test_plain_query(self):
        assert parse_image_query_response("courtroom interior") == "courtroom interior"

    def test_strips_surrounding_quotes(self):
        assert parse_image_query_response('"empty jail cell"') == "empty jail cell"

    def test_strips_leading_label(self):
        assert parse_image_query_response("Image search query: courtroom interior") == "courtroom interior"

    def test_takes_first_nonempty_line(self):
        assert parse_image_query_response("\n\ncourtroom\nignored trailing") == "courtroom"

    def test_none_is_skip_sentinel(self):
        assert parse_image_query_response("NONE") == ""

    def test_none_case_insensitive_with_punctuation(self):
        assert parse_image_query_response("none.") == ""

    def test_none_with_trailing_explanation_is_skip(self):
        assert parse_image_query_response("NONE - this word is abstract") == ""

    def test_empty_input_is_skip(self):
        assert parse_image_query_response("   ") == ""

    def test_punctuation_only_is_skip(self):
        assert parse_image_query_response("!!! ???") == ""

    def test_truncates_to_six_words(self):
        result = parse_image_query_response("one two three four five six seven eight")
        assert result == "one two three four five six"


# ── generate_image_query ──────────────────────────────────────────────────────


class TestGenerateImageQuery:
    async def test_cache_hit_returns_without_calling_llm(self, srs_db):
        srs_db.set_image_query("sodišče", "court", IMAGE_QUERY_MODEL_VERSION, "courtroom interior")
        llm = _FakeLLM(response="should not be used")
        result = await generate_image_query("sodišče", "court", llm=llm, db=srs_db)
        assert result == "courtroom interior"
        assert llm.prompts == []

    async def test_cache_hit_empty_string_is_honored_as_skip(self, srs_db):
        srs_db.set_image_query("zato", "therefore", IMAGE_QUERY_MODEL_VERSION, "")
        llm = _FakeLLM(response="should not be used")
        result = await generate_image_query("zato", "therefore", llm=llm, db=srs_db)
        assert result == ""
        assert llm.prompts == []

    async def test_cache_miss_calls_llm_parses_and_stores(self, srs_db):
        llm = _FakeLLM(response="courtroom interior")
        result = await generate_image_query("sodišče", "court", llm=llm, db=srs_db)
        assert result == "courtroom interior"
        assert len(llm.prompts) == 1
        # persisted for next time
        assert srs_db.get_image_query("sodišče", "court", IMAGE_QUERY_MODEL_VERSION) == "courtroom interior"

    async def test_llm_none_response_caches_skip_sentinel(self, srs_db):
        llm = _FakeLLM(response="NONE")
        result = await generate_image_query("zato", "therefore", llm=llm, db=srs_db)
        assert result == ""
        assert srs_db.get_image_query("zato", "therefore", IMAGE_QUERY_MODEL_VERSION) == ""

    async def test_llm_failure_returns_none_and_does_not_cache(self, srs_db):
        from app.llm.client import LLMError

        llm = _FakeLLM(error=LLMError("boom"))
        result = await generate_image_query("sodišče", "court", llm=llm, db=srs_db)
        assert result is None
        # not cached → next sync can retry
        assert srs_db.get_image_query("sodišče", "court", IMAGE_QUERY_MODEL_VERSION) is None

    async def test_no_llm_returns_none_on_cache_miss(self, srs_db):
        result = await generate_image_query("sodišče", "court", llm=None, db=srs_db)
        assert result is None

    async def test_no_llm_still_honors_cache_hit(self, srs_db):
        srs_db.set_image_query("sodišče", "court", IMAGE_QUERY_MODEL_VERSION, "courtroom interior")
        result = await generate_image_query("sodišče", "court", llm=None, db=srs_db)
        assert result == "courtroom interior"

    async def test_works_without_db(self):
        llm = _FakeLLM(response="courtroom interior")
        result = await generate_image_query("sodišče", "court", llm=llm, db=None)
        assert result == "courtroom interior"

    async def test_system_prompt_and_context_passed_to_llm(self):
        llm = _FakeLLM(response="man running")
        await generate_image_query("teči", "to run", llm=llm, db=None, grammar="verb", source_sentence="On teče.")
        assert llm.system_prompts[0] is not None
        assert "verb" in llm.prompts[0]
        assert "On teče." in llm.prompts[0]
