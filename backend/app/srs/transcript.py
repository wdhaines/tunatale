"""Transcript extraction service for SRS word-level tracking."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from app.models.lesson import KeyPhraseInfo, Lesson, SectionType
from app.models.srs_item import Direction, DirectionState, SRSState
from app.srs.collocation_matcher import match_spans
from app.srs.database import SRSDatabase
from app.srs.function_words import is_a1_morphology_feature, ud_feats_to_tt_feature
from app.srs.lemmatizer import Lemmatizer, lemmatize_surfaces_in_context
from app.srs.mastery import compute_mastery_progress
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
    # Phase 5 enrichment fields
    card_type: str | None = None  # resolved item's card_type; None if unknown
    active_state: str = "unknown"  # active direction's state.value; "unknown" if no card
    active_direction: str | None = None  # "recognition" / "production"; None if unknown
    is_due: bool = False  # active direction due_at.date() <= today
    progress: float | None = None  # compute_mastery_progress over the component set
    inflectable: bool = False  # surface!=lemma + A1 feature + base prod REVIEW/KNOWN + no existing cloze
    inflection_feature: str | None = None  # the A1 feature string when inflectable


@dataclass
class DialogueLine:
    """A single speaker line in the dialogue."""

    role: str
    words: list[WordToken] = field(default_factory=list)
    sentence: str = ""  # full sentence text reconstructed from surfaces


@dataclass
class TranscriptData:
    """Full lesson transcript with per-word SRS state snapshot."""

    key_phrases: list[KeyPhraseInfo] = field(default_factory=list)
    dialogue_lines: list[DialogueLine] = field(default_factory=list)


def build_collocation_lemma_key(text: str, lemmatizer: Lemmatizer, language_code: str) -> str:
    """Space-joined lemma tuple for a collocation's text.

    Lemmatizes the tokens in the context of the collocation's own text so the key
    stays consistent with the sentence-context lemmas used for the dialogue
    (otherwise a POS-ambiguous word like ``dobro`` would key differently on the
    two sides and the span would never match). Lemmas are single tokens, so the
    join is round-trippable via ``str.split(" ")``.
    """
    return " ".join(lemmatize_surfaces_in_context(tokenize(text), text, lemmatizer, language_code))


def _build_collocation_index(
    db: SRSDatabase,
    collocations: list[tuple[int, str, str | None]],
    lemmatizer: Lemmatizer,
    language_code: str,
) -> dict[tuple[str, ...], int]:
    """Build lemma-tuple → DB id index for multi-word collocation matching.

    Uses each collocation's stored ``lemma_key`` (review finding #4) so the
    request path doesn't re-lemmatize on every call. Rows whose key is still NULL
    are lemmatized once and persisted (self-healing backfill), so a given
    collocation is lemmatized at most once ever rather than per request.
    """
    index: dict[tuple[str, ...], int] = {}
    for coll_id, text, lemma_key in collocations:
        if lemma_key is None:
            lemma_key = build_collocation_lemma_key(text, lemmatizer, language_code)
            db.set_lemma_key(coll_id, lemma_key)
        index[tuple(lemma_key.split(" ")) if lemma_key else ()] = coll_id
    return index


def resolve_active_direction(item: object) -> Direction:
    """Return the active direction for a resolved SRSItem.

    Cloze → PRODUCTION (only direction it has).
    Vocab → RECOGNITION while rec.state != REVIEW; else PRODUCTION.
    When both REVIEW, active = production.
    """
    from app.models.srs_item import SRSItem as _SRSItem

    if not isinstance(item, _SRSItem):
        return Direction.PRODUCTION
    ct = item.syntactic_unit.card_type
    if ct == "cloze":
        return Direction.PRODUCTION
    rec = item.directions.get(Direction.RECOGNITION)
    if rec is None or rec.state != SRSState.REVIEW:
        return Direction.RECOGNITION
    return Direction.PRODUCTION


def _is_due(ds: DirectionState, today: date) -> bool:
    """True when the direction state is actionable (not known/suspended/unknown) and due."""
    if ds.state in ("unknown", SRSState.KNOWN, SRSState.SUSPENDED):
        return False
    return ds.due_at.date() <= today


def extract_transcript(
    lesson: Lesson,
    db: SRSDatabase,
    lemmatizer: Lemmatizer,
    today: date | None = None,
) -> TranscriptData:
    """Extract transcript data from a lesson with current SRS states.

    Only processes the NATURAL_SPEED section, filtering to L2 phrases only.
    Enriches each WordToken with srs_item_id, translation, collocation span info,
    and Phase 5 enrichment fields (card_type, active_state, active_direction, is_due,
    progress, inflectable, inflection_feature).
    """
    if today is None:
        today = date.today()

    natural_speed = next(
        (s for s in lesson.sections if s.section_type == SectionType.NATURAL_SPEED),
        None,
    )

    gloss_map: dict[str, str] = (lesson.generation_metadata or {}).get("token_glosses", {})

    # Pre-load multi-word collocations for span detection
    raw_collocations = db.get_collocations_with_lemma_key(lesson.language_code, min_word_count=2)
    collocation_index = _build_collocation_index(db, raw_collocations, lemmatizer, lesson.language_code)

    dialogue_lines: list[DialogueLine] = []

    if natural_speed is not None:
        # Cache inflection clozes per lemma (one gather per unique lemma)
        inflection_cache: dict[str, list[tuple[int, object]]] = {}

        for phrase in natural_speed.phrases:
            if phrase.language_code != lesson.language_code:
                continue  # skip narrator/English lines

            surfaces = tokenize(phrase.text)
            lemmas = lemmatize_surfaces_in_context(surfaces, phrase.text, lemmatizer, lesson.language_code)

            # Run lemmatizer analyze_sentence once per phrase for inflectable detection
            phrase_analyses = lemmatizer.analyze_sentence(phrase.text, lesson.language_code)
            analysis_by_surface: dict[str, object] = {}
            for ta in phrase_analyses:
                analysis_by_surface[ta.surface.lower()] = ta

            # Resolve per-token SRS state and item id
            words: list[WordToken] = []
            for surface, lemma in zip(surfaces, lemmas, strict=True):
                # Resolution order: 1) exact-surface inflection cloze, 2) base, 3) unknown
                resolved_item: object = None
                resolved_item_id: int | None = None
                db_translation: str | None = None

                # Step 1: Gather inflection clozes for this lemma
                if lemma not in inflection_cache:
                    inflection_cache[lemma] = list(db.get_inflection_clozes_for_lemma(lemma))
                inflection_clozes = inflection_cache[lemma]

                # Step 1a: Try exact-surface inflection cloze
                inflection_match: tuple[int, object] | None = None
                for ic_id, ic_item in inflection_clozes:
                    if ic_item.syntactic_unit.text.casefold() == surface.casefold():
                        inflection_match = (ic_id, ic_item)
                        break

                if inflection_match is not None:
                    item_id, item = inflection_match
                    resolved_item = item
                    resolved_item_id = item_id
                    db_translation = item.syntactic_unit.translation or None

                    # Components for progress = just the production direction
                    components = [item.directions.get(Direction.PRODUCTION)]
                else:
                    # Step 2: Try base via get_collocation_by_lemma_with_id
                    result = db.get_collocation_by_lemma_with_id(lemma)
                    if result is None and surface.lower() != lemma:
                        result = db.get_collocation_by_lemma_with_id(surface.lower())
                    if result is not None:
                        item_id, item = result
                        resolved_item = item
                        resolved_item_id = item_id
                        db_translation = item.syntactic_unit.translation or None

                        # Components = base directions plus each inflection cloze's production
                        components = list(item.directions.values())
                        for _ic_id, ic_item in inflection_clozes:
                            components.append(ic_item.directions[Direction.PRODUCTION])
                    else:
                        # Step 3: Unknown
                        components = []

                srs_state = "unknown"
                active_dir: Direction | None = None
                active_direction_str: str | None = None
                active_state_val: str = "unknown"
                card_type: str | None = None
                is_due_flag: bool = False
                progress_val: float | None = None
                inflectable_flag: bool = False
                inflection_feature_val: str | None = None

                if resolved_item is not None:
                    from app.models.srs_item import SRSState as _SRSState

                    item = resolved_item
                    srs_state = item.state.value
                    card_type = item.syntactic_unit.card_type
                    active_dir = resolve_active_direction(item)
                    active_direction_str = active_dir.value
                    active_ds = item.directions[active_dir]
                    active_state_val = active_ds.state.value
                    is_due_flag = _is_due(active_ds, today)
                    valid_components = [c for c in components if c is not None]
                    progress_val = compute_mastery_progress(valid_components)

                    if surface.lower() != lemma.lower():
                        ta = analysis_by_surface.get(surface.lower())
                        if ta is not None:
                            feature_str = ud_feats_to_tt_feature(ta.upos, ta.case, ta.number, ta.person, ta.gender)
                        else:
                            feature_str = ""
                        if feature_str and is_a1_morphology_feature(feature_str):
                            base_prod = item.directions.get(Direction.PRODUCTION)
                            base_prod_state = base_prod.state if base_prod is not None else None
                            if base_prod_state in (_SRSState.REVIEW, _SRSState.KNOWN) and inflection_match is None:
                                inflectable_flag = True
                                inflection_feature_val = feature_str

                # DB translation wins; fall back to gloss map
                translation = db_translation if db_translation else gloss_map.get(lemma)

                words.append(
                    WordToken(
                        surface=surface,
                        lemma=lemma,
                        srs_state=srs_state,
                        srs_item_id=resolved_item_id,
                        translation=translation,
                        card_type=card_type,
                        active_state=active_state_val,
                        active_direction=active_direction_str,
                        is_due=is_due_flag,
                        progress=progress_val,
                        inflectable=inflectable_flag,
                        inflection_feature=inflection_feature_val,
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

            dialogue_lines.append(
                DialogueLine(role=phrase.role, words=words, sentence=" ".join(w.surface for w in words))
            )

    return TranscriptData(
        key_phrases=list(lesson.key_phrases),
        dialogue_lines=dialogue_lines,
    )
