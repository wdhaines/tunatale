"""Resolve a table lemmatizer's ambiguous words with one batched LLM call per lesson.

A lemma table (:mod:`app.srs.lemma_table`) gives the model's exact lemma once the
UPOS is known, and a default UPOS otherwise. About 7% of lesson tokens have
readings whose lemmas differ (``så`` → *se* or *så*, ``noe`` → *noe* or *noen*);
for those the default agreed with in-context Stanza only 415/516 times
(tunatale-kbb.18, 2026-09-18). Context decides, so an LLM reads the sentence and
picks the TAG — a closed choice among the table's own readings, so it can never
put a lemma into the card keyspace that the model would not have produced.

Runs at publish time (:func:`app.generation.publishing.publish_lesson`), where an
LLM call is already normal, and writes finished analyses into the persistent
``lemma_analysis_cache``. Every later reader — transcript, listen preview, card
matching — hits that cache and never waits on the LLM. Failures are swallowed
with a warning: the default readings are still served, and generation must
never break over a lemma.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

import anyio

from app.llm.call_sites import CallSite
from app.srs.lemma_table import tokenize
from app.srs.lemmatizer import _serialize_analyses

if TYPE_CHECKING:
    from app.models.lesson import Lesson
    from app.srs.database import SRSDatabase
    from app.srs.lemma_table import Reading, TableLemmatizer

logger = logging.getLogger(__name__)

SENTENCES_PER_CALL = 20

SYSTEM_PROMPT = (
    "You are a {language} part-of-speech annotator. For each numbered item, decide how "
    "the quoted word is used in its sentence and answer with exactly one of the listed "
    "tags (Universal Dependencies UPOS). Reply with a JSON object mapping each item "
    "number to its tag, and nothing else."
)

# (sentence, token index, token, readings) for one ambiguous token.
Item = tuple[str, int, str, "list[Reading]"]


def _is_ambiguous(readings: list[Reading]) -> bool:
    return len({r.lemma.lower() for r in readings}) > 1


def ambiguous_items(sentence: str, lemmatizer: TableLemmatizer) -> list[Item]:
    """The tokens of *sentence* whose readings disagree on the lemma."""
    items: list[Item] = []
    for i, token in enumerate(tokenize(sentence)):
        readings = lemmatizer.readings(token)
        if _is_ambiguous(readings):
            items.append((sentence, i, token, readings))
    return items


def build_prompt(items: list[Item]) -> str:
    """Number *items* and list each one's tag options with the lemma each implies."""
    lines = []
    for n, (sentence, i, token, readings) in enumerate(items, start=1):
        options = " | ".join(f"{r.upos} ({r.lemma})" for r in readings)
        lines.append(f'{n}. "{token}" (word {i + 1}) in: {sentence}\n   tags: {options}')
    return "\n".join(lines)


def parse_reply(reply: str, items: list[Item]) -> dict[tuple[str, int], str]:
    """Map ``(sentence, token index)`` to the chosen tag, keeping only valid choices.

    An unparseable reply, a missing item, or a tag outside that item's readings is
    dropped, and the default reading stands for it. (The slice runs from the first
    ``{`` to the last ``}``, so it decodes to an object or not at all.)
    """
    try:
        data = json.loads(reply[reply.index("{") : reply.rindex("}") + 1])
    except ValueError:
        logger.warning("Lemma resolver: unparseable LLM reply %r", reply[:200])
        return {}
    chosen: dict[tuple[str, int], str] = {}
    for n, (sentence, i, _token, readings) in enumerate(items, start=1):
        tag = str(data.get(str(n), "")).strip().upper()
        if tag in {r.upos for r in readings}:
            chosen[(sentence, i)] = tag
    return chosen


async def resolve_sentences(
    sentences: list[str],
    language_code: str,
    language_name: str,
    lemmatizer: TableLemmatizer,
    model_version: str,
    srs_db: SRSDatabase,
    llm,
) -> int:
    """Resolve and cache every not-yet-cached sentence that has an ambiguous word.

    Sentences already cached — under the table's key, or under the real model's
    (exact) — are left alone. Returns the number of sentences written.
    """

    def _pending() -> list[list[Item]]:
        pending = []
        for s in dict.fromkeys(sentences):
            if srs_db.get_sentence_analysis(s, language_code, model_version) is not None:
                continue
            if any(
                srs_db.get_sentence_analysis(s, language_code, v) is not None
                for v in lemmatizer.compatible_cache_versions
            ):
                continue
            items = ambiguous_items(s, lemmatizer)
            if items:
                pending.append(items)
        return pending

    pending = await anyio.to_thread.run_sync(_pending)
    system = SYSTEM_PROMPT.format(language=language_name)
    written = 0
    for b in range(0, len(pending), SENTENCES_PER_CALL):
        group = pending[b : b + SENTENCES_PER_CALL]
        items = [item for sentence_items in group for item in sentence_items]
        reply = await llm.complete(
            build_prompt(items),
            system_prompt=system,
            temperature=0.0,
            max_tokens=2048,
            call_site=CallSite.LEMMA_RESOLVE,
        )
        chosen = parse_reply(reply, items)

        def _write(group: list[list[Item]] = group, chosen: dict[tuple[str, int], str] = chosen) -> None:
            for sentence_items in group:
                sentence = sentence_items[0][0]
                tags = {i: tag for (s, i), tag in chosen.items() if s == sentence}
                analyses = lemmatizer.analyze_sentence_with_tags(sentence, language_code, tags)
                srs_db.set_sentence_analysis(sentence, language_code, model_version, _serialize_analyses(analyses))

        await anyio.to_thread.run_sync(_write)
        written += len(group)
    return written


def lesson_sentences(lesson: Lesson) -> list[str]:
    """Every L2 sentence a reader will analyze: the lesson's phrases plus its key phrases."""
    sentences = [p.text for s in lesson.sections for p in s.phrases if p.language_code == lesson.language_code]
    sentences += [kp.phrase for kp in lesson.key_phrases]
    return sentences


async def resolve_lesson_lemmas(
    lesson: Lesson,
    srs_db: SRSDatabase | None,
    llm,
    *,
    lemmatizer: object | None = None,
    model_version: str | None = None,
) -> int:
    """Resolve *lesson*'s ambiguous words into the analysis cache. Never raises.

    A no-op (0) without a database, without an LLM, or when the language's engine
    is not a table engine — the real model needs no help, and lowercase has
    nothing to choose between.
    """
    if srs_db is None or llm is None:
        return 0
    try:
        from app.languages import get_language
        from app.srs.lemma_table import TableLemmatizer
        from app.srs.lemmatizer import get_lemmatizer, model_version_for

        if lemmatizer is None:
            lemmatizer = get_lemmatizer(lesson.language_code)
        if not isinstance(lemmatizer, TableLemmatizer):
            return 0
        if model_version is None:
            model_version = model_version_for(lemmatizer)
        return await resolve_sentences(
            lesson_sentences(lesson),
            lesson.language_code,
            get_language(lesson.language_code).name,
            lemmatizer,
            model_version,
            srs_db,
            llm,
        )
    except Exception:
        logger.warning("Lemma resolution failed for lesson; default readings stand", exc_info=True)
        return 0
