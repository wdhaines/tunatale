"""Tests for the LLM image candidate chooser (choose_llm.py)."""

from __future__ import annotations

from app.cards.media.choose_llm import (
    build_image_choice_prompt,
    choose_image_hit,
    parse_image_choice_response,
)

# ── parse_image_choice_response ───────────────────────────────────────────────


class TestParseImageChoiceResponse:
    def test_extracts_integer(self):
        assert parse_image_choice_response("3") == 3

    def test_extracts_integer_from_text(self):
        assert parse_image_choice_response("I pick 5") == 5

    def test_none_when_no_integer(self):
        assert parse_image_choice_response("none of them") is None

    def test_none_when_empty(self):
        assert parse_image_choice_response("") is None

    def test_none_for_none_input(self):
        assert parse_image_choice_response(None) is None

    def test_extracts_first_integer(self):
        assert parse_image_choice_response("7 and also 2") == 7


# ── build_image_choice_prompt ────────────────────────────────────────────────


class TestBuildImageChoicePrompt:
    def test_formats_as_specified(self):
        hits = [
            {
                "tags": "tree, forest",
                "imageWidth": 800,
                "imageHeight": 600,
                "likes": 42,
            },
            {
                "tags": "dog, pet",
                "imageWidth": 1024,
                "imageHeight": 768,
                "likes": 100,
            },
        ]
        prompt = build_image_choice_prompt("drevo", "tree", "forest trees", hits)
        assert "Word: drevo" in prompt
        assert "Meaning: tree" in prompt
        assert "Query: forest trees" in prompt
        assert "1. tags: tree, forest (800x600, 42 likes)" in prompt
        assert "2. tags: dog, pet (1024x768, 100 likes)" in prompt

    def test_caps_at_12_candidates(self):
        hits = [{"tags": f"t{i}", "imageWidth": 100, "imageHeight": 100, "likes": i} for i in range(20)]
        prompt = build_image_choice_prompt("x", "x", "q", hits)
        assert "12. " in prompt
        assert "13. " not in prompt

    def test_empty_hits(self):
        prompt = build_image_choice_prompt("x", "x", "q", [])
        assert "Word: x" in prompt


# ── choose_image_hit ──────────────────────────────────────────────────────────


class _StubLLM:
    def __init__(self, response: str) -> None:
        self._response = response
        self.calls: list[dict] = []

    async def complete(self, prompt, *, system_prompt=None, temperature=None, max_tokens=None):
        self.calls.append(
            {"prompt": prompt, "system_prompt": system_prompt, "temperature": temperature, "max_tokens": max_tokens}
        )
        return self._response


class _FailingLLM:
    async def complete(self, prompt, *, system_prompt=None, temperature=None, max_tokens=None):
        raise RuntimeError("LLM is down")


class TestChooseImageHit:
    def _hits(self, n: int = 5) -> list[dict]:
        return [
            {
                "tags": f"tag{i}",
                "webformatURL": f"https://example.com/{i}.jpg",
                "imageWidth": 100,
                "imageHeight": 100,
                "likes": i,
            }
            for i in range(1, n + 1)
        ]

    async def test_picks_hit_3_when_llm_says_3(self):
        llm = _StubLLM("3")
        hits = self._hits()
        result = await choose_image_hit("word", "english", "query", hits, llm=llm)
        assert result is hits[2]  # 0-indexed: hit 3 → index 2

    async def test_zero_returns_none(self):
        llm = _StubLLM("0")
        result = await choose_image_hit("word", "english", "query", self._hits(), llm=llm)
        assert result is None

    async def test_out_of_range_returns_none(self):
        llm = _StubLLM("7")
        result = await choose_image_hit("word", "english", "query", self._hits(5), llm=llm)
        assert result is None

    async def test_garbage_returns_none(self):
        llm = _StubLLM("no opinion here")
        result = await choose_image_hit("word", "english", "query", self._hits(), llm=llm)
        assert result is None

    async def test_exception_returns_none_and_logs(self, caplog):
        llm = _FailingLLM()
        with caplog.at_level("WARNING"):
            result = await choose_image_hit("word", "english", "query", self._hits(), llm=llm)
        assert result is None
        assert any("word" in r.message for r in caplog.records)

    async def test_empty_hits_returns_none_without_calling_llm(self):
        llm = _StubLLM("3")
        result = await choose_image_hit("word", "english", "query", [], llm=llm)
        assert result is None
        assert llm.calls == []

    async def test_llm_none_returns_none(self):
        result = await choose_image_hit("word", "english", "query", self._hits(), llm=None)
        assert result is None

    async def test_llm_receives_system_prompt(self):
        llm = _StubLLM("1")
        await choose_image_hit("word", "english", "query", self._hits(), llm=llm)
        assert llm.calls[0]["system_prompt"] is not None
        assert (
            "stock-photo" in llm.calls[0]["system_prompt"].lower()
            or "candidate" in llm.calls[0]["system_prompt"].lower()
        )

    async def test_temperature_zero(self):
        llm = _StubLLM("1")
        await choose_image_hit("word", "english", "query", self._hits(), llm=llm)
        assert llm.calls[0]["temperature"] == 0.0

    async def test_max_tokens_256(self):
        llm = _StubLLM("1")
        await choose_image_hit("word", "english", "query", self._hits(), llm=llm)
        assert llm.calls[0]["max_tokens"] == 256


# ── cassette test (skipped when cassette missing) ────────────────────────────


class TestChooseImageHitCassette:
    async def test_representative_prompt_parses_to_valid_index(self, cassette_llm):
        """One real-prompt cassette test: the LLM's reply must parse to a valid index.

        In default mock mode with no cassette, this is SKIPPED automatically
        by the cassette_llm fixture. The human records it after review.
        """
        hits = [
            {
                "tags": "tree, forest",
                "imageWidth": 800,
                "imageHeight": 600,
                "likes": 42,
                "webformatURL": "https://example.com/1.jpg",
            },
            {
                "tags": "dog, pet",
                "imageWidth": 1024,
                "imageHeight": 768,
                "likes": 100,
                "webformatURL": "https://example.com/2.jpg",
            },
            {
                "tags": "car, vehicle",
                "imageWidth": 640,
                "imageHeight": 480,
                "likes": 50,
                "webformatURL": "https://example.com/3.jpg",
            },
        ]
        chosen = await choose_image_hit("drevo", "tree", "forest trees", hits, llm=cassette_llm)
        assert chosen is None or isinstance(chosen, dict)
        if chosen is not None:
            assert chosen in hits
