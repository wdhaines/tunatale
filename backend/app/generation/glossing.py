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

from app.llm.call_sites import CallSite
from app.models.language import Language
from app.srs.lemmatizer import get_lemmatizer, lemmatize_surfaces_in_context
from app.srs.tokenizer import tokenize

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
# Completion tokens allowed per gloss entry. MEASURED, with a deliberate 1.5x
# margin over the worst case seen: live captures ran 9.4 tok/entry (217 entries ->
# 2,037 completion tokens) and 10.4 (241 -> 2,497), and the pre-split historical
# worst case — when entries were multi-word and their translations longer — was
# 16.1. At 24 the cap clears that worst case with half again to spare while
# reserving roughly 2,000 fewer tokens than claiming the whole budget did.
#
# ⚠️ Raising this is cheap and lowering it is NOT. Too low truncates the array
# (finish_reason=length), which is the failure bd tunatale-yet7 exists for; too
# high only costs bucket headroom. Measure before touching it, and prefer the
# entries-per-response number from a real capture over a token estimate.
_TOKENS_PER_ENTRY = 24

#: Stripped before counting a word. Language-agnostic on purpose — this is a
#: budget estimate, and the registry has no business in one.
_PUNCTUATION = ".,!?\"'—–:;()[]«»…"

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

_GLOSS_TOPUP_PROMPT = """\
Below are {language_name} dialogue lines. The following words still need a gloss
entry:

{words}

Give a gloss entry for EACH word listed above, and ONLY those words. For each,
give its lowercased surface form exactly as written and a concise English
translation appropriate to how it is used in the lines shown. Conjugated and
inflected forms get their specific translation, NOT the dictionary form
(e.g. "boste" -> "you will", "sem" -> "I am").

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


# A tag needs a LETTER after `<` (or `</`), so "a < b", "<3" and "<-" are text,
# not markup. Attributes may contain anything but angle brackets.
_MARKUP_TAG = re.compile(r"</?[A-Za-z][A-Za-z0-9-]*(?:\s[^<>]*)?/?>")
_GLOSS_TEXT_FIELDS = ("word", "translation", "base")


def _strip_markup(entries: list[dict]) -> list[dict]:
    """Remove HTML tags the model put inside gloss text (bd tunatale-vayt).

    Seen live: ``{"word": "vz<span></span>amem"}``. Kept verbatim, that becomes a
    gloss keyed on a word no dialogue contains, and ``vzamem`` goes unglossed with
    nothing naming why. Stripping recovers the word. An entry whose word was
    ONLY markup is dropped. Every change is logged, because a recovery nobody can
    see is how this went unnoticed.
    """
    cleaned: list[dict] = []
    changed: list[str] = []
    dropped = 0
    for entry in entries:
        out = dict(entry)
        for field in _GLOSS_TEXT_FIELDS:
            value = out.get(field)
            if isinstance(value, str) and _MARKUP_TAG.search(value):
                out[field] = _MARKUP_TAG.sub("", value).strip()
                if field == "word":
                    changed.append(f"{value!r}->{out[field]!r}")
        if isinstance(out.get("word"), str) and not out["word"]:
            dropped += 1
            continue
        cleaned.append(out)
    if changed or dropped:
        logger.warning(
            "Gloss response carried markup — stripped from %d word(s), dropped %d markup-only entr%s: %s",
            len(changed),
            dropped,
            "y" if dropped == 1 else "ies",
            ", ".join(changed[:10]),
        )
    return cleaned


def parse_gloss_array(raw: str) -> list[dict]:
    """Parse an LLM response into a list of ``{word, translation, base?}`` dicts.

    Accepts a bare JSON array or an object wrapping it under ``dialogue_glosses``
    (the generation schema), tolerating markdown fences. Non-dict entries are
    dropped defensively.

    When a well-formed parse fails, falls back to recovering individual ``{...}``
    dicts in order.  Recovered entries keep their original position.  A WARNING
    is logged with the counts.

    Either way, HTML tags inside ``word``/``translation``/``base`` are stripped
    (see ``_strip_markup``).
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
        return _strip_markup(recovered)
    if isinstance(data, dict):
        data = data.get("dialogue_glosses", [])
    return _strip_markup([g for g in data if isinstance(g, dict)])


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


def dialogue_surface_lemmas(lines: list[str], language_code: str) -> dict[str, str]:
    """The surface→lemma map for *lines*, keyed on the lowercased surface.

    THE one copy of this loop, shared with ``build_lesson_from_story`` so the
    coverage predicate and the transcript's lemma lookup can never disagree
    (story.py used to inline it; the top-up path needs the same map). Each
    surface keeps its first-seen lemma (``setdefault``) — the same rule the
    story builder applies to its own map.
    """
    surface_lemma: dict[str, str] = {}
    lemmatizer = get_lemmatizer(language_code)
    for text in lines:
        surfaces = tokenize(text)
        lemmas = lemmatize_surfaces_in_context(surfaces, text, lemmatizer, language_code)
        for s, lem in zip(surfaces, lemmas, strict=True):
            surface_lemma.setdefault(s.lower(), lem)
    return surface_lemma


def uncovered_surfaces(surface_lemma: dict[str, str], glosses: list[dict]) -> list[str]:
    """The dialogue surfaces with no usable gloss entry, in first-seen order.

    A surface ``s`` (lemma ``L``) is COVERED iff ``s`` or ``L`` is a gloss key,
    where the key set is what ``build_lesson_from_story`` puts in
    ``token_glosses``: every gloss entry's lowercased ``word``/``lemma`` key
    that has a truthy ``translation``, plus that key's own lemma. That mirrors
    the transcript's resolution ``gloss_map.get(surface.lower()) or
    gloss_map.get(lemma)`` (app/srs/transcript.py), so what this calls covered
    and what a learner can hover are the same set. DB card translations are
    deliberately NOT consulted — generation has no DB here.
    """
    keys: set[str] = set()
    for g in glosses:
        raw_key = g.get("word") or g.get("lemma", "")
        if raw_key and g.get("translation"):
            key = raw_key.lower()
            keys.add(key)
            keys.add(surface_lemma.get(key, key))
    return [s for s in surface_lemma if s not in keys and surface_lemma[s] not in keys]


def build_gloss_prompt(lines: list[str], language_name: str) -> str:
    return _GLOSS_PROMPT.format(language_name=language_name, dialogue="\n".join(lines))


def build_gloss_topup_prompt(words: list[str], lines: list[str], language_name: str) -> str:
    """Prompt asking for gloss entries for EXACTLY *words*.

    Context is only the lines containing at least one of *words* (token
    membership), so the model sees the sentences where the missing surfaces
    actually appear and nothing else — the full dialogue would re-raise the
    truncation risk this one-call top-up is sized to avoid.
    """
    wanted = set(words)
    context_lines = [line for line in lines if wanted & {t.lower() for t in tokenize(line)}]
    return _GLOSS_TOPUP_PROMPT.format(
        language_name=language_name,
        words="\n".join(f"- {w}" for w in words),
        dialogue="\n".join(context_lines),
    )


def _unique_words(lines: list[str]) -> set[str]:
    """The distinct surface words in *lines* — one gloss entry each, near enough.

    Deliberately crude: this sizes a token budget, not a lexicon. Splitting on
    whitespace and stripping punctuation over-counts a little (a hyphenated form
    counts once, a possessive counts as its own word), and over-counting is the
    safe direction — it raises the cap.
    """
    return {w.strip(_PUNCTUATION).casefold() for line in lines for w in line.split() if w.strip(_PUNCTUATION)}


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
    # Sized from the WORK, then clamped by what the budget allows. The array has
    # one entry per unique word, so the dialogue's own vocabulary is the estimate
    # — a count nothing else has to supply and that cannot drift out of step with
    # the prompt.
    needed = len(_unique_words(lines)) * _TOKENS_PER_ENTRY
    return max(_GLOSS_MIN_TOKENS, min(needed, available))


async def generate_dialogue_glosses(lines: list[str], llm, language: Language) -> list[dict]:
    """One LLM call returning the ``dialogue_glosses`` array for *lines*."""
    prompt = build_gloss_prompt(lines, language.name)
    raw = await llm.complete(
        prompt,
        system_prompt=_GLOSS_SYSTEM,
        temperature=0.1,
        max_tokens=gloss_max_tokens(lines),
        call_site=CallSite.GLOSSING,
    )
    return parse_gloss_array(raw)


# Completion tokens a reasoning model spends BEFORE the JSON. The top-up asks
# for a handful of words, so a cap sized from the entries alone is almost all
# reasoning headroom-free: at max(256, n * 24) a live 5-word top-up came back
# TRUNCATED mid-entry (recorded 2026-09-22). cloze_quality.py::_MAX_TOKENS
# measured the same failure (5 of 6 replies finish_reason=length at 120) and
# settled on 1500; the main pass never shows it only because a whole dialogue's
# n * 24 already dwarfs the reasoning.
_TOPUP_REASONING_TOKENS = 1500


def gloss_topup_max_tokens(words: list[str], lines: list[str]) -> int:
    """Completion cap for the top-up: reasoning allowance plus the entries,
    clamped to what the free-tier request reservation leaves after the prompt."""
    prompt_chars = len(build_gloss_topup_prompt(words, lines, "X" * 16))
    available = _GROQ_FREE_TIER_REQUEST_BUDGET - prompt_chars // _CHARS_PER_TOKEN - _GLOSS_MARGIN
    return min(_TOPUP_REASONING_TOKENS + len(words) * _TOKENS_PER_ENTRY, available)


async def generate_gloss_topup(words: list[str], lines: list[str], llm, language: Language) -> list[dict]:
    """One LLM call toping up gloss entries for exactly *words*.

    Sized by :func:`gloss_topup_max_tokens`; the context is filtered to the lines
    that mention a missing word, so this call stays small by construction.
    """
    prompt = build_gloss_topup_prompt(words, lines, language.name)
    raw = await llm.complete(
        prompt,
        system_prompt=_GLOSS_SYSTEM,
        temperature=0.1,
        max_tokens=gloss_topup_max_tokens(words, lines),
        call_site=CallSite.GLOSSING,
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
    # ONE targeted top-up for whatever the main pass left uncovered. A partial
    # reply is a completion, not a miss — the original code only retried an
    # EMPTY array, and 226-of-227 glossed then sat incomplete forever
    # (tunatale-grv3). Never a loop: one call, then a warning for whatever is
    # still missing, and the glosses already obtained are kept unchanged.
    surface_lemma = dialogue_surface_lemmas(lines, language.code)
    missing = uncovered_surfaces(surface_lemma, glosses)
    if missing:
        try:
            topup = await generate_gloss_topup(missing, lines, llm, language)
        except Exception as e:  # noqa: BLE001 — any failure degrades, never raises
            topup = []
            logger.warning("Gloss top-up failed (%s); glosses keep as-is: %s", language.code, e)
        missing_set = set(missing)
        # Only entries WITH a translation count as existing — the same rule
        # uncovered_surfaces applies. An empty-translation entry is exactly what
        # made its word "missing", so it must not also block the answer.
        existing_keys = {
            (g.get("word") or g.get("lemma", "")).lower()
            for g in glosses
            if (g.get("word") or g.get("lemma", "")) and g.get("translation")
        }
        for entry in topup:
            raw = entry.get("word") or entry.get("lemma", "")
            if not raw or not entry.get("translation"):
                continue
            key = raw.lower()
            # Only the words we asked about, and never an existing key — the
            # original translation wins over a top-up's differing one.
            if key in missing_set and key not in existing_keys:
                glosses.append(entry)
                existing_keys.add(key)
        still_missing = uncovered_surfaces(surface_lemma, glosses)
        if still_missing:
            logger.warning(
                "Gloss top-up left %d word(s) unglossed (%s): %s",
                len(still_missing),
                language.code,
                " ".join(sorted(still_missing)[:10]),
            )
    data["dialogue_glosses"] = glosses
