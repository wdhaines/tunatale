"""Tests for shared LLM JSON parsing utilities."""

import pytest

from app.generation.json_parsing import parse_json_object, split_reply_and_json

# ── parse_json_object ────────────────────────────────────────────────


def test_parse_json_object_basic():
    raw = '{"title": "Hello", "count": 42}'
    assert parse_json_object(raw) == {"title": "Hello", "count": 42}


def test_parse_json_object_strips_think_block():
    raw = '<think>I will emit JSON now.</think>\n{"title": "Test"}'
    assert parse_json_object(raw) == {"title": "Test"}


def test_parse_json_object_strips_think_block_with_braces():
    """<think> block containing braces {like this} should be stripped before brace matching."""
    raw = '<think>I will emit JSON with a {title} key.</think>\n{"title": "Test"}'
    assert parse_json_object(raw) == {"title": "Test"}


def test_parse_json_object_strips_markdown_fences():
    raw = '```json\n{"title": "Test"}\n```'
    assert parse_json_object(raw) == {"title": "Test"}


def test_parse_json_object_strips_bare_fences():
    raw = '```\n{"title": "Test"}\n```'
    assert parse_json_object(raw) == {"title": "Test"}


def test_parse_json_object_handles_prose_preamble():
    """Prose before a fenced block should be ignored."""
    raw = '**Lesson Title:** Here\'s the lesson.\n\n```json\n{"title": "Test"}\n```'
    assert parse_json_object(raw) == {"title": "Test"}


def test_parse_json_object_tolerates_trailing_prose():
    raw = '{"title": "Test"}\n\nHope this helps!'
    assert parse_json_object(raw) == {"title": "Test"}


def test_parse_json_object_invalid_raises_value_error():
    with pytest.raises(ValueError, match="LLM returned invalid JSON"):
        parse_json_object("not json")


def test_parse_json_object_empty_string_raises():
    with pytest.raises(ValueError, match="LLM returned invalid JSON"):
        parse_json_object("")


def test_parse_json_object_only_prose_raises():
    with pytest.raises(ValueError):
        parse_json_object("This is just some text without JSON.")


def test_parse_json_object_think_block_with_braces_and_fences():
    """Combined think block + fences + prose."""
    raw = '<think>Let me plan this.</think>\n```json\n{"title": "Test"}\n```\nHope this works!'
    assert parse_json_object(raw) == {"title": "Test"}


# ── split_reply_and_json ─────────────────────────────────────────────


def test_split_reply_json_think_block_stripped():
    """<think> blocks are stripped before fence detection."""
    raw = '<think>I need a plan</think>\n```json\n{"days": 5}\n```'
    prose, data = split_reply_and_json(raw)
    assert data == {"days": 5}
    assert prose == ""


def test_split_reply_json_fenced_json():
    """A simple fenced ```json block is parsed."""
    raw = '```json\n{"days": 5, "focus": "cafe"}\n```'
    prose, data = split_reply_and_json(raw)
    assert data == {"days": 5, "focus": "cafe"}
    assert prose == ""


def test_split_reply_json_bare_fence_with_json():
    """A bare ``` fence whose content starts with { is accepted."""
    raw = '```\n{"days": 3}\n```'
    prose, data = split_reply_and_json(raw)
    assert data == {"days": 3}
    assert prose == ""


def test_split_reply_json_prose_and_fence():
    """Prose before and after the fence, prose is reassembled."""
    raw = 'Here is the plan.\n```json\n{"days": 5}\n```\nLet me know if ok.'
    prose, data = split_reply_and_json(raw)
    assert data == {"days": 5}
    assert "Here is the plan." in prose
    assert "Let me know if ok." in prose
    assert "```" not in prose


def test_split_reply_json_two_fences_last_wins():
    """When multiple fenced blocks exist, the last one is used."""
    raw = '```json\n{"days": 1}\n```\n```json\n{"days": 2}\n```'
    prose, data = split_reply_and_json(raw)
    assert data == {"days": 2}


def test_split_reply_json_malformed_fence_raises():
    """A fenced ```json block with invalid JSON raises ValueError."""
    raw = "```json\n{invalid}\n```"
    with pytest.raises(ValueError, match="fenced JSON block is not a valid JSON object"):
        split_reply_and_json(raw)


def test_split_reply_json_bare_brace_fallback_with_days():
    """No fence: last balanced {…} containing 'days' is parsed."""
    raw = 'I propose the following: {"days": 7, "focus": "grammar"} for the plan.'
    prose, data = split_reply_and_json(raw)
    assert data == {"days": 7, "focus": "grammar"}
    assert "I propose the following:" in prose
    assert "for the plan." in prose


def test_split_reply_json_bare_brace_missing_days_not_parsed():
    """A bare {…} without 'days' is not treated as JSON."""
    raw = "Some text with {a brace span} but no days keyword."
    prose, data = split_reply_and_json(raw)
    assert data is None
    assert "Some text" in prose


def test_split_reply_json_malformed_bare_brace_treated_as_prose():
    """A {…} with 'days' that fails to parse is treated as prose, no error."""
    raw = "The plan: {days: broken} should not crash."
    prose, data = split_reply_and_json(raw)
    assert data is None
    assert "The plan:" in prose


def test_split_reply_json_prose_only():
    """A pure-chat turn with no JSON returns (text, None)."""
    raw = "Hello, how are you today?"
    prose, data = split_reply_and_json(raw)
    assert data is None
    assert prose == raw


def test_split_reply_json_empty_string():
    prose, data = split_reply_and_json("")
    assert data is None
    assert prose == ""


def test_split_reply_json_non_dict_json_is_rejected():
    """A fenced JSON array is not a valid JSON object."""
    raw = "```json\n[1, 2, 3]\n```"
    with pytest.raises(ValueError, match="fenced JSON block is not a valid JSON object"):
        split_reply_and_json(raw)


def test_split_reply_json_bare_brace_with_days_and_fence_wins():
    """When both fence and bare brace exist, fence wins."""
    raw = 'Some text {"days": 1}\n```json\n{"days": 99}\n```\nmore text'
    prose, data = split_reply_and_json(raw)
    assert data == {"days": 99}
    assert "Some text" in prose
    assert "more text" in prose
    assert '{"days": 1}' in prose


def test_split_reply_json_think_block_inside_fence_content_stays():
    """<think> in prose before fence is stripped; <think> inside fence content is part of JSON."""
    raw = '<think>Reasoning</think>```json\n{"days": 3}\n```'
    prose, data = split_reply_and_json(raw)
    assert data == {"days": 3}
    assert "<think>" not in prose
    assert "Reasoning" not in prose


def test_split_reply_json_non_json_fence_skipped_falls_to_bare_brace():
    """A fenced block tagged with a non-json language is skipped; bare brace fallback applies."""
    raw = 'Some text\n```python\nx = 1\n```\nand then {"days": 5} at the end.'
    prose, data = split_reply_and_json(raw)
    assert data == {"days": 5}
    assert "Some text" in prose
    assert "```python" in prose  # non-JSON fence stays in prose


def test_split_reply_json_valid_fence_before_invalid_bare_fence_wins():
    """Backlog #4: a malformed later fence must not discard an earlier valid one.

    The LLM often appends a pseudo-code sketch after the real proposal; the
    parser previously raised on the last (malformed) fence and failed the turn.
    """
    raw = '```json\n{"days": 4}\n```\nHere is a sketch:\n```\n{pseudo-code, not json}\n```'
    prose, data = split_reply_and_json(raw)
    assert data == {"days": 4}
    assert "Here is a sketch:" in prose


def test_split_reply_json_valid_fence_before_malformed_json_fence_wins():
    """An earlier valid ```json fence wins over a later malformed ```json fence."""
    raw = '```json\n{"days": 4}\n```\nor maybe:\n```json\n{oops}\n```'
    prose, data = split_reply_and_json(raw)
    assert data == {"days": 4}


def test_split_reply_json_only_malformed_bare_fence_no_raise():
    """A malformed bare {-fence with no json-tagged fence anywhere must not raise.

    (The bare-brace fallback spans first-{…last-}, which here swallows the fence
    content and fails to parse — so no data is recovered, but the turn survives
    as prose instead of erroring.)
    """
    raw = 'Some text\n```\n{not json}\n```\nand a plan {"days": 6} inline.'
    prose, data = split_reply_and_json(raw)
    assert data is None
    assert "Some text" in prose


def test_split_reply_json_bare_brace_parses_as_non_dict_returns_prose():
    """A brace span with 'days' that parses as JSON but not a dict is treated as prose."""
    raw = 'Some text ["days", 3] and more.'
    prose, data = split_reply_and_json(raw)
    assert data is None
    assert "Some text" in prose
