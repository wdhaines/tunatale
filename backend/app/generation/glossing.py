"""The dialogue-gloss pass: its own LLM call, so the story call fits Groq's budget.

bd tunatale-yet7. ``dialogue_glosses`` must hold an entry for EVERY unique dialogue
word, so it grows with the dialogue while the completion cap does not. Measured on
the 2026-09-02 Norwegian review session: 3,213 of the story's 5,511 tokens — 58% —
were the gloss array (200 entries, 16.1 tokens each). That pushed the completion
past the ceiling ``_bump_max_tokens_after_truncation`` can reach, and review-session
generation 502'd twice over (09-06, 09-07).

The ceiling could not be raised: prompt 2,396 + completion 5,476 + margin 128 is
exactly Groq's 8,000-token per-request reservation. So the array moved out instead.

⚠️ The glosses are spliced back into the story JSON BEFORE
``build_lesson_from_story`` runs. That is what keeps this change small: the builder,
the ``token_glosses``/``verb_base_glosses`` derivation, the stored ``story`` blob and
the manual-paste round-trip all see exactly the shape they always did. A story that
ARRIVES glossed — the paste route, legacy cassettes, an authored lesson — skips the
call entirely, so this is backward compatible by construction rather than by a flag.
"""

from __future__ import annotations

import json
import logging
import re

from app.models.language import Language

logger = logging.getLogger(__name__)

# Groq's free-tier gpt-oss reservation: prompt + max_completion_tokens are held
# against 8000 per request. Duplicated from story.py deliberately — importing it
# would make this module depend on the one that depends on it.
_GROQ_FREE_TIER_REQUEST_BUDGET = 8000
_GLOSS_MARGIN = 128
# Tokens-per-character used to size the prompt. Deliberately CONSERVATIVE (3, not
# the ~3.5-4 these languages actually run at): overestimating the prompt only
# shrinks the completion cap, while underestimating it risks a hard 413.
_CHARS_PER_TOKEN = 3
# Floor for a dialogue so large the estimate leaves nothing. Such a story would
# have failed its own call first; if one ever reaches here the request 413s, and
# the caller's degradation path turns that into a warning rather than a 502.
_GLOSS_MIN_TOKENS = 256

_GLOSS_SYSTEM = "You are a concise translation assistant. Return ONLY valid JSON, no other text."

_GLOSS_PROMPT = """\
Below are {language_name} dialogue lines. List EVERY unique word that appears
(including articles, prepositions, pronouns, auxiliary and conjugated verbs,
proper names, and interjections). For each, give its lowercased surface form
exactly as written and a concise English translation appropriate to how it is
used in these lines. Conjugated and inflected forms get their specific
translation, NOT the dictionary form (e.g. "boste" -> "you will", "sem" -> "I am").

Every "word" value must be a SINGLE word. Do not emit phrases or multi-word
entries — a phrase like "ses neste tirsdag" must appear only as its individual
words. Whole-phrase meanings are carried elsewhere and are not wanted here.

The "base" key is OPTIONAL and VERBS ONLY: the bare English dictionary form with
no leading "to" (e.g. "show", not "to show"), used when a card fronts the
infinitive. Non-verbs and any entry with no dictionary form must OMIT it entirely.

Respond with ONLY a JSON array (no markdown fences, no prose):
[{{"word": "lowercased_surface", "translation": "English", "base": "verbs only"}}, ...]

Dialogue:
{dialogue}
"""


def _strip_fences(raw: str) -> str:
    """Strip markdown code fences from an LLM response."""
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*\n?", "", raw)
        raw = re.sub(r"\n?```\s*$", "", raw)
        raw = raw.strip()
    return raw


def _recover_entries(text: str) -> tuple[list[dict], int]:
    """Recover individual ``{...}`` dicts from malformed JSON via ``raw_decode``.

    Returns the recovered dicts in their original order, plus a count of the
    object starts that could NOT be decoded.

    ⚠️ The drop count is taken at ``{`` boundaries where decoding FAILED, not from
    ``text.count("{")``. A brace inside a translation value ("the { brace") sits
    inside an entry ``raw_decode`` consumes whole, so it is never visited here —
    where a raw brace count would inflate the denominator and report phantom
    losses. This log line is the only signal that a partial parse happened at all
    (bd tunatale-y0bk), so a wrong number in it defeats its own purpose.
    """
    decoder = json.JSONDecoder()
    recovered: list[dict] = []
    dropped = 0
    idx = 0
    while idx < len(text):
        try:
            obj, end = decoder.raw_decode(text, idx)
        except ValueError:  # JSONDecodeError is a ValueError
            if text[idx] == "{":
                dropped += 1
            idx += 1
            continue
        if isinstance(obj, dict):
            recovered.append(obj)
        idx = end
    return recovered, dropped


def parse_gloss_array(raw: str) -> list[dict]:
    """Parse an LLM response into a list of ``{word, translation, base?}`` dicts.

    Accepts a bare JSON array or an object wrapping it under ``dialogue_glosses``
    (the generation schema), tolerating markdown fences. Non-dict entries are
    dropped defensively.

    When a well-formed parse fails, falls back to recovering individual ``{...}``
    dicts in order.  Recovered entries keep their original position.  A WARNING
    is logged with the counts.
    """
    stripped = _strip_fences(raw.strip())
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        recovered, dropped = _recover_entries(stripped)
        logger.warning(
            "Gloss array was malformed — recovered %d entries, dropped %d",
            len(recovered),
            dropped,
        )
        return recovered
    if isinstance(data, dict):
        data = data.get("dialogue_glosses", [])
    return [g for g in data if isinstance(g, dict)]


def dialogue_lines_from_story(data: dict) -> list[str]:
    """The L2 lines of a story JSON, in order — the input the gloss call needs.

    Reads the raw story dict rather than a built ``Lesson`` so the pass can run
    before the build, which is what lets the result be spliced in as if the model
    had produced it in one call.
    """
    lines: list[str] = []
    for scene in data.get("scenes", []):
        if not isinstance(scene, dict):
            continue
        for line in scene.get("lines", []):
            if not isinstance(line, dict):
                continue
            text = line.get("text", "").strip()
            if text:
                lines.append(text)
    return lines


def build_gloss_prompt(lines: list[str], language_name: str) -> str:
    return _GLOSS_PROMPT.format(language_name=language_name, dialogue="\n".join(lines))


def gloss_max_tokens(lines: list[str]) -> int:
    """Completion cap for the gloss call, sized from what is left of the budget.

    ⚠️ NOT a constant. ``regloss_lessons`` hardcodes 2048, which is BELOW the 3,213
    tokens the measured 200-entry array needed — reusing that number would have
    moved the truncation instead of fixing it. The array scales with the dialogue,
    so its cap has to as well.
    """
    prompt_chars = len(build_gloss_prompt(lines, "X" * 16))
    estimated_prompt_tokens = prompt_chars // _CHARS_PER_TOKEN
    available = _GROQ_FREE_TIER_REQUEST_BUDGET - estimated_prompt_tokens - _GLOSS_MARGIN
    return max(_GLOSS_MIN_TOKENS, available)


async def generate_dialogue_glosses(lines: list[str], llm, language: Language) -> list[dict]:
    """One LLM call returning the ``dialogue_glosses`` array for *lines*."""
    prompt = build_gloss_prompt(lines, language.name)
    raw = await llm.complete(
        prompt,
        system_prompt=_GLOSS_SYSTEM,
        temperature=0.1,
        max_tokens=gloss_max_tokens(lines),
    )
    return parse_gloss_array(raw)


async def ensure_dialogue_glosses(data: dict, llm, language: Language) -> None:
    """Fill *data*'s ``dialogue_glosses`` in place, unless it already has them.

    THE one place the gloss pass is invoked, shared by generation and by both
    paste-import paths. Import needs it for the same reason generation does: the
    exported prompt is byte-identical to the generate-path prompt (guarded by
    ``test_drift_guard_user_prompt_identical_to_generate_path``), so it no longer
    asks for glosses either, and a pasted story would otherwise build a lesson with
    no hover translations at all.

    ⚠️ A gloss failure NEVER propagates. The asymmetry is deliberate: the story is
    expensive and already in hand, while glosses are a cheap, re-runnable
    enrichment. Losing them degrades hover translations — and ``_missing_log`` then
    names every unglossed word — where raising would throw away a valid story and
    502 the exact button bd tunatale-yet7 was filed to fix.
    """
    if llm is None or data.get("dialogue_glosses"):
        return
    lines = dialogue_lines_from_story(data)
    if not lines:
        return
    try:
        glosses = await generate_dialogue_glosses(lines, llm, language)
    except Exception as e:  # noqa: BLE001 — any failure here degrades, never raises
        logger.warning("Gloss pass failed (%s); lesson keeps its story, loses hover glosses: %s", language.code, e)
        return
    if not glosses:
        logger.warning("Gloss pass returned no usable entries (%s), retrying once", language.code)
        try:
            glosses = await generate_dialogue_glosses(lines, llm, language)
        except Exception as e:  # noqa: BLE001
            logger.warning("Gloss retry failed (%s); lesson keeps its story, loses hover glosses: %s", language.code, e)
            return
        if not glosses:
            logger.warning(
                "Gloss retry also returned no usable entries (%s); lesson loses hover glosses", language.code
            )
            return
    data["dialogue_glosses"] = glosses
