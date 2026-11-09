"""A word the learner knows well is not built up syllable by syllable (the owner, 2026-10-14).

"Any word that is well recognized ... could also not be built up backwards.
That would save some length as well." Then: "that could be everywhere,
including in the key phrases." The threshold is the owner's choice: the listen
preview's own "well known" (``mastery.is_well_known``), read off the transcript
so it is the preview's answer word for word, inflections and all.

What goes is a well-known word's syllable pieces: every breakdown phrase that
names the word as its ``source_word`` and is not the word itself. The word, and
the phrase built backwards from it, stay. The same rule the affix drill uses
for a root it has already built (``affix_drill._Built``), with "the learner
already owns it" in place of "the drill already built it".

Runs at publish, the one seam where every lesson meets the learner's deck,
after the closing drill is appended and annotated. Key-phrase groups are read
off the section's own shape (``key_phrase_groups``: a head is a phrase followed
by its narrator translation), and a breakdown piece is never followed by one,
so dropping pieces moves no group boundary.

A word the story never says is not judged and keeps its buildup: the
transcript reads the story, and a word it never meets has no answer there.

A word made of a prefix the learner has (``pattern_gate.taught_prefixes``) and
a root they know well counts as well known too (tunatale-lat6: "treat various
affixes as well-known once they are taught", "similarly for roots that are
well-known"). And in the key phrases, a word that is no longer broken loses its
lone rung unless it is its phrase's last word, where the backward build
starts: the next rung says it (``buildup_rules``).
"""

from __future__ import annotations

from app.generation.drill_lines import fold
from app.generation.section_builder import key_phrase_groups
from app.languages import get_prefix_fn
from app.models.lesson import Lesson, Phrase, Section, SectionType
from app.models.srs_item import Direction
from app.srs.anki_mirror.rollover import anki_today
from app.srs.lemmatizer import Lemmatizer, get_lemmatizer
from app.srs.mastery import is_well_known
from app.srs.transcript import extract_transcript

_BUILT_UP = (SectionType.KEY_PHRASES, SectionType.AFFIX_DRILL)


def well_known_words(lesson: Lesson, srs_db, lemmatizer: Lemmatizer | None) -> set[str]:
    """The story's words, :func:`fold`-ed, that the transcript marks well known."""
    if lemmatizer is None:
        lemmatizer = get_lemmatizer(lesson.language_code)
    transcript = extract_transcript(lesson, srs_db, lemmatizer)
    return {fold(word.surface) for line in transcript.dialogue_lines for word in line.words if word.well_known}


def affix_known_words(lesson: Lesson, srs_db, taught_prefixes: frozenset[str]) -> set[str]:
    """The built-up words, :func:`fold`-ed, made of a taught prefix and a well-known root."""
    prefix_fn = get_prefix_fn(lesson.language_code)
    if prefix_fn is None or not taught_prefixes:
        return set()
    today = anki_today()
    words = {
        phrase.source_word
        for section in lesson.sections
        if section.section_type in _BUILT_UP
        for phrase in section.phrases
        if phrase.source_word and phrase.syllable_span
    }
    known: set[str] = set()
    for word in words:
        prefix = prefix_fn(word)
        if prefix not in taught_prefixes:
            continue
        root = fold(word)[len(prefix) :]
        item = srs_db.get_collocation_by_lemma(root) or srs_db.get_collocation(root)
        if item is not None and is_well_known(item.directions.get(Direction.RECOGNITION), today):
            known.add(fold(word))
    return known


def _drop_lone_rungs(section: Section, known: set[str], l2_code: str) -> list[Phrase]:
    """*section*'s key phrases without the lone rung of a known word that is not its phrase's last."""
    groups = key_phrase_groups(section, l2_code)
    kept: list[Phrase] = list(section.phrases[: groups[0].start]) if groups else list(section.phrases)
    for group in groups:
        past_last_word = False
        for index, phrase in enumerate(section.phrases[group.start : group.stop]):
            # The head, the narrator's English and the phrase that opens the build come first;
            # the first rung joining two words marks the end of the phrase's last word.
            if index >= 3 and " " in phrase.text.strip():
                past_last_word = True
            if (
                past_last_word
                and phrase.source_word
                and fold(phrase.source_word) in known
                and fold(phrase.text) == fold(phrase.source_word)
            ):
                continue
            kept.append(phrase)
    return kept


def trim_well_known_buildups(
    lesson: Lesson, srs_db, lemmatizer: Lemmatizer | None, *, taught_prefixes: frozenset[str] = frozenset()
) -> int:
    """Drop the syllable pieces of well-known words from *lesson*'s buildups, in place. Returns how many."""
    known = well_known_words(lesson, srs_db, lemmatizer) | affix_known_words(lesson, srs_db, taught_prefixes)
    dropped = 0
    for section in lesson.sections:
        if section.section_type not in _BUILT_UP:
            continue
        before = len(section.phrases)
        section.phrases = [
            phrase
            for phrase in section.phrases
            if not (
                phrase.source_word
                and phrase.syllable_span
                and fold(phrase.source_word) in known
                and fold(phrase.text) != fold(phrase.source_word)
            )
        ]
        if section.section_type is SectionType.KEY_PHRASES:
            section.phrases = _drop_lone_rungs(section, known, lesson.language_code)
        dropped += before - len(section.phrases)
    return dropped
