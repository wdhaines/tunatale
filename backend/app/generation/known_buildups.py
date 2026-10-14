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
"""

from __future__ import annotations

from app.generation.drill_lines import fold
from app.models.lesson import Lesson, SectionType
from app.srs.lemmatizer import Lemmatizer, get_lemmatizer
from app.srs.transcript import extract_transcript

_BUILT_UP = (SectionType.KEY_PHRASES, SectionType.AFFIX_DRILL)


def well_known_words(lesson: Lesson, srs_db, lemmatizer: Lemmatizer | None) -> set[str]:
    """The story's words, :func:`fold`-ed, that the transcript marks well known."""
    if lemmatizer is None:
        lemmatizer = get_lemmatizer(lesson.language_code)
    transcript = extract_transcript(lesson, srs_db, lemmatizer)
    return {fold(word.surface) for line in transcript.dialogue_lines for word in line.words if word.well_known}


def trim_well_known_buildups(lesson: Lesson, srs_db, lemmatizer: Lemmatizer | None) -> int:
    """Drop the syllable pieces of well-known words from *lesson*'s buildups, in place. Returns how many."""
    known = well_known_words(lesson, srs_db, lemmatizer)
    dropped = 0
    for section in lesson.sections:
        if section.section_type not in _BUILT_UP:
            continue
        kept = [
            phrase
            for phrase in section.phrases
            if not (
                phrase.source_word
                and phrase.syllable_span
                and fold(phrase.source_word) in known
                and fold(phrase.text) != fold(phrase.source_word)
            )
        ]
        dropped += len(section.phrases) - len(kept)
        section.phrases = kept
    return dropped
