"""A story lesson's planned affix pattern, and the closing drill built from it (tunatale-ve4p.7).

The owner, 2026-10-07: "Lessons should have an affix pattern." A thematic
lesson keeps its story and gains two things, neither of which calls a model:

**Before the story is written**, the prompt names a few roots and asks for each
in BOTH forms of the day's pattern (:func:`pattern_block`). Unasked, a story
uses a pattern on one or two roots in one form each, which is a handful of
words and not a pattern; the target is three roots in both cells
(``scripts/report_affix_exposure.py``). The roots come from the language's own
worded table, so every form asked for is one the language vouches for.

**After the story exists**, a short affix drill on the roots the story actually
used is appended as the lesson's last section (:func:`add_closing_drill`). It
records itself the way a grammar lesson does, in
``generation_metadata["affix_drill"]``, so Mark as Listened adds contrast cards
for a story lesson with no code of its own.

Which pattern a day gets is :func:`pattern_for_day`: its own if it names one,
otherwise the language's patterns in rotation over the curriculum's thematic
days. The rotation reads nothing but the curriculum, so the prompt that asked
for a pattern and the import that later builds the drill agree without either
storing the choice.
"""

from __future__ import annotations

from app.generation.affix_drill import MIN_ROOTS, DrillRoot, build_affix_drill, drill_root
from app.generation.grammar_lesson import (
    _SECOND_SENTENCE,
    _WORD,
    _fold,
    lesson_lines,
    pattern_title,
    understood_roots,
)
from app.generation.section_builder import build_affix_drill_section
from app.languages import get_a1_morphology
from app.models.curriculum import Curriculum, CurriculumDay
from app.models.language import Language
from app.models.lesson import Lesson, SectionType
from app.srs.a1_morphology import AffixPattern

ASKED = 4  # roots the story is asked to use in both forms; the target is three
DRILLED = 3  # roots in the closing drill: it ends a lesson, it is not one
CLOSING_LINES = 2


def named_pattern(language_code: str, day: CurriculumDay) -> AffixPattern | None:
    """The pattern a thematic *day* names for itself, when the language has it."""
    bundle = get_a1_morphology(language_code)
    if bundle is None or day.kind != "thematic":
        return None
    return next((p for p in bundle.patterns if p.key == day.pattern), None)


def pattern_for_day(curriculum: Curriculum, day: CurriculumDay) -> AffixPattern | None:
    """The affix pattern *day*'s story is planned around, or ``None``.

    ``None`` for a language that registers no patterns and for a grammar day,
    which is a drill on a pattern of its own and is planned elsewhere. A day
    that names a pattern the language has keeps it. Any other thematic day
    takes the patterns in turn, by how many thematic days come before it.
    """
    bundle = get_a1_morphology(curriculum.language_code)
    if bundle is None or not bundle.patterns or day.kind != "thematic":
        return None
    named = named_pattern(curriculum.language_code, day)
    if named is not None:
        return named
    turn = sum(1 for other in curriculum.days if other.kind == "thematic" and other.day < day.day)
    return bundle.patterns[turn % len(bundle.patterns)]


def _worded(language_code: str, pattern: AffixPattern) -> list[DrillRoot]:
    """Every root the language can drill in *pattern*, in its table's order."""
    bundle = get_a1_morphology(language_code)
    ready = (drill_root(language_code, root, pattern) for root in bundle.pattern_roots(pattern))
    return [root for root in ready if isinstance(root, DrillRoot)]


def pattern_block(language_code: str, pattern: AffixPattern, *, srs_db=None) -> str:
    """The story-prompt block that asks for *pattern* on up to :data:`ASKED` roots.

    Roots the learner understands come first: only those can get a contrast
    card. The rest follow in the table's order. Ends with a blank line, so a
    template that has no block to place renders exactly as it did before.
    """
    understood = set(understood_roots(language_code, pattern, srs_db)) if srs_db is not None else set()
    asked = sorted(_worded(language_code, pattern), key=lambda root: root.root not in understood)[:ASKED]
    rows = [f"- {root.root}: " + ", ".join(f"{form} ({english})" for form, english in root.cells) for root in asked]
    title = pattern_title(pattern).removeprefix("Affix drill: ")
    return (
        f"**Verb Pattern for This Lesson: {title}**\n"
        "Use each of these verbs in BOTH forms somewhere in the dialogue, each in a line where it is natural:\n"
        + "\n".join(rows)
        + "\n\n"
    )


def add_closing_drill(lesson: Lesson, language: Language, pattern: AffixPattern) -> None:
    """Append to *lesson* a drill of *pattern* on the roots its story used. In place.

    A root the story said in both forms leads; the rest follow in the order
    the story first says them, up to :data:`DRILLED`. Fewer than ``MIN_ROOTS``
    is a word and not a pattern, and the lesson is left as it was. So is a
    lesson that already has a drill, and a grammar lesson, which is one.
    """
    if lesson.kind != "thematic" or any(s.section_type is SectionType.AFFIX_DRILL for s in lesson.sections):
        return
    story = list(dict.fromkeys(lesson_lines(lesson)))
    first_said: dict[str, int] = {}
    for word in (_fold(word) for text, _ in story for word in _WORD.findall(text)):
        first_said.setdefault(word, len(first_said))

    used: list[tuple[bool, int, DrillRoot]] = []
    for root in _worded(language.code, pattern):
        said = [first_said[_fold(form)] for form, _ in root.cells if _fold(form) in first_said]
        if said:
            used.append((len(said) < len(root.cells), min(said), root))
    drilled = [root for *_, root in sorted(used, key=lambda entry: entry[:2])[:DRILLED]]
    if len(drilled) < MIN_ROOTS:
        return

    forms = {_fold(form) for root in drilled for form, _ in root.cells}
    whole = [
        line
        for line in story
        if line[1]
        and not _SECOND_SENTENCE.search(line[0].strip())
        and any(_fold(word) in forms for word in _WORD.findall(line[0]))
    ]
    roots = [root.root for root in drilled]
    drill = build_affix_drill(
        language.code,
        pattern,
        roots=roots,
        lines=sorted(whole, key=lambda line: len(line[0].split()))[:CLOSING_LINES],
    )
    voices = language.tts_voice_map
    lesson.sections.append(
        build_affix_drill_section(
            drill, narrator_voice=voices["narrator"], l2_voice=voices.get("key-phrases") or voices["female-1"]
        )
    )
    lesson.generation_metadata["affix_drill"] = {"pattern": pattern.key, "roots": roots, "new_roots": []}


def add_day_drill(lesson: Lesson, language: Language, curriculum: Curriculum | None, day: int) -> None:
    """Append to *lesson* the closing drill of the pattern its curriculum *day* is planned around. In place.

    The one call every writer of a curriculum day's lesson makes: the publish
    seam, and the script that rebuilds a stored lesson from its story. Nothing
    happens with no curriculum, for a day it does not hold, or for a day with
    no pattern.
    """
    planned = next((d for d in curriculum.days if d.day == day), None) if curriculum is not None else None
    pattern = pattern_for_day(curriculum, planned) if planned is not None else None
    if pattern is not None:
        add_closing_drill(lesson, language, pattern)
