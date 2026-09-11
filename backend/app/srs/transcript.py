"""Transcript extraction service for SRS word-level tracking."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from app.cards.cloze_source import parse_inflection_forms
from app.cards.field_map import inflection_labels
from app.languages import card_surface_variants, get_variant_separator
from app.models.lesson import KeyPhraseInfo, Lesson, SectionType
from app.models.srs_item import Direction, DirectionState, SRSItem, SRSState
from app.models.syntactic_unit import deserialize_extras
from app.srs.anki_mirror.rollover import anki_today
from app.srs.collocation_matcher import match_spans
from app.srs.database import SRSDatabase
from app.srs.function_words import is_a1_morphology_feature, is_clozes_only_verb, ud_feats_to_tt_feature
from app.srs.lemmatizer import Lemmatizer, analyze_sentence_cached, lemmatize_surfaces_in_context, model_version_for
from app.srs.mastery import band_stability, compute_mastery_progress, direction_band, is_well_known, side_progress
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
    # Read-ahead: recognition direction exists and is in a reviewable state
    # (LEARNING/REVIEW/RELEARNING), regardless of due date. Reading the word is a
    # valid recognition review even when the SRS wouldn't have surfaced it yet.
    recognition_reviewable: bool = False
    # Recognition-side state and dueness for mastery-line bucketing.
    # Independent of the active direction: a word whose recognition graduated to
    # REVIEW (active=production, active_state=new) shows recognition_state='review'.
    # None when the word has no recognition direction (untracked, production-only cloze).
    recognition_state: str | None = None
    recognition_is_due: bool = False
    # Recognition scheduled past the listen horizon — the same cutoff the listen
    # preview uses to stop asking about a word. Rendered as "known" by the
    # dialogue and counted in the known bucket of the mastery line, which is
    # where a card that came back from a sync as REVIEW-due-2126 belongs.
    well_known: bool = False
    # Twin rails (bd tunatale-yh47): per-direction mastery bands for the reader.
    # understand_* read the recognition direction, produce_* the production
    # direction of the word's OWN card (the exact-surface cloze when one is
    # resolved, else the base). All None for untracked/ignored/unknown words —
    # the frontend draws no rails for them.
    understand_band: str | None = None
    produce_band: str | None = None
    # The direction's stability, only when its band is a real strength band
    # (days/week/month/solid) AND its state is not KNOWN. KNOWN has no
    # meaningful stability; a NEW card's default 1.0 is not a measurement.
    understand_stability: float | None = None
    produce_stability: float | None = None
    # Each side's mastery in [0, 1] (mastery.py::side_progress) — the lesson
    # roll-up's per-side percent. None for untracked words and suspended sides.
    understand_progress: float | None = None
    produce_progress: float | None = None
    # Bands of the enclosing multi-word collocation span's OWN card, filled on
    # every span word; None off-span.
    collocation_understand_band: str | None = None
    collocation_produce_band: str | None = None


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


def _extract_punct_pairs(text: str, surfaces: list[str]) -> list[tuple[str, str]]:
    """Extract prefix/suffix punctuation around each surface, walking the raw text.

    Surfaces come from ``tokenize()``, which drops standalone punctuation (an
    en-dash "–" as its own whitespace token) and splits on boundaries that
    ``str.split()`` does not — so ``text.split()`` and ``surfaces`` are NOT
    positionally 1:1 in general (an LLM line like ``"Koliko stane? – Dve kavi."``
    splits to 5 tokens but tokenizes to 4). Locating each surface in ``text`` in
    order keeps the returned list aligned exactly 1:1 with ``surfaces``: for each
    surface, the punctuation is the non-space run before/after it within its
    whitespace-delimited token. A cursor advances past each match so a repeated
    surface resolves to successive occurrences. Returns one (prefix, suffix) per
    surface (``("", "")`` for a surface not found in ``text``).
    """
    pairs: list[tuple[str, str]] = []
    lower = text.lower()
    pos = 0
    for surf in surfaces:
        idx = lower.find(surf.lower(), pos)
        if idx == -1:
            pairs.append(("", ""))
            continue
        end = idx + len(surf)
        tok_start = idx
        while tok_start > 0 and not text[tok_start - 1].isspace():
            tok_start -= 1
        tok_end = end
        while tok_end < len(text) and not text[tok_end].isspace():
            tok_end += 1
        pairs.append((text[tok_start:idx], text[end:tok_end]))
        pos = end
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


#: Cache miss marker: `None` is a legitimate cached answer ("this word has no
#: covering cloze"), so absence needs its own sentinel or every uncovered word
#: re-queries on each token.
_UNSET = object()

_NON_REVIEWABLE_STATES = (SRSState.NEW, SRSState.KNOWN, SRSState.SUSPENDED, SRSState.BURIED)
# Read-ahead is more permissive than the due queue: reading a NEW word is a valid
# early introduction (the user recognizes it before the SRS surfaces it), so NEW is
# allowed here. KNOWN/SUSPENDED are off the ramp entirely; BURIED is deferred.
_READ_AHEAD_TERMINAL_STATES = (SRSState.KNOWN, SRSState.SUSPENDED, SRSState.BURIED)


def _is_reviewable(ds: DirectionState) -> bool:
    """True when the direction is on the review ramp (LEARNING/REVIEW/RELEARNING).

    Matches the review queue's non-reviewable set (database._NON_REVIEWABLE_STATES):
    NEW is gated by the daily cap; SUSPENDED/KNOWN are off the ramp; BURIED is
    sibling-deferred for the day. This is the due-independent half of _is_due.
    """
    return ds.state not in _NON_REVIEWABLE_STATES


def _is_read_reviewable(ds: DirectionState) -> bool:
    """True when the direction can be reviewed by reading — NEW included.

    Broader than _is_reviewable: reading a not-yet-introduced (NEW) word counts as
    an early recognition review, pulling it into learning ahead of the SRS schedule.
    """
    return ds.state not in _READ_AHEAD_TERMINAL_STATES


def _is_due(ds: DirectionState, today: date) -> bool:
    """True when the direction state is actionable (not new/known/suspended/buried) and due."""
    # A buried card has due_at.date() == today but is NOT due — don't bold it.
    if not _is_reviewable(ds):
        return False
    return ds.due_at.date() <= today


def _inflection_feature_for(
    surface: str,
    analysis_by_surface: dict[str, object],
    language_code: str,
) -> str:
    """Compute the A1 morphology feature string for *surface*, or ``""`` if none.

    Looks up the surface in the per-phrase analysis map, maps UD features via
    ``ud_feats_to_tt_feature`` (per *language_code*), and returns the feature
    string if valid. Returns ``""`` when no analysis is available or the feature
    is not mappable.
    """
    ta = analysis_by_surface.get(surface.lower())
    if ta is not None:
        feature = ud_feats_to_tt_feature(ta, language_code)
        return feature if feature is not None else ""
    return ""


def _build_variant_index(db: SRSDatabase, language_code: str) -> dict[str, tuple[int, SRSItem]]:
    """Map each accepted spelling of a variant card to its (id, hydrated item).

    A card front listing comma-separated spellings (Norwegian ``mot, imot``) is one
    lexical item that the single-word lemma lookup can't match (its ``lemma`` column
    is unset). This index lets the reader resolve *either* spelling to the one card.
    Empty for languages with no ``variant_separator`` (every other language today).

    ``get_variant_candidates_with_items`` scans and hydrates in one query, so
    there is no scan→refetch window (and no "row vanished" branch to cover).
    """
    sep = get_variant_separator(language_code)
    if not sep:
        return {}
    index: dict[str, tuple[int, SRSItem]] = {}
    for cid, text, item in db.get_variant_candidates_with_items(language_code, sep):
        # A production cloze shares its base card's front ("mellom, imellom").
        # The cloze row is the base's mastery component, not a second lexical
        # item — resolving the front to the cloze (higher id, last write wins)
        # hides the recognition card that is actually reviewable. The base row
        # is the lexical item; skip its cloze twins. (tunatale-xmnv)
        if item.syntactic_unit.card_type == "cloze":
            continue
        variants = card_surface_variants(language_code, text)
        if len(variants) <= 1:
            continue  # contained the separator but isn't a variant list (real phrase)
        for variant in variants:
            index[variant.casefold()] = (cid, item)
    return index


def _build_inflection_index(db: SRSDatabase, language_code: str) -> dict[str, int]:
    """Map each inflected form the deck itself lists to the card that lists it.

    The deck's ``Inflections`` table is deck-authored ground truth — ``fersk``'s
    table spells out ``fersk / ferskt / ferske`` — so it answers "is this surface
    already carded?" for words no lemmatizer reduces correctly. Stanza returns
    ``ferskt`` for the neuter ``ferskt`` while reducing the neuter ``helt`` to
    ``hel`` in the same sentence, so this is a data fallback for an engine defect,
    not a substitute for lemmatization. It is consulted LAST, after the lemma and
    surface keys, so a form that is some other card's own headword still resolves
    to that card (16 forms in the real Norwegian deck are both).

    **Forms claimed by more than one card are dropped, not arbitrated.** That
    covers two different shapes with one rule: genuine homographs whose notes
    list the same forms (the noun and verb ``løfte``), and the grammar labels
    that leak out of noun/determinative ``<tbody>`` cells (``hankjønn``,
    ``intetkjønn``) — 199 such forms in the real deck. Picking a winner by row
    order would grade a card the learner never met, which is the expensive error
    here; declining just leaves the word untracked, exactly as today.

    Empty for a deck that declares no inflection table (every language but
    Norwegian today), which costs one scan returning no rows.
    """
    claims: dict[str, set[int]] = {}
    for label in inflection_labels():
        for cid, extras_raw in db.get_inflection_candidates(language_code, label):
            html = next((e.html for e in deserialize_extras(extras_raw) if e.label == label), "")
            for form in parse_inflection_forms(html):
                claims.setdefault(form.casefold(), set()).add(cid)
    return {form: next(iter(ids)) for form, ids in claims.items() if len(ids) == 1}


def resolve_via_inflection_index(
    db: SRSDatabase,
    index: dict[str, int],
    *keys: str,
) -> tuple[int, SRSItem] | None:
    """First of *keys* the deck lists as an inflected form, hydrated.

    Shared by the reader and ``/listen`` so both resolve identically — a word the
    transcript shows as tracked must not be one ``/listen`` mints a card for (the
    6a5c718 preview↔commit class).

    A key absent from *index* is the ordinary outcome (untracked, or ambiguous
    and therefore excluded at build time). An entry pointing at a row that is no
    longer there yields ``None`` too: the index is a snapshot, and a stale entry
    must leave the word untracked rather than raise.
    """
    for key in keys:
        cid = index.get(key.casefold())
        if cid is None:
            continue
        found = db.get_collocation_by_id(cid)
        if found is not None:
            return (found[0], found[1])
    return None


def _resolve_base_card(
    db: SRSDatabase,
    surface: str,
    lemma: str,
    base_cache: dict[str, tuple | None],
    surface_base_cache: dict[str, tuple | None],
    variant_index: dict[str, tuple[int, SRSItem]],
    inflection_index: dict[str, int],
    language_code: str,
) -> tuple[int, SRSItem] | None:
    """Step 2 of the per-token resolution order: the base card for a lemma.

    Order: lemma lookup (cached), surface fallback (its own cache), the
    spelling-variant card, then the deck's own Inflections table. Extracted so
    the exact-surface-cloze branch can read the BASE card's recognition band
    with the identical lookup the base branch uses — ``understand_band`` must
    never resolve by a second path. (bd tunatale-yh47)
    """
    # Clozes-only verbs (e.g. biti) have no base card by LEMMA — but steps 2b
    # and 2c below still run for them, exactly as they did before this helper
    # was extracted, so do not return early here.
    result: tuple | None
    if is_clozes_only_verb(lemma, language_code):
        result = None
    elif lemma in base_cache:
        result = base_cache[lemma]
    else:
        result = db.get_collocation_by_lemma_with_id(lemma)
        if result is None and surface.lower() != lemma:
            surface_key = surface.lower()
            if surface_key in surface_base_cache:
                result = surface_base_cache[surface_key]
            else:
                result = db.get_collocation_by_lemma_with_id(surface_key)
                surface_base_cache[surface_key] = result
        base_cache[lemma] = result
    if result is None:
        result = variant_index.get(surface.casefold())
    if result is None:
        result = resolve_via_inflection_index(db, inflection_index, surface, lemma)
    return result


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
        # Anki-day rollover, not local midnight — feeds _is_due's `due_at.date()
        # <= today` comparison. date.today() would bold a card as due up to a
        # day early in the [midnight, 4 AM) local window (the documented
        # is_due bolding divergence).
        today = anki_today()

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
    # Spelling-variant cards ('mot, imot') keyed by each accepted surface form
    variant_index = _build_variant_index(db, lesson.language_code)
    # Inflected surfaces the deck itself lists ('ferskt' on the 'fersk' card)
    inflection_index = _build_inflection_index(db, lesson.language_code)
    # Persistent cache key — empty for cheap lemmatizers (skips DB round-trip)
    model_version = model_version_for(lemmatizer)

    dialogue_lines: list[DialogueLine] = []

    if natural_speed is not None:
        # Cache inflection clozes per lemma (one gather per unique lemma)
        inflection_cache: dict[str, list[tuple[int, object]]] = {}
        # Cache base-collocation lookups per lemma (finding #6)
        base_cache: dict[str, tuple | None] = {}
        # Surface-fallback lookups (lemma missed, surface hit) in their OWN
        # cache: base_cache is read by lemma, so a surface key must never be
        # able to satisfy a lemma read. Sharing one dict let a verb surface
        # ('gaar', lemma 'gaa') hand its card to a later token whose lemma is
        # genuinely 'gaar' — the same sentence rendered a different card by
        # position alone. (tunatale-klh)
        surface_base_cache: dict[str, tuple | None] = {}
        # Cache "does this word have a base cloze carrying its production?" per
        # collocation id. Keyed by id rather than lemma because that is what the
        # link records — a lemma cannot tell two homographs apart. `None` is a
        # real answer here (no covering cloze), hence the _UNSET sentinel.
        base_cloze_cache: dict[int, tuple | None] = {}

        for phrase in natural_speed.phrases:
            if phrase.language_code != lesson.language_code:
                continue  # skip narrator/English lines

            surfaces = tokenize(phrase.text)
            lemmas = lemmatize_surfaces_in_context(
                surfaces, phrase.text, lemmatizer, lesson.language_code, db, model_version
            )

            # Extract punctuation around each surface for display (aligned to
            # surfaces, robust to standalone-punctuation tokens tokenize() drops).
            punct_pairs = _extract_punct_pairs(phrase.text, surfaces)

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
                    # Step 2: resolve the base card — lemma lookup (cached),
                    # surface fallback (its own cache), the spelling-variant
                    # card ('mot, imot'), then the deck's own Inflections table
                    # ('ferskt' under 'fersk', consulted LAST so a form that is
                    # another card's headword still resolves to that card).
                    # Clozes-only verbs (e.g. biti) have no base card — None.
                    result = _resolve_base_card(
                        db,
                        surface,
                        lemma,
                        base_cache,
                        surface_base_cache,
                        variant_index,
                        inflection_index,
                        lesson.language_code,
                    )
                    if result is not None:
                        item_id, item = result
                        resolved_item = item
                        resolved_item_id = item_id
                        db_translation = item.syntactic_unit.translation or None

                        # Components = base directions plus each inflection cloze's production
                        components = list(item.directions.values())
                        for _ic_id, ic_item in inflection_clozes:
                            components.append(ic_item.directions[Direction.PRODUCTION])
                        # ...plus the production of a BASE cloze covering this word.
                        # A word that cannot be pictured gets its production card as a
                        # cloze, which is a separate Anki note and so a separate
                        # collocation. Without this the word is measured on its vocab
                        # row alone, which has no production direction — so mastery
                        # imputes the absent-production 0.0 and the word reads
                        # half-mastered forever, with the card that completes it
                        # sitting unread in the next row.
                        covering = base_cloze_cache.get(item_id, _UNSET)
                        if covering is _UNSET:
                            covering = db.get_covering_cloze(item_id)
                            base_cloze_cache[item_id] = covering
                        if covering is not None:
                            # Appended unconditionally: `valid_components` below
                            # already drops None, so guarding here would only add
                            # a branch nothing can reach.
                            components.append(covering[1].directions.get(Direction.PRODUCTION))
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
                recognition_reviewable_flag: bool = False
                recognition_state_val: str | None = None
                recognition_is_due_flag: bool = False
                well_known_flag: bool = False
                understand_band: str | None = None
                produce_band: str | None = None
                understand_stability: float | None = None
                produce_stability: float | None = None
                understand_progress: float | None = None
                produce_progress: float | None = None

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
                    # Twin rails (bd tunatale-yh47): per-direction mastery bands.
                    # A word resolved to an exact-surface inflection cloze reads
                    # its PRODUCTION from the cloze itself, but its UNDERSTAND
                    # from the base card step 2 of the resolution order would
                    # have found for this lemma ("none" when there is no base) —
                    # never a second lookup path. Any other resolved card is its
                    # own rails. Ignored/unknown/untracked words keep the None
                    # defaults above.
                    if inflection_match is not None:
                        base_result = _resolve_base_card(
                            db,
                            surface,
                            lemma,
                            base_cache,
                            surface_base_cache,
                            variant_index,
                            inflection_index,
                            lesson.language_code,
                        )
                        rail_rec = (
                            base_result[1].directions.get(Direction.RECOGNITION) if base_result is not None else None
                        )
                        rail_prod = item.directions.get(Direction.PRODUCTION)
                    else:
                        rail_rec = item.directions.get(Direction.RECOGNITION)
                        rail_prod = item.directions.get(Direction.PRODUCTION)
                    understand_band = direction_band(rail_rec)
                    produce_band = direction_band(rail_prod)
                    understand_stability = band_stability(understand_band, rail_rec)
                    produce_stability = band_stability(produce_band, rail_prod)
                    understand_progress = side_progress(rail_rec)
                    produce_progress = side_progress(rail_prod)
                    # Read-ahead keys off RECOGNITION specifically (not active_dir):
                    # reading always evidences recognition, even after the active
                    # direction has flipped to production on graduation.
                    rec_ds = item.directions.get(Direction.RECOGNITION)
                    recognition_reviewable_flag = rec_ds is not None and _is_read_reviewable(rec_ds)
                    recognition_state_val = rec_ds.state.value if rec_ds is not None else None
                    recognition_is_due_flag = _is_due(rec_ds, today) if rec_ds is not None else False
                    # A due card is never "known", however strong: the preview
                    # applies the same guard (it defers only "ahead" cards), and
                    # the mastery line's due bucket relies on the two being
                    # exclusive, which the old due-date rule guaranteed for free.
                    well_known_flag = is_well_known(rec_ds) and not recognition_is_due_flag
                    valid_components = [c for c in components if c is not None]
                    progress_val = compute_mastery_progress(valid_components)

                    if surface.lower() != lemma.lower():
                        feature_str = _inflection_feature_for(surface, analysis_by_surface, lesson.language_code)
                        if feature_str and is_a1_morphology_feature(feature_str, lesson.language_code):
                            base_prod = item.directions.get(Direction.PRODUCTION)
                            if base_prod is None:
                                # Same predicate the /inflection-clozes gate uses:
                                # a word whose production card is a base cloze can
                                # be produced, so the affordance must open for it.
                                # Plain `.get()`: the cache only ever holds None or
                                # a tuple, so a miss and a cached "no covering
                                # cloze" mean the same thing here. (The _UNSET
                                # sentinel matters only where a miss must trigger
                                # a query — the components block above.)
                                covering_for_gate = base_cloze_cache.get(resolved_item_id)
                                if covering_for_gate is not None:
                                    base_prod = covering_for_gate[1].directions.get(Direction.PRODUCTION)
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
                    feature_str = _inflection_feature_for(surface, analysis_by_surface, lesson.language_code)
                    if (
                        feature_str
                        and is_a1_morphology_feature(feature_str, lesson.language_code)
                        and inflection_match is None
                    ):
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
                        recognition_reviewable=recognition_reviewable_flag,
                        recognition_state=recognition_state_val,
                        recognition_is_due=recognition_is_due_flag,
                        well_known=well_known_flag,
                        understand_band=understand_band,
                        produce_band=produce_band,
                        understand_stability=understand_stability,
                        produce_stability=produce_stability,
                        understand_progress=understand_progress,
                        produce_progress=produce_progress,
                    )
                )

            # Annotate collocation spans
            span_annotations = match_spans(lemmas, collocation_index)
            span_cache: dict[int, tuple[str, str, str | None, float | None, bool, str, str]] = {}
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
                        direction_band(coll_item.directions.get(Direction.RECOGNITION)),
                        direction_band(coll_item.directions.get(Direction.PRODUCTION)),
                    )
                    span_cache[span_id] = cached
                (
                    word.collocation_srs_state,
                    word.collocation_lemma,
                    word.collocation_translation,
                    word.collocation_progress,
                    word.collocation_is_due,
                    word.collocation_understand_band,
                    word.collocation_produce_band,
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
