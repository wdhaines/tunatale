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

from app.cards.media.prestage import IMAGE_REPAIR_LIMIT, prestage_production_images
from app.models.srs_item import Direction, DirectionState, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.anki_mirror.rollover import anki_today, due_at_rollover_utc
from app.srs.database import SRSDatabase

LANG = "no"


@dataclass
class _Media:
    """Stand-in for the media pipeline's result.

    ``image_url`` is on the real ``MediaResult`` and the pre-stage now reads it:
    the URL that produced a duplicate is added to ``used_image_urls`` so the
    retry is handed the next candidate instead of the same one forever. A double
    missing the field made the guard raise rather than run.
    """

    image_bytes: bytes | None = b"\x89PNG-pretend"
    image_ext: str | None = "png"
    image_status: str | None = None
    image_url: str | None = None


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


def _add_repair_word(
    db: SRSDatabase,
    word: str,
    english: str,
    *,
    note_id: int,
    card_id: int,
) -> int:
    """Seed a word that ALREADY has a production card and no image.

    The repair population. A row reaches this state without ever passing the
    mint router: ``upsert_by_guid`` creates whatever directions the Anki
    collection reports, so a note the user made in Anki with both templates
    arrives complete — and ``_AWAITING_PRODUCTION_WHERE`` then excludes it from
    the queue the pre-stage walks, forever. Its one image attempt was the
    best-effort add-time fetch, which records nothing on a miss.
    """
    unit = SyntacticUnit(
        text=word, translation=english, word_count=1, difficulty=1, source="anki", frequency=0, disambig_key="noun"
    )
    directions = {
        Direction.RECOGNITION: DirectionState(
            direction=Direction.RECOGNITION,
            due_at=due_at_rollover_utc(anki_today()),
            state=SRSState.REVIEW,
            reps=9,
            anki_card_id=card_id,
            last_review=datetime.fromisoformat("2026-08-01T12:00:00+00:00"),
        ),
        Direction.PRODUCTION: DirectionState(
            direction=Direction.PRODUCTION,
            due_at=due_at_rollover_utc(anki_today()),
            state=SRSState.LEARNING,
            reps=2,
            anki_card_id=card_id + 1,
            last_review=datetime.fromisoformat("2026-08-01T12:00:00+00:00"),
        ),
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
        media_fn = _MediaFn(_Media(image_bytes=None, image_status="no_results"))

        report = await prestage_production_images(db, media_fn, language_code=LANG, limit=10)

        assert (report.fetched, report.no_image) == (0, 1)
        assert db.get_image_filename(coll_id) is None

    async def test_a_media_fn_returning_none_is_tolerated(self, db) -> None:
        """The pipeline can return None outright; a background job must not crash on it.

        A bare None carries no image_status, so it is no verdict at all — retried
        next pass (counted as a transient), never stamped unpicturable without
        evidence.
        """
        coll_id = _add_word(db, "beslutning", "decision", note_id=1000, card_id=10000)

        report = await prestage_production_images(db, _MediaFn(None), language_code=LANG, limit=10)

        assert (report.fetched, report.no_image) == (0, 0)
        assert report.failed == 1
        assert report.transient == 1
        assert db.get_image_filename(coll_id) is None
        assert db.is_image_unavailable(coll_id) is False

    async def test_the_limit_bounds_the_number_of_live_fetches(self, db) -> None:
        for i in range(5):
            _add_word(db, f"ord{i}", f"word{i}", note_id=1000 + i, card_id=10000 + i)
        # Distinct bytes per word: the default `_MediaFn()` hands back one shared
        # image, which the run's duplicate-picture guard now (correctly) refuses to
        # store twice. That guard is not what this test is about.
        media_fn = _MediaFn(_Media(image_bytes=b"ONE"), _Media(image_bytes=b"TWO"))

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

        # Three calls, not two: the default `_MediaFn` hands both words the same
        # bytes, so the second is refused as a duplicate and re-fetched once with
        # the offending URL barred. That retry is the thing that makes refusing
        # converge instead of looping, so it must share the set too.
        assert len(seen) == 3
        assert seen[0] is seen[1] is seen[2], "every fetch must share one set, or duplicates slip through"
        assert seen[0] is not None


class TestConcurrentFetching:
    """Fetching in parallel (tunatale-byw).

    A pre-stage pass refills the buffer the mint drains. Serially at a measured
    10.0s median per image, refilling 20 takes ~200s — and the real trigger for a
    slow sync was syncing again ~2 minutes later, before the buffer had refilled.
    So the refill has to outrun the user, not merely happen.

    This is safe to parallelise precisely because of the property in this module's
    docstring: the pass never opens the Anki collection. It writes TT media only,
    so there is no lock, no sync sequence and no Anki-side ordering to preserve.
    """

    async def test_fetches_overlap_rather_than_running_one_at_a_time(self, db) -> None:
        """The point of the change: N images must not cost N x one-image latency."""
        import asyncio

        in_flight = 0
        peak = 0

        async def slow_media_fn(word, english, **kwargs):
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0)  # yield, so a serial implementation cannot overlap
            await asyncio.sleep(0)
            in_flight -= 1
            return _Media(image_bytes=f"IMG-{word}".encode(), image_ext="png")

        for i in range(6):
            _add_word(db, f"ord{i}", f"word{i}", note_id=1000 + i, card_id=10000 + i)

        report = await prestage_production_images(db, slow_media_fn, language_code=LANG, limit=6)

        assert report.fetched == 6
        assert peak > 1, "fetches ran strictly one at a time — the refill is still serial"

    async def test_concurrency_is_capped(self, db) -> None:
        """Unbounded fan-out at Pixabay/LLM rate limits trades one problem for another."""
        import asyncio

        from app.cards.media.prestage import PRESTAGE_CONCURRENCY

        in_flight = 0
        peak = 0

        async def slow_media_fn(word, english, **kwargs):
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0)
            in_flight -= 1
            return _Media(image_bytes=f"IMG-{word}".encode(), image_ext="png")

        for i in range(PRESTAGE_CONCURRENCY * 3):
            _add_word(db, f"ord{i}", f"word{i}", note_id=1000 + i, card_id=10000 + i)

        await prestage_production_images(db, slow_media_fn, language_code=LANG, limit=PRESTAGE_CONCURRENCY * 3)

        assert peak <= PRESTAGE_CONCURRENCY, f"{peak} concurrent fetches exceeds the cap"

    async def test_two_words_cannot_be_given_the_same_picture(self, db) -> None:
        """The dedup invariant must survive concurrency.

        Serially, `used_image_urls` stopped two words being handed one picture.
        Concurrent calls all see the set as it was when they started, so that guard
        alone no longer holds — two words with a shared English gloss can come back
        with identical bytes. Content-identical images are the observable form of
        the bug, so the pass must refuse to store the second one rather than
        silently give two cards the same picture.
        """
        same = _Media(image_bytes=b"IDENTICAL-BYTES", image_ext="png")

        async def duplicating_media_fn(word, english, **kwargs):
            return same

        a = _add_word(db, "beslutning", "decision", note_id=1000, card_id=10000)
        b = _add_word(db, "avgjørelse", "decision", note_id=1001, card_id=10001)

        await prestage_production_images(db, duplicating_media_fn, language_code=LANG, limit=10)

        images = [db.get_image_filename(a), db.get_image_filename(b)]
        assert images.count(None) == 1, f"both words were given the same picture: {images}"


class TestUnpicturableWords:
    """The verdict the mint can no longer reach on its own (tunatale-byw).

    The mint used to fetch inline and, on an empty image search, route the word to
    a cloze. With the fetch gone it sees only "no image in TT", which now means
    either *not staged yet* (wait) or *cannot be pictured* (cloze) — opposite
    handling. Only this pass can tell the two apart, so it records the answer.
    """

    async def test_an_empty_image_search_is_recorded_for_the_mint(self, db) -> None:
        coll_id = _add_word(db, "foranledning", "occasion", note_id=1000, card_id=10000)

        report = await prestage_production_images(
            db, _MediaFn(_Media(image_bytes=None, image_status="no_results")), language_code=LANG, limit=10
        )

        assert report.no_image == 1
        assert db.is_image_unavailable(coll_id), "the mint cannot cloze this word without the marker"

    async def test_a_known_unpicturable_word_is_never_refetched(self, db) -> None:
        """Otherwise the same word burns a live fetch on every pass, forever.

        The pre-stage budget is the scarce thing here: 20 fetches a pass against a
        1200-word backlog. Re-searching words already known to have no picture
        would starve the words that do.
        """
        _add_word(db, "foranledning", "occasion", note_id=1000, card_id=10000)
        await prestage_production_images(
            db, _MediaFn(_Media(image_bytes=None, image_status="no_results")), language_code=LANG, limit=10
        )

        again = _MediaFn()
        report = await prestage_production_images(db, again, language_code=LANG, limit=10)

        assert again.calls == [], "a word already known to be unpicturable cost another live fetch"
        assert report.fetched == 0


class TestImageStatusSplitsTransientFromSettled:
    """tunatale-fwe5 — a transient fetch failure must not stamp a permanent verdict.

    ``mark_image_unavailable`` has no clearing path anywhere in the tree; once set,
    ``promote_production_cards`` routes the word to a permanent cloze. So the only
    statuses allowed to reach the marker are evidence ABOUT THE WORD: ``no_results``
    (searched, nothing usable) and ``skipped`` (the explicit abstract-word
    sentinel). ``rate_limited`` and ``api_error`` say "could not search" — nothing
    about the word — so they get the same treatment as a raised fetch: counted,
    not marked, retried next pass. An unset status with no bytes is the same
    default: a wrong "mark" is permanent, while a wrong retry costs one more search.
    """

    async def test_rate_limited_does_not_mark_and_is_retried(self, db) -> None:
        coll_id = _add_word(db, "beslutning", "decision", note_id=1000, card_id=10000)

        report = await prestage_production_images(
            db, _MediaFn(_Media(image_bytes=None, image_status="rate_limited")), language_code=LANG, limit=10
        )

        assert report.failed == 1
        assert report.transient == 1
        assert report.no_image == 0
        assert db.is_image_unavailable(coll_id) is False

    async def test_api_error_does_not_mark_and_is_retried(self, db) -> None:
        coll_id = _add_word(db, "beslutning", "decision", note_id=1000, card_id=10000)

        report = await prestage_production_images(
            db, _MediaFn(_Media(image_bytes=None, image_status="api_error")), language_code=LANG, limit=10
        )

        assert report.failed == 1
        assert report.transient == 1
        assert report.no_image == 0
        assert db.is_image_unavailable(coll_id) is False

    async def test_no_results_marks_unavailable(self, db) -> None:
        coll_id = _add_word(db, "foranledning", "occasion", note_id=1000, card_id=10000)

        report = await prestage_production_images(
            db, _MediaFn(_Media(image_bytes=None, image_status="no_results")), language_code=LANG, limit=10
        )

        assert report.no_image == 1
        assert report.transient == 0
        assert db.is_image_unavailable(coll_id) is True

    async def test_skipped_marks_unavailable(self, db) -> None:
        coll_id = _add_word(db, "foranledning", "occasion", note_id=1000, card_id=10000)

        report = await prestage_production_images(
            db, _MediaFn(_Media(image_bytes=None, image_status="skipped")), language_code=LANG, limit=10
        )

        assert report.no_image == 1
        assert report.transient == 0
        assert db.is_image_unavailable(coll_id) is True

    async def test_ok_stores_and_marks_nothing(self, db, tmp_path) -> None:
        coll_id = _add_word(db, "beslutning", "decision", note_id=1000, card_id=10000)

        report = await prestage_production_images(
            db,
            _MediaFn(_Media(image_bytes=b"PNGBYTES", image_ext="png", image_status="ok")),
            language_code=LANG,
            limit=10,
        )

        assert report.fetched == 1
        assert report.no_image == 0
        assert (tmp_path / "media" / db.get_image_filename(coll_id)).read_bytes() == b"PNGBYTES"
        assert db.is_image_unavailable(coll_id) is False

    async def test_missing_status_with_no_bytes_is_not_marked(self, db) -> None:
        """The safe default: an unset status is not a verdict about the word."""
        coll_id = _add_word(db, "beslutning", "decision", note_id=1000, card_id=10000)

        report = await prestage_production_images(
            db, _MediaFn(_Media(image_bytes=None, image_status=None)), language_code=LANG, limit=10
        )

        assert report.no_image == 0
        assert report.transient == 1
        assert db.is_image_unavailable(coll_id) is False

    async def test_the_summary_line_counts_transient_fetches(self, db, caplog) -> None:
        """A reader must be able to tell "searched, nothing there" from "could not
        search"; `no_image` keeps meaning the settled verdict and never counts these."""
        _add_word(db, "beslutning", "decision", note_id=1000, card_id=10000)

        with caplog.at_level("WARNING"):
            await prestage_production_images(
                db, _MediaFn(_Media(image_bytes=None, image_status="api_error")), language_code=LANG, limit=10
            )

        assert "PRESTAGE_IMAGES fetched=0" in caplog.text
        assert "no_image=0" in caplog.text
        assert "transient=1" in caplog.text

    async def test_marking_unavailable_names_the_word_and_the_status(self, db, caplog) -> None:
        """The mark moment must be findable by grep; before this change the word
        and the reason appeared nowhere, and the defect had to be found with a
        database query instead."""
        _add_word(db, "foranledning", "occasion", note_id=1000, card_id=10000)

        with caplog.at_level("WARNING"):
            await prestage_production_images(
                db, _MediaFn(_Media(image_bytes=None, image_status="no_results")), language_code=LANG, limit=10
            )

        assert "PRESTAGE_IMAGES" in caplog.text
        assert "foranledning" in caplog.text
        assert "no_results" in caplog.text


class TestNumberWords:
    """A number word is drawn, never searched for (tunatale-elrj).

    The picture of a quantity is exact by construction, so it costs no fetch, no
    API key and no network — and unlike a photo, its count is assertable. These
    tests are the reason the render was chosen over an image search: for a
    fetched photo there is no oracle for "this shows exactly five things" at all.
    """

    async def test_draws_the_quantity_instead_of_fetching_a_photo(self, db) -> None:
        media_fn = _MediaFn()
        coll_id = _add_word(db, "fem", "five", note_id=1000, card_id=10000)

        report = await prestage_production_images(db, media_fn, language_code=LANG, limit=10)

        assert media_fn.calls == [], "a number word must never reach the image search"
        assert (report.rendered_number, report.fetched) == (1, 0)
        assert db.get_image_filename(coll_id) is not None

    async def test_the_stored_picture_shows_exactly_that_many_objects(self, db, tmp_path) -> None:
        """The oracle a fetched photo could never satisfy."""
        coll_id = _add_word(db, "syv", "seven", note_id=1000, card_id=10000)

        await prestage_production_images(db, _MediaFn(), language_code=LANG, limit=10)

        stored = (tmp_path / "media" / db.get_image_filename(coll_id)).read_bytes()
        assert stored.count(b"<circle") == 7

    async def test_an_excluded_number_still_takes_the_ordinary_route(self, db) -> None:
        """Norwegian `en` is 'one' AND 'a/an'; it is curated as a function word."""
        media_fn = _MediaFn()
        _add_word(db, "en", "a, an, one", note_id=1000, card_id=10000)

        report = await prestage_production_images(db, media_fn, language_code=LANG, limit=10)

        assert media_fn.calls == []
        assert (report.rendered_number, report.skipped_function_word) == (0, 1)

    async def test_a_number_too_large_to_draw_takes_the_ordinary_route(self, db) -> None:
        """`tusen` is a hundred rods — a texture, not a countable set."""
        media_fn = _MediaFn()
        _add_word(db, "tusen", "thousand", note_id=1000, card_id=10000)

        report = await prestage_production_images(db, media_fn, language_code=LANG, limit=10)

        assert report.rendered_number == 0
        assert media_fn.calls == [("tusen", "thousand")], "it is not closed-class, so it is a normal candidate"

    async def test_a_number_already_drawn_is_not_drawn_again(self, db) -> None:
        _add_word(db, "fem", "five", note_id=1000, card_id=10000)
        await prestage_production_images(db, _MediaFn(), language_code=LANG, limit=10)

        report = await prestage_production_images(db, _MediaFn(), language_code=LANG, limit=10)

        assert (report.rendered_number, report.already_had_image) == (0, 1)

    async def test_a_stale_unpicturable_marker_does_not_block_the_render(self, db) -> None:
        """The marker means "the photo search found nothing", which is now moot.

        Every number word in the real deck predates this feature, and one that
        was searched for and came back empty carries the marker that routes a
        word permanently to a cloze. Drawing is checked first so that verdict —
        reached about a photo — cannot outlive the reason for it.
        """
        coll_id = _add_word(db, "fem", "five", note_id=1000, card_id=10000)
        db.mark_image_unavailable(coll_id)

        report = await prestage_production_images(db, _MediaFn(), language_code=LANG, limit=10)

        assert report.rendered_number == 1
        assert db.get_image_filename(coll_id) is not None

    async def test_renders_do_not_consume_the_fetch_budget(self, db) -> None:
        """The budget exists to bound live network calls; a render is neither."""
        for i, word in enumerate(("to", "tre", "fire", "fem")):
            _add_word(db, word, f"n{i}", note_id=1000 + i, card_id=10000 + i)
        _add_word(db, "beslutning", "decision", note_id=1100, card_id=11000)

        media_fn = _MediaFn()
        report = await prestage_production_images(db, media_fn, language_code=LANG, limit=1)

        assert report.rendered_number == 4
        assert media_fn.calls == [("beslutning", "decision")]

    async def test_the_summary_line_names_the_renders(self, db, caplog) -> None:
        """A counter absent from the durable line is a counter nobody can read."""
        _add_word(db, "fem", "five", note_id=1000, card_id=10000)

        with caplog.at_level("WARNING"):
            await prestage_production_images(db, _MediaFn(), language_code=LANG, limit=10)

        assert "drawn=1" in caplog.text


class _RaisingMediaFn:
    """Fetches that raise for named words, and succeed for the rest.

    The real failure this models is a live one: the fetch is an LLM call plus a
    Pixabay round trip, either of which can raise on a 429, a timeout or a
    transport error, and it runs inside a FastAPI BackgroundTask where an escaped
    exception is swallowed with no trace.
    """

    def __init__(self, *, raise_on: set[str]) -> None:
        self.raise_on = raise_on
        self.calls: list[str] = []

    async def __call__(self, word: str, english: str, **kwargs):
        self.calls.append(word)
        if word in self.raise_on:
            raise RuntimeError(f"pixabay exploded for {word}")
        # Distinct bytes per word. Identical bytes hash to one filename and trip
        # the duplicate-image guard, which would count a successful fetch as
        # no_image and quietly weaken what this class is testing.
        return _Media(image_bytes=f"PNG-{word}".encode())


class TestAFailingFetchIsCountedNotSwallowed:
    """tunatale-ouk.10 — the pre-stage stalled and left no evidence anywhere.

    Symptom on the real deck: awaiting_image=173 with minted=0 across six syncs,
    ~4 images produced in a day against a limit of 20 PER SYNC, and
    image_unavailable_at set on 0 of 3100 collocations.

    Cause: `asyncio.gather(..., return_exceptions=False)`. One raising fetch
    abandons the whole batch — the other words are never stored, pass 3 never
    runs so no word is marked unpicturable, and the exception escapes into a
    background task that discards it. The pass leaves the DB and the log exactly
    as it found them, which is indistinguishable from never having run.
    """

    async def test_one_raising_fetch_does_not_discard_the_rest_of_the_batch(self, db, tmp_path):
        ids = {
            word: _add_word(db, word, eng, note_id=100 + i, card_id=200 + i)
            for i, (word, eng) in enumerate([("hus", "house"), ("bil", "car"), ("bok", "book")])
        }

        media_fn = _RaisingMediaFn(raise_on={"bil"})
        report = await prestage_production_images(db, media_fn, language_code=LANG, limit=10)

        assert report.failed == 1
        # The other two must still be stored. Under return_exceptions=False they
        # were not — that is the whole bug.
        assert report.fetched == 2
        stored = [w for w, cid in ids.items() if db.get_image_filename(cid) is not None]
        assert stored == ["hus", "bok"]

    async def test_a_raising_fetch_is_never_marked_unpicturable(self, db):
        """A crash must not be recorded as "cannot be pictured".

        The existing `return_exceptions=False` comment chose to let failures
        surface precisely so they were not "silently counted as no_image and
        turned into a cloze by the mint" — that reasoning is right and survives.
        Catching the exception must therefore count it apart from `no_image`,
        because `mark_image_unavailable` is what routes a word to a permanent
        cloze, and a transient 429 is not evidence a word cannot be pictured.
        """
        coll_id = _add_word(db, "bil", "car", note_id=100, card_id=200)

        report = await prestage_production_images(db, _RaisingMediaFn(raise_on={"bil"}), language_code=LANG, limit=10)

        assert report.failed == 1
        assert report.no_image == 0
        assert db.is_image_unavailable(coll_id) is False


class TestTheSummaryReachesTheDurableLog:
    """The counters must survive in ~/.tunatale/logs/sync.log.

    They were `logger.info`, and start-dev.sh runs uvicorn at `--log-level
    warning` and redirects it nowhere — so the line was filtered out AND had
    nowhere to land. Measured on the real log before this change:
    `grep -c PRESTAGE ~/.tunatale/logs/sync.log` -> 0, ever.

    Identical defect to the one fixed for PRODUCTION_MINT in b7211aa
    (tunatale-7wsv), one component over. Same fix: persist beside SYNC_SOAK and
    grep the file, not the scrollback.
    """

    async def test_the_counters_are_appended_to_the_sync_log(self, db, tmp_path, monkeypatch):
        from app.config import settings

        log_path = tmp_path / "logs" / "sync.log"
        monkeypatch.setattr(settings, "sync_log", log_path)
        _add_word(db, "hus", "house", note_id=100, card_id=200)

        await prestage_production_images(db, _MediaFn(_Media()), language_code=LANG, limit=10)

        line = next(ln for ln in log_path.read_text().splitlines() if "PRESTAGE_IMAGES" in ln)
        for field in ("fetched=1", "already=0", "function_word=0", "no_image=0", "failed=0"):
            assert field in line, f"{field} missing from {line!r}"

    async def test_a_pass_that_did_nothing_still_writes_a_line(self, db, tmp_path, monkeypatch):
        """The all-zero pass is the MOST diagnostic one and used to be the only
        one guaranteed silent: the emit was guarded on `if fetched or missing`.

        "The pre-stage ran and found nothing to do" and "the pre-stage never ran"
        are the two hypotheses a reader needs to separate, and the guard made
        them produce identical evidence. That ambiguity is exactly what left
        ouk.10 undiagnosable.
        """
        from app.config import settings

        log_path = tmp_path / "logs" / "sync.log"
        monkeypatch.setattr(settings, "sync_log", log_path)

        report = await prestage_production_images(db, _MediaFn(_Media()), language_code=LANG, limit=10)

        assert report == report.__class__()  # all zeros
        assert "PRESTAGE_IMAGES fetched=0" in log_path.read_text()

    async def test_an_unwritable_log_does_not_break_the_pass(self, db, tmp_path, monkeypatch, caplog):
        """Observability is best-effort and must never take down a pre-stage pass.

        The whole point of this change is diagnosing a pass that fails silently;
        introducing a NEW way for it to die — a full disk, a read-only home, a
        path whose parent is a file — would be a poor trade. The failure is
        logged rather than raised.
        """
        from app.config import settings

        blocker = tmp_path / "not-a-dir"
        blocker.write_text("i am a file")
        monkeypatch.setattr(settings, "sync_log", blocker / "logs" / "sync.log")
        coll_id = _add_word(db, "hus", "house", note_id=100, card_id=200)

        report = await prestage_production_images(db, _MediaFn(_Media()), language_code=LANG, limit=10)

        assert report.fetched == 1
        assert db.get_image_filename(coll_id) is not None
        assert "could not be persisted" in caplog.text


class TestTheFailureCauseIsNamed:
    """tunatale-ouk.11 — `failed=N` says something broke, not what.

    The count shipped in cc0a18a; the traceback went to `logger.warning(...,
    exc_info=...)`, which is the ephemeral channel uvicorn filters and
    start-dev.sh redirects nowhere. So the same defect this whole thread was
    about still applied to the REASON, one level down. Diagnosing ouk.10 took a
    copy of the production DB, a pass-1 probe and a process-tree check; with the
    exception type on the durable line it would have been one read.

    It also gates a real decision: PRESTAGE_CONCURRENCY = 5 is documented as "a
    deliberate compromise, not a measurement", capped to avoid 429s. Whether an
    observed failure IS a 429 decides whether 5 is too high or merely unmeasured,
    and those point opposite ways.
    """

    async def _run(self, db, tmp_path, monkeypatch, media_fn, limit=10):
        from app.config import settings

        log_path = tmp_path / "logs" / "sync.log"
        monkeypatch.setattr(settings, "sync_log", log_path)
        report = await prestage_production_images(db, media_fn, language_code=LANG, limit=limit)
        line = next(ln for ln in log_path.read_text().splitlines() if "PRESTAGE_IMAGES" in ln)
        return report, line

    async def test_the_exception_type_and_message_reach_the_durable_line(self, db, tmp_path, monkeypatch):
        _add_word(db, "bil", "car", note_id=100, card_id=200)

        _, line = await self._run(db, tmp_path, monkeypatch, _RaisingMediaFn(raise_on={"bil"}))

        assert "failed=1" in line
        assert "RuntimeError" in line, f"exception type missing from {line!r}"
        assert "pixabay exploded" in line, f"exception message missing from {line!r}"

    async def test_no_failures_means_no_failures_field(self, db, tmp_path, monkeypatch):
        """A clean pass must not carry an empty `failures=` tail — `failed=0`
        already says it, and a dangling field invites a reader to wonder."""
        _add_word(db, "hus", "house", note_id=100, card_id=200)

        report, line = await self._run(db, tmp_path, monkeypatch, _MediaFn(_Media()))

        assert report.failed == 0
        assert "failed=0" in line
        assert "failures=" not in line

    async def test_a_systematic_failure_cannot_flood_the_log(self, db, tmp_path, monkeypatch):
        """20 identical failures must not write 20 messages into sync.log EVERY
        pass. The pre-stage runs after every sync, so an unbounded tail here is a
        slow-motion disk filler on the one file the operator greps."""
        words = [(f"ord{i}", f"word{i}") for i in range(12)]
        for i, (w, e) in enumerate(words):
            _add_word(db, w, e, note_id=100 + i, card_id=200 + i)

        report, line = await self._run(
            db, tmp_path, monkeypatch, _RaisingMediaFn(raise_on={w for w, _ in words}), limit=12
        )

        assert report.failed == 12
        assert len(report.failures) <= 3, "the reason list must be capped"
        assert len(line) < 400, f"line is {len(line)} chars: {line!r}"

    async def test_a_long_message_is_truncated(self, db, tmp_path, monkeypatch):
        class _Long:
            calls: list[str] = []

            async def __call__(self, word, english, **kwargs):
                raise RuntimeError("x" * 5000)

        _add_word(db, "bil", "car", note_id=100, card_id=200)

        report, line = await self._run(db, tmp_path, monkeypatch, _Long())

        assert report.failed == 1
        assert len(report.failures[0]) <= 100
        assert len(line) < 400

    async def test_a_url_query_string_is_redacted(self, db, tmp_path, monkeypatch):
        """⚠️ The fetch chain is an LLM call plus a Pixabay request, and this
        text is written to a file on disk. An exception whose message echoes the
        request URL would persist the API key with it. Truncation alone is not a
        defence — the key can sit inside the first 80 characters."""

        class _Leaky:
            calls: list[str] = []

            async def __call__(self, word, english, **kwargs):
                raise RuntimeError("GET https://pixabay.com/api/?key=SECRETKEY123&q=car failed with 429")

        _add_word(db, "bil", "car", note_id=100, card_id=200)

        report, line = await self._run(db, tmp_path, monkeypatch, _Leaky())

        assert "SECRETKEY123" not in line, f"API key leaked into the durable log: {line!r}"
        assert "SECRETKEY123" not in report.failures[0]
        # The diagnostic value must survive the redaction.
        assert "RuntimeError" in line
        assert "429" in line

    async def test_a_query_param_outside_the_keyword_list_is_still_redacted(self, db, tmp_path, monkeypatch):
        """Discriminates the URL rule from the keyword rule.

        Written because the first version of the leak test above did NOT: its
        `?key=…` is caught by _KEYED_SECRET alone, so deleting _URL_QUERY left
        the whole suite green and the URL rule was decoration by accident. A
        credential does not have to be spelled `key` — `?auth=`, `?sig=`,
        `?t=` are all real — so the URL rule carries its own weight and needs its
        own oracle.
        """

        class _Leaky:
            calls: list[str] = []

            async def __call__(self, word, english, **kwargs):
                raise RuntimeError("GET https://api.example.com/v1/img?auth=LEAKEDVALUE99&q=car -> 500")

        _add_word(db, "bil", "car", note_id=100, card_id=200)

        report, line = await self._run(db, tmp_path, monkeypatch, _Leaky())

        assert "LEAKEDVALUE99" not in line, f"query-string credential leaked: {line!r}"
        assert "LEAKEDVALUE99" not in report.failures[0]
        assert "RuntimeError" in line

    async def test_a_bare_keyed_credential_with_no_url_is_redacted(self, db, tmp_path, monkeypatch):
        """Discriminates the keyword rule from the URL rule — the mirror of the
        test above.

        The two redactions cover each other on a `?key=…` inside a URL, so each
        needs a case only IT can catch or the pair is untested by accident. Here
        there is no URL, so _URL_QUERY cannot fire and only _KEYED_SECRET can
        keep the value out of the log.
        """

        class _Leaky:
            calls: list[str] = []

            async def __call__(self, word, english, **kwargs):
                raise RuntimeError("authentication rejected: api_key=BARESECRET77 is not valid")

        _add_word(db, "bil", "car", note_id=100, card_id=200)

        report, line = await self._run(db, tmp_path, monkeypatch, _Leaky())

        assert "BARESECRET77" not in line, f"bare credential leaked: {line!r}"
        assert "BARESECRET77" not in report.failures[0]
        assert "RuntimeError" in line

    async def test_identical_failures_collapse_to_one_reason(self, db, tmp_path, monkeypatch):
        """De-duplication, which is what makes a systematic fault READABLE.

        The realistic shape is not twelve different errors — it is one cause
        hitting every fetch in the batch (a 429, an expired key, DNS). Twelve
        copies of one string would spend the whole cap restating it and crowd
        out any second, different cause. `failed=12` already carries the
        multiplicity; the list carries the KIND.

        Distinct from the flood test above, which raises a DIFFERENT message per
        word and so only exercises the cap. This exercises the dedupe.
        """

        class _SameError:
            calls: list[str] = []

            async def __call__(self, word, english, **kwargs):
                raise RuntimeError("429 Too Many Requests")

        for i in range(6):
            _add_word(db, f"ord{i}", f"word{i}", note_id=300 + i, card_id=400 + i)

        report, line = await self._run(db, tmp_path, monkeypatch, _SameError(), limit=6)

        assert report.failed == 6
        assert report.failures == ("RuntimeError:429 Too Many Requests",)
        assert line.count("429 Too Many Requests") == 1


class TestRepairingProductionCardsWithNoImage:
    """The blind spot the mint queue's predicate creates (2026-09-06).

    ``_AWAITING_PRODUCTION_WHERE`` excludes any row that already has a production
    direction — right for minting, wrong for image acquisition, because the
    pre-stage walks the same list. A vocab row can acquire that direction without
    ever passing the mint router (``upsert_by_guid`` mirrors whatever Anki
    reports), so its ONLY image attempt was the add-time fetch in
    ``generate_vocab_media``, which is best-effort and records nothing on a miss:
    no media row, and no ``image_unavailable_at`` either.

    Found on the real deck: `den gangen`, 1 of 815 Norwegian vocab production
    cards, imageless and clozeless since 2026-08-31 while its three batch-mates —
    added in the same two seconds — all got pictures. Nothing retried it and
    nothing counted it, which is the part worth fixing: the population was
    invisible, not merely unserved.

    ⚠️ A cloze is NOT the alternative remedy here. ``_fallback_to_cloze`` is
    reachable only from the mint router, which by construction never sees these
    rows. An image is the only front they can get.
    """

    async def test_a_production_card_with_no_image_is_repaired(self, db, tmp_path) -> None:
        coll_id = _add_repair_word(db, "den gangen", "that time", note_id=1000, card_id=10000)
        media_fn = _MediaFn(_Media(image_bytes=b"REPAIRED", image_ext="jpg"))

        report = await prestage_production_images(db, media_fn, language_code=LANG, limit=10)

        assert report.repaired == 1
        filename = db.get_image_filename(coll_id)
        assert filename is not None
        assert (tmp_path / "media" / filename).read_bytes() == b"REPAIRED"

    async def test_the_repair_flags_the_row_so_the_picture_reaches_anki(self, db) -> None:
        """Without this the repair is visible in TT and invisible in Anki.

        The card already exists with an empty Image field, so the picture reaches
        it only through ``sync_push``'s vocab branch — which writes ``Image`` only
        for a row flagged "image". ``store_tt_media`` alone sets no flag.
        """
        coll_id = _add_repair_word(db, "den gangen", "that time", note_id=1000, card_id=10000)

        await prestage_production_images(db, _MediaFn(), language_code=LANG, limit=10)

        _rid, item, _lang = db.get_collocation_by_id(coll_id)
        assert "image" in db.get_dirty_fields(item.guid)

    async def test_a_mint_candidate_is_not_flagged_dirty(self, db) -> None:
        """The discriminator. A word awaiting production has no Anki card yet —
        ``mint_production_card`` writes the Image field when it creates one, so a
        flag here would queue a redundant field write on every such word."""
        coll_id = _add_word(db, "beslutning", "decision", note_id=1000, card_id=10000)

        report = await prestage_production_images(db, _MediaFn(), language_code=LANG, limit=10)

        assert report.fetched == 1 and report.repaired == 0
        _rid, item, _lang = db.get_collocation_by_id(coll_id)
        assert "image" not in db.get_dirty_fields(item.guid)

    async def test_a_production_card_that_already_has_an_image_is_left_alone(self, db) -> None:
        coll_id = _add_repair_word(db, "hus", "house", note_id=1000, card_id=10000)
        db.add_media(coll_id, "image", "img_house_aaaaaaaa.jpg", "media/x.jpg", "x.jpg", "0" * 64, 1)
        media_fn = _MediaFn()

        report = await prestage_production_images(db, media_fn, language_code=LANG, limit=10)

        assert media_fn.calls == []
        assert report.repaired == 0

    async def test_a_production_card_served_by_a_cloze_is_left_alone(self, db) -> None:
        """A clozed word has a front already; an image would be a second one."""
        base_id = _add_repair_word(db, "beslutning", "decision", note_id=1000, card_id=10000)
        db.add_collocation(
            SyntacticUnit(
                text="beslutning",
                translation="",
                word_count=1,
                difficulty=1,
                source="anki",
                lemma="beslutning",
                disambig_key="cloze:beslutning",
                card_type="cloze",
                source_sentence="Det var en vanskelig {{c1::beslutning}}.",
            ),
            language_code=LANG,
        )
        # NOT get_collocation_by_lemma: the word's vocab row carries the same
        # lemma, so that lookup is ambiguous and silently returns the wrong row —
        # which made the first version of this test pass a base id to itself and
        # go red for the wrong reason.
        with db._get_conn() as conn:
            cloze_id = conn.execute(
                "SELECT id FROM collocations WHERE card_type = 'cloze' AND lemma = 'beslutning'"
            ).fetchone()["id"]
        db.set_base_collocation_id(cloze_id, base_id)
        assert db.get_base_collocation_id(cloze_id) == base_id, "seeding failed: the cloze is not linked"
        media_fn = _MediaFn()

        report = await prestage_production_images(db, media_fn, language_code=LANG, limit=10)

        assert media_fn.calls == []
        assert report.repair_queue == 0

    async def test_a_settled_unpicturable_card_stops_being_retried(self, db) -> None:
        """Otherwise the repair queue costs a live fetch on the same word forever.

        The first pass searches and gets ``no_results``, which is a verdict about
        the word rather than about the network, so it is recorded — and the query
        then excludes the row.
        """
        _add_repair_word(db, "janez", "Janez", note_id=1000, card_id=10000)
        media_fn = _MediaFn(_Media(image_bytes=None, image_status="no_results"))

        first = await prestage_production_images(db, media_fn, language_code=LANG, limit=10)
        assert first.repair_queue == 1 and first.no_image == 1

        second = await prestage_production_images(db, media_fn, language_code=LANG, limit=10)
        assert second.repair_queue == 0
        assert len(media_fn.calls) == 1, "the word must be searched once, not once per pass"

    async def test_settled_rows_do_not_starve_a_repairable_one(self, db) -> None:
        """Why ``image_unavailable_at IS NULL`` is in the SQL and not only in triage.

        ``_triage`` skips a settled row either way, so the clause looks redundant
        — a sabotage drill removing it left every other test in this class green.
        It earns its place under BUDGET pressure: the query returns
        ``IMAGE_REPAIR_LIMIT`` rows in deck order, so without the clause three
        settled words with low note ids fill that window and the repairable word
        behind them is never returned at all. Permanently, since deck order is
        immutable.
        """
        for i in range(IMAGE_REPAIR_LIMIT):
            settled = _add_repair_word(db, f"unpicturable{i}", f"abstract {i}", note_id=100 + i, card_id=1000 + i * 10)
            db.mark_image_unavailable(settled)
        # A higher note id, so it sorts BEHIND all three in deck order.
        wanted = _add_repair_word(db, "den gangen", "that time", note_id=9000, card_id=90000)

        report = await prestage_production_images(db, _MediaFn(), language_code=LANG, limit=0)

        assert report.repaired == 1
        assert db.get_image_filename(wanted) is not None

    async def test_a_transient_failure_leaves_the_card_in_the_queue(self, db) -> None:
        """A 429 is not a claim about the word (tunatale-fwe5, same rule as the
        mint queue): it must not settle the row out of the repair queue."""
        _add_repair_word(db, "den gangen", "that time", note_id=1000, card_id=10000)
        media_fn = _MediaFn(_Media(image_bytes=None, image_status="rate_limited"))

        first = await prestage_production_images(db, media_fn, language_code=LANG, limit=10)
        assert first.transient == 1 and first.repaired == 0

        second = await prestage_production_images(db, media_fn, language_code=LANG, limit=10)
        assert second.repair_queue == 1, "a rate-limited word must still be waiting"

    async def test_the_repair_budget_is_additive_not_carved_out_of_limit(self, db) -> None:
        """The reason ``IMAGE_REPAIR_LIMIT`` exists as a separate number.

        The Norwegian mint queue is 1487 rows deep and fills ``limit`` on every
        pass. A repair sharing that budget would never get a turn, and the fix
        would be inert on the only deck that has the bug.
        """
        _add_word(db, "beslutning", "decision", note_id=1000, card_id=10000)
        repair_id = _add_repair_word(db, "den gangen", "that time", note_id=1001, card_id=10001)
        # Distinct bytes per word: identical bytes hash to one filename and the
        # digest guard drops the second, which would mask the budget question.
        media_fn = _MediaFn(_Media(image_bytes=b"MINT"), _Media(image_bytes=b"REPAIR"))

        report = await prestage_production_images(db, media_fn, language_code=LANG, limit=1)

        assert report.fetched == 1, "the mint queue's budget is spent in full"
        assert report.repaired == 1, "and the repair still ran"
        assert db.get_image_filename(repair_id) is not None

    async def test_the_repair_queue_is_bounded_per_pass(self, db) -> None:
        for i in range(IMAGE_REPAIR_LIMIT + 2):
            _add_repair_word(db, f"ord{i}", f"word {i}", note_id=1000 + i, card_id=10000 + i * 10)
        media_fn = _MediaFn(*(_Media(image_bytes=f"PIC{i}".encode()) for i in range(IMAGE_REPAIR_LIMIT + 2)))

        report = await prestage_production_images(db, media_fn, language_code=LANG, limit=0)

        assert report.repaired == IMAGE_REPAIR_LIMIT
        assert len(media_fn.calls) == IMAGE_REPAIR_LIMIT

    async def test_a_number_word_is_drawn_and_still_flagged_dirty(self, db) -> None:
        """Drawing costs no network call, but the Anki card still needs telling."""
        coll_id = _add_repair_word(db, "fem", "five", note_id=1000, card_id=10000)
        media_fn = _MediaFn()

        report = await prestage_production_images(db, media_fn, language_code=LANG, limit=10)

        assert media_fn.calls == [], "a number is drawn, never searched for"
        assert report.rendered_number == 1
        assert db.get_image_filename(coll_id).endswith(".svg")
        _rid, item, _lang = db.get_collocation_by_id(coll_id)
        assert "image" in db.get_dirty_fields(item.guid)

    async def test_the_summary_line_reports_the_repair_queue(self, db, caplog) -> None:
        """The property that was actually missing: this population was invisible.

        A standing ``repair_queue>0`` with ``repaired=0`` is the signal to look.
        """
        _add_repair_word(db, "den gangen", "that time", note_id=1000, card_id=10000)

        with caplog.at_level("WARNING"):
            await prestage_production_images(db, _MediaFn(), language_code=LANG, limit=10)

        assert "repair_queue=1" in caplog.text
        assert "repaired=1" in caplog.text


class TestOnePictureNeverServesTwoWords:
    """The guard that existed only within one pass (2026-09-06).

    ``used_image_urls`` and ``seen_digests`` both live for the duration of one
    run, so two words fetched in DIFFERENT passes were each handed the same top
    hit and nothing noticed. Identical bytes hash to one filename, so the second
    store overwrote nothing — it simply pointed two cards at one file.

    Measured on the Norwegian deck: 16 image files each shown on two or more
    different words, every one with a live production card. `bitte`/`veldig`/
    `meget` shared one "very" picture; `vite`/`kjenne` one "know"; `mene`/`tenke`
    one "think". 349 glosses are used by more than one card.

    ⚠️ The defect is in the CARD, not the file. A production front shows the
    picture and asks for the word, so when two words share it the prompt admits
    more than one right answer — the same shape as the underdetermined cloze of
    tunatale-keb0, arriving by a different route.
    """

    async def test_bytes_another_card_already_holds_are_not_stored_again(self, db) -> None:
        first = _add_word(db, "vite", "know", note_id=1000, card_id=10000)
        await prestage_production_images(
            db, _MediaFn(_Media(image_bytes=b"KNOW", image_url="u1")), language_code=LANG, limit=10
        )
        assert db.get_image_filename(first) is not None

        # A later pass, a different word, the same picture back from Pixabay.
        second = _add_word(db, "kjenne", "know", note_id=1001, card_id=10001)
        report = await prestage_production_images(
            db, _MediaFn(_Media(image_bytes=b"KNOW", image_url="u1")), language_code=LANG, limit=10
        )

        assert report.duplicate_image == 1
        assert report.fetched == 0
        assert db.get_image_filename(second) is None, "two words must not share one picture"

    async def test_the_retry_bars_the_url_that_collided_so_refusing_converges(self, db) -> None:
        """Without this the refusal is a permanent no-op.

        ``used_image_urls`` starts empty every pass, so Pixabay returns the same
        top hit and the word is refused forever — quieter than the bug it
        replaced, and just as permanent. The URL that produced the collision is
        added to the set, so the retry is handed the next candidate.
        """
        _add_word(db, "vite", "know", note_id=1000, card_id=10000)
        await prestage_production_images(
            db, _MediaFn(_Media(image_bytes=b"KNOW", image_url="u1")), language_code=LANG, limit=10
        )

        second = _add_word(db, "kjenne", "know", note_id=1001, card_id=10001)
        seen_url_sets: list[set] = []

        class _SecondChoice(_MediaFn):
            """Returns the taken picture until its URL is barred, then another."""

            async def __call__(self, word, english, **kwargs):
                used = kwargs.get("used_image_urls") or set()
                seen_url_sets.append(set(used))
                if "u1" in used:
                    return _Media(image_bytes=b"KNOW-2", image_url="u2")
                return _Media(image_bytes=b"KNOW", image_url="u1")

        report = await prestage_production_images(db, _SecondChoice(), language_code=LANG, limit=10)

        assert report.duplicate_image == 1
        assert report.recovered_duplicate == 1
        assert db.get_image_filename(second) is not None
        assert seen_url_sets[0] == set(), "the first attempt asks freely"
        assert "u1" in seen_url_sets[1], "the retry must bar the URL that collided"

    async def test_a_word_still_duplicate_after_its_retry_is_left_imageless(self, db) -> None:
        """One attempt, not a loop: a query whose results are exhausted would
        otherwise spend the whole free-tier budget on one word. The gap between
        `duplicate` and `recovered` in the summary is how that shows up."""
        _add_word(db, "vite", "know", note_id=1000, card_id=10000)
        await prestage_production_images(
            db, _MediaFn(_Media(image_bytes=b"KNOW", image_url="u1")), language_code=LANG, limit=10
        )

        second = _add_word(db, "kjenne", "know", note_id=1001, card_id=10001)
        stubborn = _MediaFn(_Media(image_bytes=b"KNOW", image_url="u1"))

        report = await prestage_production_images(db, stubborn, language_code=LANG, limit=10)

        assert (report.duplicate_image, report.recovered_duplicate) == (1, 0)
        assert db.get_image_filename(second) is None
        assert len(stubborn.calls) == 2, "one fetch and one retry — never a loop"

    async def test_a_card_re_storing_its_own_picture_is_not_a_collision(self, db) -> None:
        """The lookup excludes the card being written, or a refresh refuses itself."""
        coll_id = _add_word(db, "vite", "know", note_id=1000, card_id=10000)
        await prestage_production_images(
            db, _MediaFn(_Media(image_bytes=b"KNOW", image_url="u1")), language_code=LANG, limit=10
        )
        stored = db.get_image_filename(coll_id)

        # Same card, same bytes: `already_had_image` short-circuits before the
        # digest check, so this asserts the row is intact rather than replaced.
        report = await prestage_production_images(
            db, _MediaFn(_Media(image_bytes=b"KNOW", image_url="u1")), language_code=LANG, limit=10
        )

        assert report.duplicate_image == 0
        assert db.get_image_filename(coll_id) == stored

    async def test_a_repaired_card_is_also_held_to_uniqueness(self, db) -> None:
        """The repair queue writes through the same store, so it inherits the guard."""
        _add_word(db, "vite", "know", note_id=1000, card_id=10000)
        await prestage_production_images(
            db, _MediaFn(_Media(image_bytes=b"KNOW", image_url="u1")), language_code=LANG, limit=10
        )

        repair = _add_repair_word(db, "kjenne", "know", note_id=1001, card_id=10001)
        report = await prestage_production_images(
            db, _MediaFn(_Media(image_bytes=b"KNOW", image_url="u1")), language_code=LANG, limit=10
        )

        assert report.repaired == 0
        assert report.duplicate_image == 1
        assert db.get_image_filename(repair) is None

    async def test_the_summary_line_reports_duplicates(self, db, caplog) -> None:
        _add_word(db, "vite", "know", note_id=1000, card_id=10000)
        await prestage_production_images(
            db, _MediaFn(_Media(image_bytes=b"KNOW", image_url="u1")), language_code=LANG, limit=10
        )
        _add_word(db, "kjenne", "know", note_id=1001, card_id=10001)

        with caplog.at_level("WARNING"):
            await prestage_production_images(
                db, _MediaFn(_Media(image_bytes=b"KNOW", image_url="u1")), language_code=LANG, limit=10
            )

        assert "duplicate=1 recovered=0" in caplog.text
        assert "already holds those bytes" in caplog.text

    async def test_a_retry_that_raises_is_counted_not_fatal(self, db) -> None:
        """One word's retry must not end the pass, for the reason `return_exceptions`
        exists in pass 2: the caller schedules this as a background task, where an
        escaped exception is swallowed and the whole batch is abandoned invisibly."""
        _add_word(db, "vite", "know", note_id=1000, card_id=10000)
        await prestage_production_images(
            db, _MediaFn(_Media(image_bytes=b"KNOW", image_url="u1")), language_code=LANG, limit=10
        )
        _add_word(db, "kjenne", "know", note_id=1001, card_id=10001)

        class _FailsOnRetry(_MediaFn):
            async def __call__(self, word, english, **kwargs):
                if (kwargs.get("used_image_urls") or set()) & {"u1"}:
                    raise RuntimeError("pixabay timeout on retry")
                return _Media(image_bytes=b"KNOW", image_url="u1")

        report = await prestage_production_images(db, _FailsOnRetry(), language_code=LANG, limit=10)

        assert report.duplicate_image == 1
        assert report.recovered_duplicate == 0
        assert report.failed == 1
        assert any("pixabay timeout" in f for f in report.failures), "the reason must reach the durable line"

    async def test_a_retry_that_finds_nothing_stores_nothing(self, db) -> None:
        _add_word(db, "vite", "know", note_id=1000, card_id=10000)
        await prestage_production_images(
            db, _MediaFn(_Media(image_bytes=b"KNOW", image_url="u1")), language_code=LANG, limit=10
        )
        second = _add_word(db, "kjenne", "know", note_id=1001, card_id=10001)

        class _EmptyOnRetry(_MediaFn):
            async def __call__(self, word, english, **kwargs):
                if (kwargs.get("used_image_urls") or set()) & {"u1"}:
                    return _Media(image_bytes=None, image_status="no_results")
                return _Media(image_bytes=b"KNOW", image_url="u1")

        report = await prestage_production_images(db, _EmptyOnRetry(), language_code=LANG, limit=10)

        assert report.recovered_duplicate == 0
        assert db.get_image_filename(second) is None

    async def test_one_systematic_retry_fault_is_reported_once(self, db) -> None:
        """The de-duplication `MAX_FAILURE_REASONS` documents, on the retry path.

        A systematic fault produces one message per word; `failed=` already
        carries the multiplicity, and the reason list is there for the KIND. An
        unbounded tail here fills the one file the operator greps.
        """
        for i, (word, gloss) in enumerate((("vite", "know"), ("mene", "think"))):
            _add_word(db, word, gloss, note_id=1000 + i, card_id=10000 + i * 10)
            await prestage_production_images(
                db, _MediaFn(_Media(image_bytes=f"PIC{i}".encode(), image_url=f"u{i}")), language_code=LANG, limit=10
            )
        _add_word(db, "kjenne", "know", note_id=2000, card_id=20000)
        _add_word(db, "tenke", "think", note_id=2001, card_id=20010)

        class _AlwaysFailsOnRetry(_MediaFn):
            """Both words collide, both retries hit the same outage."""

            async def __call__(self, word, english, **kwargs):
                used = kwargs.get("used_image_urls") or set()
                if used & {"u0", "u1"}:
                    raise RuntimeError("pixabay unreachable")
                return _Media(image_bytes=b"PIC0" if english == "know" else b"PIC1", image_url="u0")

        report = await prestage_production_images(db, _AlwaysFailsOnRetry(), language_code=LANG, limit=10)

        assert report.failed == 2, "both retries failed"
        assert len(report.failures) == 1, "one KIND of failure, reported once"
