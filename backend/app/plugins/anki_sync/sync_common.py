"""Leaf helpers shared across the sync modules — no internal sync imports.

Moved verbatim out of ``app/anki/sync.py`` (Phase 9 mechanical split): constants,
exceptions, the record/report dataclasses, the Cloze ``Back Extra`` text utils,
and the time helpers. ``app.plugins.anki_sync.sync`` re-exports everything here, so external
imports (tests, archive scripts) keep working unchanged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.models.syntactic_unit import BackField
from app.plugins.anki_sync.sqlite_reader import extract_translation
from app.srs.anki_mirror.rollover import local_today_rollover

KNOWN_ANKI_SCHEMA_VER = 18

# Stage 3b: absolute tolerance for FSRS memory-state comparison between the
# forward-step replay and Anki's cards.data. Matches the strict threshold in
# app/anki/measure_stage3b_premise.py (lines 369/377).
_FSRS_REPLAY_TOLERANCE = 0.01


class DuplicateNoteError(Exception):
    """Raised by OfflineWriter.create_note when the note guid already exists."""

    def __init__(self, note_id: int) -> None:
        super().__init__(f"duplicate note: note_id={note_id}")
        self.note_id = note_id


class OrphanThresholdExceededError(Exception):
    """Refuse to reset Anki ids when too many TT rows look orphaned.

    Trips when >25% of linked directions reference card_ids that are not in
    the live Anki collection — usually a sign the configured deck path is
    pointing at the wrong file, in which case wholesale ID reset would erase
    the user's actual sync state.
    """


@dataclass
class CardRecord:
    anki_card_id: int
    ord: int
    queue: int
    reps: int
    lapses: int
    stability: float
    difficulty: float
    due_at: datetime
    anki_due: int | None = None
    anki_card_mod: int | None = None
    last_review: datetime | None = None
    last_review_ms: int | None = None
    # MIN(revlog.id) for this card. Used by sync_pull to detect the
    # NEW→graded transition when local_dir.prior_state is None (a record
    # written before prior_state was set during sync; self-heal on re-sync).
    first_review_ms: int | None = None
    # False when the source (e.g. AnkiConnect cardsInfo) does not reliably expose
    # FSRS stability/difficulty/due_at — sync_pull then preserves local FSRS
    # state instead of overwriting it with the placeholder values above.
    fsrs_known: bool = True
    card_type: int = 0  # Anki's cards.type (0=New, 1=Learn, 2=Review, 3=Relearn)
    # Required to mirror Anki's queue=1 learning state. Without these, a graded
    # card resumes through the FSRS REVIEW branch and graduates prematurely.
    left: int | None = None


@dataclass
class NoteRecord:
    anki_note_id: int
    anki_guid: str
    l2_text: str
    translation: str
    note: str
    disambig_key: str
    mod: int
    cards: list[CardRecord]
    sentence_translation: str = ""
    article: str = ""
    extras: tuple[BackField, ...] = field(default_factory=tuple)
    is_cloze: bool = False


@dataclass
class SyncConflict:
    guid: str
    direction: str | None
    field: str
    local_value: str | None
    remote_value: str | None
    resolution: str


@dataclass
class RecomputeDivergence:
    collocation_id: int
    direction: str
    replay_stability: float
    replay_difficulty: float
    anki_stability: float
    anki_difficulty: float


@dataclass
class PullReport:
    notes_updated: int = 0
    directions_updated: int = 0
    conflicts: list[SyncConflict] = field(default_factory=list)
    recompute_divergences: list[RecomputeDivergence] = field(default_factory=list)
    skipped_unknown_guid: int = 0


@dataclass
class PushReport:
    notes_pushed: int = 0
    directions_pushed: int = 0


@dataclass
class CreateNewReport:
    count: int = 0
    created: int = 0
    linked: int = 0
    skipped: int = 0
    notes_created_from_anki: int = 0
    image_ok: int = 0
    image_no_results: int = 0
    image_failed: int = 0


_BACK_EXTRA_TRANS = re.compile(r"^\s*<i>([^<]+)</i>\s*<br\s*/?>\s*<br\s*/?>\s*(.*)", re.DOTALL)
_BACK_EXTRA_SENT = re.compile(
    r"^\s*<i>([^<]+)</i>\s*<br\s*/?>\s*<br\s*/?>\s*<span class=\"st\">([^<]*)</span>\s*(.*)", re.DOTALL
)
_SOUND_TAG = re.compile(r"\s*\[sound:[^\]]+\]\s*")


def _strip_sound_tags(back_extra: str) -> str:
    """Remove trailing [sound:...] tags + trailing <br> from a Back Extra string."""
    stripped = _SOUND_TAG.sub("", back_extra)
    stripped = re.sub(r"(?:<br\s*/?>)*\s*$", "", stripped)
    return stripped.rstrip()


def extract_cloze_translation(back_extra: str) -> str:
    """Extract word-level translation from a Cloze note's back_extra (<i>…) field."""
    back_extra = _strip_sound_tags(back_extra)
    m = _BACK_EXTRA_SENT.match(back_extra) or _BACK_EXTRA_TRANS.match(back_extra)
    if m:
        return m.group(1).strip()
    # No leading <i>WORD</i> means there is no word-level translation. The
    # bare-text fallback below exists only for legacy notes that stored the
    # translation as plain text. A morphology cloze (e.g. biti) carries a
    # grammar / sentence span but no <i> — HTML-stripping it here would leak the
    # grammar hint ("biti, 3rd person singular") into the translation column on
    # every sync_pull, so treat the word translation as empty.
    if 'class="grammar"' in back_extra or 'class="st"' in back_extra:
        return ""
    return extract_translation(back_extra)


def extract_cloze_sentence_translation(back_extra: str) -> str:
    """Extract sentence-level translation from a Cloze note's back_extra (<span class="st">…)."""
    back_extra = _strip_sound_tags(back_extra)
    m = _BACK_EXTRA_SENT.match(back_extra)
    if m:
        return m.group(2).strip()
    return ""


def build_cloze_back_extra(
    translation: str,
    sentence_translation: str,
    note: str = "",
    grammar: str = "",
    sentence_audio_filename: str | None = None,
) -> str:
    """Compose a Cloze note's `Back Extra` field from its parts.

    Format: ``<i>WORD</i><br><br><span class="st">SENTENCE</span><br><br>NOTE<br><br><span class="grammar">GRAMMAR</span><br><br>[sound:filename]``,
    skipping any empty part. Single source of truth for both card creation
    (sync_create_new) and edit-push (sync_push).
    """
    parts: list[str] = []
    if translation:
        parts.append(f"<i>{translation}</i>")
    if sentence_translation:
        parts.append(f'<span class="st">{sentence_translation}</span>')
    if note:
        parts.append(note)
    if grammar:
        parts.append(f'<span class="grammar">{grammar}</span>')
    if sentence_audio_filename:
        parts.append(f"[sound:{sentence_audio_filename}]")
    return "<br><br>".join(parts)


def extract_cloze_note(back_extra: str) -> str:
    """Extract note body from a Cloze note's back_extra (after translation/sentence spans)."""
    back_extra = _strip_sound_tags(back_extra)
    m = _BACK_EXTRA_SENT.match(back_extra)
    if m:
        return re.sub(r"^(?:<br\s*/?>)+", "", m.group(3).strip()).strip()
    m = _BACK_EXTRA_TRANS.match(back_extra)
    if m:
        return m.group(2).strip()
    return ""


def _ms_to_datetime(ms: int | None) -> datetime | None:
    """Convert an epoch-milliseconds revlog id to a UTC datetime (None passes through)."""
    return datetime.fromtimestamp(ms / 1000, tz=UTC) if ms is not None else None


# Single-sourced in app.srs.anki_mirror.rollover; the legacy name stays importable here
# (and via the app.plugins.anki_sync.sync facade) for existing call sites and tests.
_local_today_4am = local_today_rollover
