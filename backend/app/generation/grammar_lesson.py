"""The grammar lesson: one affix pattern, drilled on roots the learner understands (tunatale-ve4p.5).

A grammar lesson is an affix drill (``app.generation.affix_drill``) and nothing
else: no story, no key phrases, no model call. What goes into it is read off
two things the learner already has.

**Their cards** decide which roots. A root is drilled once its RECOGNITION card
is in review or marked known: the owner's call, 2026-10-07, "understanding is
enough". Production of the bare root is not asked for, because the bare root is
rarely what is said; the affixed form is.

**Their lessons** decide how each root is used, and what the drill ends on.

* A root whose forms do not mean what the root means has to be MODELLED:
  ``uban`` is "accompany" and ``mouban`` is "will go along", and nothing in a
  prompt leads from one to the other (``DrillRoot.transparent``).
* The rest are ranked by how many of their forms the lessons have already
  used. The best-met join the modelled round, up to :data:`MODELLED` roots.
* Whatever is left is NEW: named, not modelled, least-met last. A root the
  lessons never used in this pattern is the cleanest test there is, because
  getting it right can only be the affix.
* The drill ends on up to :data:`MAX_LINES` whole lines from the lessons that
  use a drilled form, shortest first.

Nothing here writes anything. :func:`plan_grammar_lesson` reads,
:func:`build_grammar_lesson` builds a ``Lesson`` in memory, and the caller
publishes it the way every lesson is published.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.generation.affix_drill import MIN_ROOTS, DrillRoot, build_affix_drill, drill_root
from app.generation.section_builder import build_affix_drill_section
from app.languages import get_a1_morphology
from app.models.curriculum import CurriculumDay
from app.models.language import Language
from app.models.lesson import Lesson, SectionType, extract_sentence_translations_from_translated
from app.models.srs_item import Direction, SRSState
from app.srs.a1_morphology import AffixPattern
from app.storage.store import ContentStore

MODELLED = 3
MAX_LINES = 3

_UNDERSTOOD = (SRSState.REVIEW, SRSState.KNOWN)
_NO_PATTERNS = "the language registers no affix patterns"
_WORD = re.compile(r"[\w'-]+")
# A sentence end with more text after it: the line is two sentences.
_SECOND_SENTENCE = re.compile(r"[.?!…]\s+\S")


@dataclass(frozen=True)
class GrammarPlan:
    """What one grammar lesson drills: the pattern, who is modelled, who is not, and the lines."""

    pattern: AffixPattern
    roots: tuple[str, ...]
    new_roots: tuple[str, ...]
    lines: tuple[tuple[str, str], ...]
    forms: tuple[str, ...]  # every form drilled, modelled roots first


def _fold(word: str) -> str:
    return word.casefold().replace("-", "")


def pattern_title(pattern: AffixPattern) -> str:
    """``Affix drill: mo- / mi-``, from the pattern's own features."""
    return "Affix drill: " + " / ".join(f"{feature.rsplit(':', 1)[-1]}-" for feature in pattern.features)


def find_pattern(language_code: str, key: str) -> AffixPattern | None:
    """The language's pattern called *key*, or ``None``."""
    bundle = get_a1_morphology(language_code)
    return next((p for p in bundle.patterns if p.key == key), None) if bundle is not None else None


def _understood(language_code: str, pattern: AffixPattern, srs_db) -> dict[str, DrillRoot]:
    bundle = get_a1_morphology(language_code)
    if bundle is None or bundle.pattern_roots is None:
        raise ValueError(f"{language_code}: {_NO_PATTERNS}")
    found: dict[str, DrillRoot] = {}
    for root in bundle.pattern_roots(pattern):
        ready = drill_root(language_code, root, pattern)
        if isinstance(ready, DrillRoot) and any(
            (state := item.directions.get(Direction.RECOGNITION)) is not None and state.state in _UNDERSTOOD
            for _, item in srs_db.get_collocations_by_lemma_with_id(root)
        ):
            found[root] = ready
    return found


def understood_roots(language_code: str, pattern: AffixPattern, srs_db) -> list[str]:
    """The roots *pattern* can drill that the learner understands, in the language's own order."""
    return list(_understood(language_code, pattern, srs_db))


def _story_lines(store: ContentStore, curriculum_id: str) -> list[tuple[str, str]]:
    """Every ``(line, its English)`` the curriculum's stories tell, in lesson order.

    The latest lesson of each day only. What is read is a lesson's
    natural-speed section, which a grammar lesson does not have: a drill's
    whole lines came from these same stories and are not read back.
    """
    lines: list[tuple[str, str]] = []
    for entry in store.get_lesson_days(curriculum_id):
        _, lesson = store.get_latest_lesson_by_day(curriculum_id, entry["day"])
        english = (lesson.generation_metadata or {}).get(
            "sentence_translations"
        ) or extract_sentence_translations_from_translated(lesson)
        for section in lesson.sections:
            if section.section_type is SectionType.NATURAL_SPEED:
                lines += [
                    (phrase.text, english.get(phrase.text, ""))
                    for phrase in section.phrases
                    if phrase.language_code == lesson.language_code
                ]
    return lines


def plan_grammar_lesson(
    language_code: str, pattern: AffixPattern, *, srs_db, store: ContentStore, curriculum_id: str
) -> GrammarPlan:
    """Choose the roots and lines of a *pattern* lesson for this learner.

    Raises ``ValueError`` when fewer than ``MIN_ROOTS`` understood roots take
    the pattern: a drill on one root teaches a word, not an affix.
    """
    found = _understood(language_code, pattern, srs_db)
    understood = list(found)
    if len(understood) < MIN_ROOTS:
        raise ValueError(
            f"An affix drill needs at least {MIN_ROOTS} roots you understand, "
            f"and {pattern.key} has {len(understood)}: {', '.join(understood) or 'none'}"
        )

    story = _story_lines(store, curriculum_id)
    heard = {_fold(word) for text, _ in story for word in _WORD.findall(text)}
    met = {root: sum(_fold(form) in heard for form, _ in found[root].cells) for root in understood}

    must_model = [root for root in understood if not found[root].transparent]
    by_met = sorted((root for root in understood if found[root].transparent), key=lambda root: -met[root])
    room = max(0, MODELLED - len(must_model))
    roots, new_roots = (*must_model, *by_met[:room]), tuple(by_met[room:])

    forms = tuple(form for root in (*roots, *new_roots) for form, _ in found[root].cells)
    drilled = {_fold(form) for form in forms}
    usable = [
        (text, english)
        for text, english in dict.fromkeys(story)
        if english
        and not _SECOND_SENTENCE.search(text.strip())
        and any(_fold(word) in drilled for word in _WORD.findall(text))
    ]
    lines = tuple(sorted(usable, key=lambda line: len(line[0].split()))[:MAX_LINES])
    return GrammarPlan(pattern=pattern, roots=roots, new_roots=new_roots, lines=lines, forms=forms)


def build_grammar_lesson(language: Language, plan: GrammarPlan) -> Lesson:
    """The lesson for *plan*: one AFFIX_DRILL section, in the voice that reads the key phrases."""
    drill = build_affix_drill(
        language.code, plan.pattern, roots=list(plan.roots), new_roots=list(plan.new_roots), lines=list(plan.lines)
    )
    voices = language.tts_voice_map
    narrator = voices["narrator"]
    section = build_affix_drill_section(
        drill, narrator_voice=narrator, l2_voice=voices.get("key-phrases") or voices["female-1"]
    )
    return Lesson(
        title=pattern_title(plan.pattern),
        language_code=language.code,
        sections=[section],
        narrator_voice=narrator,
        generation_metadata={
            "affix_drill": {
                "pattern": plan.pattern.key,
                "roots": list(plan.roots),
                "new_roots": list(plan.new_roots),
            }
        },
        kind="grammar",
    )


def grammar_day(day: int, plan: GrammarPlan) -> CurriculumDay:
    """The curriculum day that holds *plan*'s lesson; its collocations are the forms drilled."""
    affixes = pattern_title(plan.pattern).removeprefix("Affix drill: ")
    return CurriculumDay(
        day=day,
        title=pattern_title(plan.pattern),
        focus=f"The {affixes} pair, on roots you already understand",
        collocations=list(plan.forms),
        learning_objective="Say each form of a root you understand when asked for it in English.",
        kind="grammar",
        pattern=plan.pattern.key,
    )
