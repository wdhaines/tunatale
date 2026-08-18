"""Pre-staging production images OUTSIDE the sync (tunatale-6xa).

Why this exists, measured 2026-08-17 against the real decks:

    tunatale_no.db: 1487 words awaiting production, **1487 of them with no image**
    tunatale_sl.db:   77 (all unservable Basic/Cloze rows, skipped cheaply)

``promote_production_cards`` mints ``PRODUCTIONS_PER_SYNC = 10`` per sync and,
for each candidate lacking a TT image, fetches one INLINE — an LLM call plus a
Pixabay search and download, serially. So the next ~149 Norwegian syncs each pay
10 live network chains. That is the "sync takes way too long" report.

The fix is not to mint faster. **The 10/sync pacing was settled with the user on
2026-08-15** and is a pedagogical decision, not a performance knob: at a 3
new-cards/day introduction cap there is nothing to gain from minting ahead of
what the learner can meet. This module leaves the pacing untouched and removes
the *fetch* from the critical path instead — by the time promotion runs, the
image is already in TT and the mint is a local file copy.

Two properties that make this safe to run in the background:

- **It never opens the Anki collection.** It writes TT's media table and media
  dir only, via ``store_tt_media``. So it sits outside the Anki safety envelope
  entirely — no lock probe, no second sync sequence, nothing that could collide
  with Anki desktop being open.
- **Disagreeing with promotion is harmless.** The closed-class test here runs
  without ``upos`` (that comes from the Anki reader, which this must not touch),
  so it is weaker than promotion's. Promotion checks closed-class *before* it
  looks for an image, so a pre-staged image for a function word is wasted work,
  never a wrong card.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256

import pytest

from app.cards.media.prestage import prestage_production_images
from app.models.srs_item import Direction, DirectionState, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.anki_mirror.rollover import anki_today, due_at_rollover_utc
from app.srs.database import SRSDatabase

LANG = "no"


@dataclass
class _Media:
    """Stand-in for the media pipeline's result."""

    image_bytes: bytes | None = b"\x89PNG-pretend"
    image_ext: str | None = "png"


class _MediaFn:
    """Records every call so a test can assert a fetch did NOT happen."""

    def __init__(self, *results: _Media | None) -> None:
        self._results = list(results) or [_Media()]
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, word: str, english: str, **kwargs) -> _Media | None:
        self.calls.append((word, english))
        return self._results[min(len(self.calls) - 1, len(self._results) - 1)]


def _add_word(
    db: SRSDatabase,
    word: str,
    english: str,
    *,
    note_id: int,
    card_id: int,
    state: SRSState = SRSState.REVIEW,
) -> int:
    """Seed a word whose recognition has graduated and which has no production."""
    unit = SyntacticUnit(
        text=word, translation=english, word_count=1, difficulty=1, source="anki", frequency=0, disambig_key="noun"
    )
    directions = {
        Direction.RECOGNITION: DirectionState(
            direction=Direction.RECOGNITION,
            due_at=due_at_rollover_utc(anki_today()),
            state=state,
            reps=9 if state is SRSState.REVIEW else 0,
            anki_card_id=card_id,
            last_review=datetime.fromisoformat("2026-08-01T12:00:00+00:00"),
        )
    }
    return db.upsert_by_guid(unit, LANG, directions, anki_note_id=note_id)


@pytest.fixture
def db(tmp_path, monkeypatch) -> SRSDatabase:
    """An in-memory SRS db whose media writes land in a temp dir, not backend/media."""
    monkeypatch.setattr("app.cards.media.vocab_media._MEDIA_DIR", tmp_path / "media")
    return SRSDatabase(":memory:")


class TestPreStaging:
    async def test_stores_an_image_for_a_word_awaiting_production(self, db, tmp_path) -> None:
        coll_id = _add_word(db, "beslutning", "decision", note_id=1000, card_id=10000)
        media_fn = _MediaFn(_Media(image_bytes=b"PNGBYTES", image_ext="png"))

        report = await prestage_production_images(db, media_fn, language_code=LANG, limit=10)

        assert report.fetched == 1
        filename = db.get_image_filename(coll_id)
        assert filename is not None
        assert (tmp_path / "media" / filename).read_bytes() == b"PNGBYTES"

    async def test_filename_is_hash_suffixed_so_a_shared_gloss_cannot_overwrite(self, db) -> None:
        """Two words with the SAME English gloss must not collide.

        `promote_production_cards` documents this hazard directly: "beslutning"
        and "avgjørelse" are both "decision", and a bare `img_<gloss>.jpg` has
        the second fetch overwrite the first word's picture in place — silently
        changing a card the learner already knows. Pre-staging must not
        reintroduce the bare form.
        """
        a = _add_word(db, "beslutning", "decision", note_id=1000, card_id=10000)
        b = _add_word(db, "avgjørelse", "decision", note_id=1001, card_id=10001)
        media_fn = _MediaFn(_Media(image_bytes=b"FIRST"), _Media(image_bytes=b"SECOND"))

        await prestage_production_images(db, media_fn, language_code=LANG, limit=10)

        first, second = db.get_image_filename(a), db.get_image_filename(b)
        assert first != second, "a shared gloss must not produce one shared filename"
        assert sha256(b"FIRST").hexdigest()[:8] in first
        assert sha256(b"SECOND").hexdigest()[:8] in second

    async def test_a_word_that_already_has_an_image_is_not_refetched(self, db) -> None:
        _add_word(db, "hus", "house", note_id=1000, card_id=10000)
        media_fn = _MediaFn()
        await prestage_production_images(db, media_fn, language_code=LANG, limit=10)
        assert len(media_fn.calls) == 1

        again = _MediaFn()
        report = await prestage_production_images(db, again, language_code=LANG, limit=10)

        assert again.calls == [], "an image already in TT must not cost a second fetch"
        assert report.already_had_image == 1

    async def test_a_curated_closed_class_word_costs_no_fetch(self, db) -> None:
        """Promotion routes these to a cloze; spending a live LLM+Pixabay call is waste."""
        _add_word(db, "i", "in", note_id=1000, card_id=10000)
        media_fn = _MediaFn()

        report = await prestage_production_images(db, media_fn, language_code=LANG, limit=10)

        assert media_fn.calls == []
        assert report.skipped_function_word == 1

    async def test_the_no_upos_check_is_weak_and_that_is_recorded_not_assumed(self, db) -> None:
        """⚠️ This skip catches FAR less than promotion's, and the gap is measured.

        Norwegian's curated ``include`` set is 22 surfaces. Real closed-class
        detection comes from the UPOS set (ADP/AUX/CCONJ/DET/PART/PRON/SCONJ),
        which needs an analyzer this job cannot reach — ``upos`` comes from the
        Anki reader, and prod runs ``LEMMATIZER_TYPE=lowercase`` which emits
        ``upos=""`` anyway.

        So "og", "ikke", "den", "er" DO cost a wasted fetch here. That is
        accepted, not overlooked: the fetch is wasted work, never a wrong card,
        because promotion re-tests closed-class with upos before looking for an
        image. This test pins the gap so nobody later reads the skip as complete.
        """
        _add_word(db, "og", "and", note_id=1000, card_id=10000)
        media_fn = _MediaFn()

        report = await prestage_production_images(db, media_fn, language_code=LANG, limit=10)

        assert media_fn.calls == [("og", "and")], "known gap: a conjunction still costs a fetch"
        assert report.skipped_function_word == 0

    async def test_an_empty_image_search_stores_nothing_and_does_not_raise(self, db) -> None:
        coll_id = _add_word(db, "beslutning", "decision", note_id=1000, card_id=10000)
        media_fn = _MediaFn(_Media(image_bytes=None))

        report = await prestage_production_images(db, media_fn, language_code=LANG, limit=10)

        assert (report.fetched, report.no_image) == (0, 1)
        assert db.get_image_filename(coll_id) is None

    async def test_a_media_fn_returning_none_is_tolerated(self, db) -> None:
        """The pipeline can return None outright; a background job must not crash on it."""
        _add_word(db, "beslutning", "decision", note_id=1000, card_id=10000)

        report = await prestage_production_images(db, _MediaFn(None), language_code=LANG, limit=10)

        assert (report.fetched, report.no_image) == (0, 1)

    async def test_the_limit_bounds_the_number_of_live_fetches(self, db) -> None:
        for i in range(5):
            _add_word(db, f"ord{i}", f"word{i}", note_id=1000 + i, card_id=10000 + i)
        media_fn = _MediaFn()

        report = await prestage_production_images(db, media_fn, language_code=LANG, limit=2)

        assert len(media_fn.calls) == 2, "the budget is on live fetches, not rows scanned"
        assert report.fetched == 2

    async def test_a_word_not_awaiting_production_is_never_touched(self, db) -> None:
        """Only graduated words are candidates — the selection query owns this."""
        _add_word(db, "ny", "new", note_id=1000, card_id=10000, state=SRSState.NEW)
        media_fn = _MediaFn()

        report = await prestage_production_images(db, media_fn, language_code=LANG, limit=10)

        assert media_fn.calls == []
        assert report.fetched == 0

    async def test_nothing_to_do_is_quiet_and_cheap(self, db) -> None:
        report = await prestage_production_images(db, _MediaFn(), language_code=LANG, limit=10)
        assert (report.fetched, report.already_had_image, report.no_image) == (0, 0, 0)

    async def test_images_are_deduplicated_within_one_run(self, db) -> None:
        """The shared used_image_urls set is threaded through, as promotion does."""
        _add_word(db, "beslutning", "decision", note_id=1000, card_id=10000)
        _add_word(db, "avgjørelse", "decision", note_id=1001, card_id=10001)
        seen: list[object] = []

        class _Recorder(_MediaFn):
            async def __call__(self, word, english, **kwargs):
                seen.append(kwargs.get("used_image_urls"))
                return await super().__call__(word, english, **kwargs)

        await prestage_production_images(db, _Recorder(), language_code=LANG, limit=10)

        assert len(seen) == 2
        assert seen[0] is seen[1], "both fetches must share one set, or duplicates slip through"
        assert seen[0] is not None
