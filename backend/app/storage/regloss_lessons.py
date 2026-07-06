"""One-shot migration: re-gloss legacy lessons.

Lessons generated under the old prompt have ``token_glosses`` keyed by POS-blind,
conjugation-collapsed, sometimes hallucinated lemmas — e.g. ``{"biti": "am",
"hoteti": "hotel"}`` — so conjugated surfaces like ``boste`` fall through to the
wrong translation. This migration re-glosses each lesson by translating the
*actual dialogue surfaces in context* and rewriting
``generation_metadata["token_glosses"]`` with surface keys plus a sentence-aware
lemma fallback. Story text and audio are left untouched.

Run once, manually, against a live LLM:

    uv run python -m app.storage.regloss_lessons --language sl

It is deliberately NOT a startup hook: it calls the LLM (non-deterministic), so
it runs once on demand, not on every boot. Re-keying the old data in place is
impossible — the old translations themselves are wrong (the surface-specific
meaning was never captured), so we re-translate from the stored dialogue.
"""

from __future__ import annotations

import json
import logging
import re

from app.languages import get_language
from app.models.language import Language
from app.models.lesson import Lesson, SectionType
from app.srs.lemmatizer import Lemmatizer, get_lemmatizer, lemmatize_surfaces_in_context
from app.srs.tokenizer import tokenize

logger = logging.getLogger(__name__)

_REGLOSS_SYSTEM = "You are a concise translation assistant. Return ONLY valid JSON, no other text."

_REGLOSS_PROMPT = """\
Below are {language_name} dialogue lines. List EVERY unique word that appears
(including articles, prepositions, pronouns, auxiliary and conjugated verbs,
proper names, and interjections). For each, give its lowercased surface form
exactly as written and a concise English translation appropriate to how it is
used in these lines. Conjugated and inflected forms get their specific
translation, NOT the dictionary form (e.g. "boste" -> "you will", "sem" -> "I am").

Respond with ONLY a JSON array (no markdown fences, no prose):
[{{"word": "lowercased_surface", "translation": "English"}}, ...]

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


def dialogue_lines(lesson: Lesson) -> list[str]:
    """L2 NATURAL_SPEED phrase texts — the lines the transcript glosses."""
    lines: list[str] = []
    for section in lesson.sections:
        if section.section_type != SectionType.NATURAL_SPEED:
            continue
        for phrase in section.phrases:
            if phrase.language_code == lesson.language_code:
                lines.append(phrase.text)
    return lines


def build_regloss_prompt(lines: list[str], language_name: str) -> str:
    return _REGLOSS_PROMPT.format(language_name=language_name, dialogue="\n".join(lines))


def parse_gloss_array(raw: str) -> list[dict]:
    """Parse the LLM response into a list of ``{word, translation}`` dicts.

    Accepts a bare JSON array or an object wrapping it under ``dialogue_glosses``
    (the generation schema), tolerating markdown fences. Non-dict entries are
    dropped defensively.
    """
    data = json.loads(_strip_fences(raw.strip()))
    if isinstance(data, dict):
        data = data.get("dialogue_glosses", [])
    return [g for g in data if isinstance(g, dict)]


def _surface_lemma_map(lines: list[str], lemmatizer: Lemmatizer, language_code: str) -> dict[str, str]:
    """Sentence-aware ``surface(lower) -> lemma`` over the dialogue.

    Uses ``lemmatize_surfaces_in_context`` (not single-word ``lemmatize``) so the
    fallback lemma keys match the transcript's POS-aware lemmas; first occurrence
    of a surface wins.
    """
    mapping: dict[str, str] = {}
    for text in lines:
        surfaces = tokenize(text)
        lemmas = lemmatize_surfaces_in_context(surfaces, text, lemmatizer, language_code)
        for surface, lemma in zip(surfaces, lemmas, strict=True):
            mapping.setdefault(surface.lower(), lemma)
    return mapping


async def regloss_lesson(
    lesson: Lesson,
    llm,
    lemmatizer: Lemmatizer,
    language: Language,
) -> dict[str, str] | None:
    """Return a fresh surface-keyed ``token_glosses`` map for *lesson*.

    ``None`` when the lesson has no L2 dialogue to gloss (nothing to do — the LLM
    is not called).
    """
    lines = dialogue_lines(lesson)
    if not lines:
        return None
    prompt = build_regloss_prompt(lines, language.name)
    raw = await llm.complete(prompt, system_prompt=_REGLOSS_SYSTEM, temperature=0.1, max_tokens=2048)
    surface_lemma = _surface_lemma_map(lines, lemmatizer, language.code)
    token_glosses: dict[str, str] = {}
    for g in parse_gloss_array(raw):
        word = (g.get("word") or "").strip().lower()
        translation = (g.get("translation") or "").strip()
        if not word or not translation:
            continue
        token_glosses[word] = translation  # surface key: the specific translation
        lemma = surface_lemma.get(word)
        if lemma:
            token_glosses.setdefault(lemma, translation)  # generic fallback; first surface wins
    return token_glosses


async def regloss_all(store, llm, lemmatizer: Lemmatizer, language: Language) -> int:
    """Re-gloss every lesson in *store*. Returns the number of lessons rewritten.

    Lessons with no dialogue, or for which the LLM produced no usable glosses, are
    left untouched (we never clobber existing data with an empty map).
    """
    updated = 0
    for lesson_id, curriculum_id, day, lesson in store.list_lessons():
        new_glosses = await regloss_lesson(lesson, llm, lemmatizer, language)
        if not new_glosses:
            continue
        meta = lesson.generation_metadata or {}
        meta["token_glosses"] = new_glosses
        lesson.generation_metadata = meta
        store.save_lesson(lesson_id, curriculum_id, day, lesson)
        updated += 1
    return updated


async def _main() -> None:  # pragma: no cover — CLI wiring, run once against live Groq
    import argparse

    parser = argparse.ArgumentParser(description="Re-gloss legacy lessons with surface-keyed translations.")
    parser.add_argument("--language", default="sl", help="language code (default: sl)")
    args = parser.parse_args()

    try:
        language = get_language(args.language)
    except KeyError as e:
        raise SystemExit(str(e)) from None

    from app.config import settings
    from app.llm.client import LLMClient
    from app.storage.store import ContentStore

    logging.basicConfig(level=logging.INFO)
    store = ContentStore(settings.database_url.replace("sqlite:///", ""))
    llm = LLMClient(groq_api_key=settings.groq_api_key, groq_model=settings.llm_model)
    try:
        count = await regloss_all(store, llm, get_lemmatizer(language.code), language)
        logger.info("Re-glossed %d lesson(s)", count)
    finally:
        store.close()


if __name__ == "__main__":  # pragma: no cover — CLI guard
    import asyncio

    asyncio.run(_main())
