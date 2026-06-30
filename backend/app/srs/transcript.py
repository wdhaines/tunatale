"""Transcript extraction service for SRS word-level tracking."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from app.models.lesson import KeyPhraseInfo, Lesson, SectionType
from app.models.srs_item import Direction, DirectionState, SRSState
from app.srs.collocation_matcher import match_spans
from app.srs.database import SRSDatabase
from app.srs.function_words import is_a1_morphology_feature, is_clozes_only_verb, ud_feats_to_tt_feature
from app.srs.lemmatizer import Lemmatizer, analyze_sentence_cached, lemmatize_surfaces_in_context, model_version_for
from app.srs.mastery import compute_mastery_progress
from app.srs.tokenizer import tokenize


@dataclass
class WordToken:
    """A single word in the transcript with its SRS state and enrichment fields."""

    surface: str  # original word as it appears in text (punctuation stripped)
    lemma: str  # canonical base form (lowercased)
    srs_state: str  # "unknown"|"new"|"learning"|"review"|"relearning"|"known"
    prefix_punct: str = ""  # non-word characters before the surface in the raw token
    suffix_punct: str = ""  # non-word characters after the surface in the raw token
    srs_item_id: int | None = None  # database id of the SRS card, if one exists
    translation: str | None = None  # L1 translation: DB value wins over gloss map
    collocation_span_id: int | None = None  # DB id of multi-word collocation this token belongs to
    collocation_start: bool = False  # True if this is the first token in its collocation span
    collocation_srs_state: str | None = None  # SRS state of the enclosing collocation
    collocation_lemma: str | None = None  # canonical text of the enclosing collocation
    collocation_translation: str | None = None  # L1 translation of the enclosing collocation
    collocation_progress: float | None = None  # mastery of the enclosing collocation (red→green ramp)
    collocation_is_due: bool = False  # enclosing collocation's active direction is due (same rule as is_due)
    # Phase 5 enrichment fields
    card_type: str | None = None  # resolved item's card_type; None if unknown
    active_state: str = "unknown"  # active direction's state.value; "unknown" if no card
    active_direction: str | None = None  # "recognition" / "production"; None if unknown
    is_due: bool = False  # active direction due_at.date() <= today
    progress: float | None = None  # compute_mastery_progress over the component set
    inflectable: bool = False  # surface!=lemma + A1 feature + base prod REVIEW/KNOWN + no existing cloze
    inflection_feature: str | None = None  # the A1 feature string when inflectable
    known_marked: bool = False  # resolved item has a reversible "known" snapshot (db.is_known_marked)


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


def _extract_punct_pairs(raw_tokens: list[str], surfaces: list[str]) -> list[tuple[str, str]]:
    """Extract prefix/suffix punctuation around each surface in its raw token.

    Each raw token contains the surface as a contiguous substring (case-insensitive).
    Returns list of (prefix_punct, suffix_punct) tuples.
    """
    pairs: list[tuple[str, str]] = []
    for raw, surf in zip(raw_tokens, surfaces, strict=True):
        idx = raw.lower().find(surf.lower())
        if idx == -1:
            pairs.append(("", ""))
        else:
            pairs.append((raw[:idx], raw[idx + len(surf) :]))
    return pairs


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
    prod = item.directions.get(Direction.PRODUCTION)
    # Recognition is active until it graduates (REVIEW), then production takes over
    # — BUT only if production exists. Single-direction cards (the imported
    # Norwegian deck is recognition-only) have nothing to advance to, so they stay
    # on the direction they actually have. Returning an absent direction makes the
    # caller's item.directions[active_dir] KeyError (the lesson-transcript 500).
    if rec is not None and rec.state == SRSState.REVIEW and prod is not None:
        return Direction.PRODUCTION
    if rec is not None:
        return Direction.RECOGNITION
    return Direction.PRODUCTION


def _is_due(ds: DirectionState, today: date) -> bool:
    """True when the direction state is actionable (not new/known/suspended/buried) and due."""
    # Match the review queue's non-reviewable set (database._NON_REVIEWABLE_STATES):
    # NEW is gated by the daily cap; SUSPENDED/KNOWN are off the ramp; BURIED is
    # sibling-deferred for the day. A buried card has due_at.date() == today but is
    # NOT due — don't bold it.
    if ds.state in (SRSState.NEW, SRSState.KNOWN, SRSState.SUSPENDED, SRSState.BURIED):
        return False
    return ds.due_at.date() <= today


def _inflection_feature_for(surface: str, analysis_by_surface: dict[str, object]) -> str:
    """Compute the A1 morphology feature string for *surface*, or ``""`` if none.

    Looks up the surface in the per-phrase analysis map, maps UD features via
    ``ud_feats_to_tt_feature``, and returns the feature string if valid.
    Returns ``""`` when no analysis is available or the feature is not mappable.
    """
    ta = analysis_by_surface.get(surface.lower())
    if ta is not None:
        feature = ud_feats_to_tt_feature(ta.upos, ta.case, ta.number, ta.person, ta.gender)
        return feature if feature is not None else ""
    return ""


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

    # note: token_glosses is a plain dict — if the same key appears in two different
    # sources the last-write-wins. Collocation matching uses the lemmatizer which may
    # disambiguate homographs via sentence context.
    gloss_map: dict[str, str] = (lesson.generation_metadata or {}).get("token_glosses", {})

    # Pre-load multi-word collocations for span detection
    raw_collocations = db.get_collocations_with_lemma_key(lesson.language_code, min_word_count=2)
    collocation_index = _build_collocation_index(db, raw_collocations, lemmatizer, lesson.language_code)
    # Card-less ignore list
    ignored_lemmas = db.get_ignored_lemmas(lesson.language_code)
    # Persistent cache key — empty for cheap lemmatizers (skips DB round-trip)
    model_version = model_version_for(lemmatizer)

    dialogue_lines: list[DialogueLine] = []

    if natural_speed is not None:
        # Cache inflection clozes per lemma (one gather per unique lemma)
        inflection_cache: dict[str, list[tuple[int, object]]] = {}
        # Cache base-collocation lookups per lemma (finding #6)
        base_cache: dict[str, tuple | None] = {}

        for phrase in natural_speed.phrases:
            if phrase.language_code != lesson.language_code:
                continue  # skip narrator/English lines

            surfaces = tokenize(phrase.text)
            lemmas = lemmatize_surfaces_in_context(
                surfaces, phrase.text, lemmatizer, lesson.language_code, db, model_version
            )

            # Extract punctuation from raw tokens for display
            raw_tokens = phrase.text.split()
            punct_pairs = _extract_punct_pairs(raw_tokens, surfaces)

            # Run lemmatizer analyze_sentence once per phrase for inflectable detection
            phrase_analyses = analyze_sentence_cached(db, lemmatizer, phrase.text, lesson.language_code, model_version)
            analysis_by_surface: dict[str, object] = {}
            for ta in phrase_analyses:
                analysis_by_surface[ta.surface.lower()] = ta

            # Resolve per-token SRS state and item id
            words: list[WordToken] = []
            for i, (surface, lemma) in enumerate(zip(surfaces, lemmas, strict=True)):
                prefix_punct, suffix_punct = punct_pairs[i]
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
                    # Step 2: Try base via get_collocation_by_lemma_with_id (cached).
                    # Clozes-only verbs (e.g. biti) have no base card — skip.
                    if is_clozes_only_verb(lemma, lesson.language_code):
                        result = None
                    elif lemma in base_cache:
                        result = base_cache[lemma]
                    else:
                        result = db.get_collocation_by_lemma_with_id(lemma)
                        if result is None and surface.lower() != lemma:
                            result = db.get_collocation_by_lemma_with_id(surface.lower())
                        base_cache[lemma] = result
                        if result is not None and surface.lower() != lemma:
                            base_cache[surface.lower()] = result
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

                # Step 3b: Check card-less ignore list (inside the Step-3 unknown branch only)
                if resolved_item is None and lemma.lower() in ignored_lemmas:
                    srs_state = "ignored"
                    active_state_val = "ignored"
                    progress_val = None
                    inflectable_flag = False

                if resolved_item is not None:
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
                        feature_str = _inflection_feature_for(surface, analysis_by_surface)
                        if feature_str and is_a1_morphology_feature(feature_str):
                            base_prod = item.directions.get(Direction.PRODUCTION)
                            base_prod_state = base_prod.state if base_prod is not None else None
                            if base_prod_state in (SRSState.REVIEW, SRSState.KNOWN) and inflection_match is None:
                                inflectable_flag = True
                                inflection_feature_val = feature_str

                # For clozes-only verbs with no resolvable card, still check
                # inflectable — they are ungated (no base required).
                if (
                    resolved_item is None
                    and is_clozes_only_verb(lemma, lesson.language_code)
                    and surface.lower() != lemma.lower()
                ):
                    feature_str = _inflection_feature_for(surface, analysis_by_surface)
                    if feature_str and is_a1_morphology_feature(feature_str) and inflection_match is None:
                        inflectable_flag = True
                        inflection_feature_val = feature_str

                # DB translation wins; fall back to gloss map — prefer surface-specific
                # (e.g. "boste" → "you will") over lemma-generic (e.g. "biti" → "am").
                translation = (
                    db_translation if db_translation else (gloss_map.get(surface.lower()) or gloss_map.get(lemma))
                )

                known_marked_flag = resolved_item_id is not None and db.is_known_marked(resolved_item_id)

                words.append(
                    WordToken(
                        surface=surface,
                        prefix_punct=prefix_punct,
                        suffix_punct=suffix_punct,
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
                        known_marked=known_marked_flag,
                    )
                )

            # Annotate collocation spans
            span_annotations = match_spans(lemmas, collocation_index)
            span_cache: dict[int, tuple[str, str, str | None, float | None, bool]] = {}
            for word, (span_id, is_start) in zip(words, span_annotations, strict=True):
                word.collocation_span_id = span_id
                word.collocation_start = is_start
                if span_id is None:
                    continue
                cached = span_cache.get(span_id)
                if cached is None:
                    _, coll_item, _ = db.get_collocation_by_id(span_id)
                    coll_active_ds = coll_item.directions.get(resolve_active_direction(coll_item))
                    cached = (
                        coll_item.state.value,
                        coll_item.syntactic_unit.text,
                        coll_item.syntactic_unit.translation or None,
                        compute_mastery_progress(coll_item.directions.values()),
                        coll_active_ds is not None and _is_due(coll_active_ds, today),
                    )
                    span_cache[span_id] = cached
                (
                    word.collocation_srs_state,
                    word.collocation_lemma,
                    word.collocation_translation,
                    word.collocation_progress,
                    word.collocation_is_due,
                ) = cached

            # Reconstruct with each token's surrounding punctuation, not the bare
            # surface join — the sentence is used as a card's source_sentence, and
            # dropping punctuation produces clozes/examples like "Koliko časa imaš"
            # missing the "?" and breaks exact sentence-translation lookups.
            dialogue_lines.append(
                DialogueLine(
                    role=phrase.role,
                    words=words,
                    sentence=" ".join(f"{w.prefix_punct}{w.surface}{w.suffix_punct}" for w in words),
                )
            )

    return TranscriptData(
        key_phrases=list(lesson.key_phrases),
        dialogue_lines=dialogue_lines,
    )
