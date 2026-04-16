"""Transcript extraction service for SRS word-level tracking."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models.lesson import KeyPhraseInfo, Lesson, SectionType
from app.srs.collocation_matcher import match_spans
from app.srs.database import SRSDatabase
from app.srs.lemmatizer import Lemmatizer
from app.srs.tokenizer import tokenize


@dataclass
class WordToken:
    """A single word in the transcript with its SRS state and enrichment fields."""

    surface: str  # original word as it appears in text (punctuation stripped)
    lemma: str  # canonical base form (lowercased)
    srs_state: str  # "unknown"|"new"|"learning"|"review"|"relearning"|"known"
    srs_item_id: int | None = None  # database id of the SRS card, if one exists
    translation: str | None = None  # L1 translation: DB value wins over gloss map
    collocation_span_id: int | None = None  # DB id of multi-word collocation this token belongs to
    collocation_start: bool = False  # True if this is the first token in its collocation span
    collocation_srs_state: str | None = None  # SRS state of the enclosing collocation
    collocation_lemma: str | None = None  # canonical text of the enclosing collocation
    collocation_translation: str | None = None  # L1 translation of the enclosing collocation


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


def _build_collocation_index(
    collocations: list[tuple[int, str]],
    lemmatizer: Lemmatizer,
    language_code: str,
) -> dict[tuple[str, ...], int]:
    """Build lemma-tuple → DB id index for multi-word collocation matching."""
    return {
        tuple(lemmatizer.lemmatize(t, language_code) for t in tokenize(text)): coll_id for coll_id, text in collocations
    }


def extract_transcript(
    lesson: Lesson,
    db: SRSDatabase,
    lemmatizer: Lemmatizer,
) -> TranscriptData:
    """Extract transcript data from a lesson with current SRS states.

    Only processes the NATURAL_SPEED section, filtering to L2 phrases only.
    Enriches each WordToken with srs_item_id, translation, and collocation span info.
    """
    natural_speed = next(
        (s for s in lesson.sections if s.section_type == SectionType.NATURAL_SPEED),
        None,
    )

    gloss_map: dict[str, str] = (lesson.generation_metadata or {}).get("token_glosses", {})

    # Pre-load multi-word collocations for span detection
    raw_collocations = db.get_collocations_for_language(lesson.language_code, min_word_count=2)
    collocation_index = _build_collocation_index(raw_collocations, lemmatizer, lesson.language_code)

    dialogue_lines: list[DialogueLine] = []

    if natural_speed is not None:
        for phrase in natural_speed.phrases:
            if phrase.language_code != lesson.language_code:
                continue  # skip narrator/English lines

            surfaces = tokenize(phrase.text)
            lemmas = [lemmatizer.lemmatize(s, lesson.language_code) for s in surfaces]

            # Resolve per-token SRS state and item id
            words: list[WordToken] = []
            for surface, lemma in zip(surfaces, lemmas, strict=True):
                result = db.get_collocation_by_lemma_with_id(lemma)
                if result is not None:
                    item_id, item = result
                    srs_state = item.state.value
                    db_translation = item.syntactic_unit.translation or None
                else:
                    item_id = None
                    srs_state = "unknown"
                    db_translation = None

                # DB translation wins; fall back to gloss map
                translation = db_translation if db_translation else gloss_map.get(lemma)

                words.append(
                    WordToken(
                        surface=surface,
                        lemma=lemma,
                        srs_state=srs_state,
                        srs_item_id=item_id,
                        translation=translation,
                    )
                )

            # Annotate collocation spans
            span_annotations = match_spans(lemmas, collocation_index)
            span_cache: dict[int, tuple[str, str, str | None]] = {}
            for word, (span_id, is_start) in zip(words, span_annotations, strict=True):
                word.collocation_span_id = span_id
                word.collocation_start = is_start
                if span_id is None:
                    continue
                cached = span_cache.get(span_id)
                if cached is None:
                    _, coll_item, _ = db.get_collocation_by_id(span_id)
                    cached = (
                        coll_item.state.value,
                        coll_item.syntactic_unit.text,
                        coll_item.syntactic_unit.translation or None,
                    )
                    span_cache[span_id] = cached
                word.collocation_srs_state, word.collocation_lemma, word.collocation_translation = cached

            dialogue_lines.append(DialogueLine(role=phrase.role, words=words))

    return TranscriptData(
        key_phrases=list(lesson.key_phrases),
        dialogue_lines=dialogue_lines,
    )
