"""LLM JSON output extraction utilities shared across generators.

Extracted from `story.py` so the curriculum planner can also parse LLM responses
without depending on ``StoryGenerator``.
"""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def _strip_fences(raw: str) -> str:
    """Strip markdown code fences from an LLM response."""
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*\n?", "", raw)
        raw = re.sub(r"\n?```\s*$", "", raw)
        raw = raw.strip()
    return raw


def parse_json_object(raw: str) -> dict:
    """Strip thinking blocks, fences, and prose, then parse JSON.

    Raises ``ValueError`` if no valid JSON object can be extracted.
    """
    cleaned = _strip_fences(_THINK_RE.sub("", raw).strip())
    candidates = [cleaned]
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end > start:
        candidates.append(cleaned[start : end + 1])
    last_error: json.JSONDecodeError | None = None
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as e:
            last_error = e
    logger.error(
        "LLM returned unparseable response (len=%d): %r",
        len(cleaned),
        cleaned[:500],
    )
    raise ValueError(f"LLM returned invalid JSON: {last_error}") from last_error


def split_reply_and_json(raw: str) -> tuple[str, dict | None]:
    """Separate a chat reply into prose and an optional JSON proposal.

    The function looks for a fenced `` ```json`` block (or a bare `` ``` `` whose
    content starts with ``{``) and parses the **last** such block.  If no fence
    is found it falls back to extracting the last balanced ``{…}`` span that
    contains the substring ``"days"``.

    Returns ``(prose, parsed_dict_or_None)``.  Never raises for missing or
    malformed JSON — only raises ``ValueError`` when a fence exists but its
    content is not a valid JSON object.
    """
    cleaned = _THINK_RE.sub("", raw).strip()

    # Find the last fenced code block that looks like JSON.
    fence_re = re.compile(r"```(\w*)\s*\n(.*?)```", re.DOTALL)
    matches = list(fence_re.finditer(cleaned))

    for match in reversed(matches):
        lang_tag = match.group(1).lower().strip()
        content = match.group(2).strip()

        is_json_fence = (lang_tag == "json") or (not lang_tag and content.startswith("{"))

        if is_json_fence:
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                raise ValueError("fenced JSON block is not a valid JSON object") from None
            if not isinstance(parsed, dict):
                raise ValueError("fenced JSON block is not a valid JSON object")
            prose = (cleaned[: match.start()] + cleaned[match.end() :]).strip()
            return (prose, parsed)

    # No qualifying fence: fall back to the last balanced {…} span containing "days".
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end > start:
        span = cleaned[start : end + 1]
        if "days" in span:
            try:
                parsed = json.loads(span)
                prose = (cleaned[:start] + cleaned[end + 1 :]).strip()
                return (prose, parsed)
            except json.JSONDecodeError:
                pass  # brace match in prose is too weak to error on

    return (cleaned.strip(), None)
