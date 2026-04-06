"""Transcript extraction service for SRS word-level tracking."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models.lesson import KeyPhraseInfo, Lesson, SectionType
from app.srs.database import SRSDatabase
from app.srs.lemmatizer import Lemmatizer
from app.srs.tokenizer import tokenize


@dataclass
class WordToken:
    """A single word in the transcript with its SRS state."""

    surface: str  # original word as it appears in text (punctuation stripped)
    lemma: str  # canonical base form (lowercased)
    srs_state: str  # "unknown"|"new"|"learning"|"review"|"relearning"


@dataclass
class DialogueLine:
    """A single speaker line in the dialogue."""

    role: str
    words: list[WordToken] = field(default_factory=list)


@dataclass
class TranscriptData:
    """Full lesson transcript with per-word SRS state snapshot."""

    key_phrases: list[KeyPhraseInfo] = field(default_factory=list)
    dialogue_lines: list[DialogueLine] = field(default_factory=list)


def extract_transcript(
    lesson: Lesson,
    db: SRSDatabase,
    lemmatizer: Lemmatizer,
) -> TranscriptData:
    """Extract transcript data from a lesson with current SRS states.

    Only processes the NATURAL_SPEED section, filtering to L2 phrases only.
    """
    natural_speed = next(
        (s for s in lesson.sections if s.section_type == SectionType.NATURAL_SPEED),
        None,
    )

    dialogue_lines: list[DialogueLine] = []

    if natural_speed is not None:
        for phrase in natural_speed.phrases:
            if phrase.language_code != lesson.language_code:
                continue  # skip narrator/English lines

            tokens = tokenize(phrase.text)
            words: list[WordToken] = []
            for surface in tokens:
                lemma = lemmatizer.lemmatize(surface, lesson.language_code)
                item = db.get_collocation_by_lemma(lemma)
                srs_state = item.state.value if item is not None else "unknown"
                words.append(WordToken(surface=surface, lemma=lemma, srs_state=srs_state))

            dialogue_lines.append(DialogueLine(role=phrase.role, words=words))

    return TranscriptData(
        key_phrases=list(lesson.key_phrases),
        dialogue_lines=dialogue_lines,
    )
