"""Card audio rendered when a card is about to be heard, not when it is minted.

Why this exists (the user's call, 2026-10-06): a sync can mint hundreds of
cards at once — a 462-word base list did — and at ~10 new cards a day most of
them are not seen for weeks. Rendering a METERED voice for each at mint spends
money on audio nobody hears. So the mint keeps only what is free (a Forvo
recording), and this pass renders TTS for the cards a learner has already met
and the next few in new-card order.

What must hold:

- it only ever touches a vocab card TunaTale originated that has NO word audio;
- it spends at most ``limit`` fetches a pass, cards already seen first;
- what it stores reaches Anki: the row is flagged ``audio`` for ``sync_push``;
- it never opens the Anki collection (it has no handle to one).
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from hashlib import sha256

import pytest

from app.cards.media.audio_prestage import AUDIO_LOOKAHEAD, mint_audio_mode, prestage_card_audio
from app.cards.media.pipeline import MediaResult
from app.models.srs_item import Direction, DirectionState, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.anki_mirror.rollover import anki_today, due_at_rollover_utc
from app.srs.database import SRSDatabase

LANG = "no"


@pytest.fixture
def db():
    database = SRSDatabase(":memory:")
    yield database
    database.close()


@pytest.fixture(autouse=True)
def _media_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("app.cards.media.vocab_media._MEDIA_DIR", tmp_path / "media")
    monkeypatch.setattr("app.config.settings.sync_log", str(tmp_path / "sync.log"))
    return tmp_path / "media"


class _Fetch:
    """Stand-in for ``fetch_card_media``: records each call, replays results."""

    def __init__(self, *results):
        self.calls: list[tuple[str, dict]] = []
        self._results = list(results)

    async def __call__(self, word, english, **kwargs):
        self.calls.append((word, kwargs))
        result = (
            self._results.pop(0)
            if self._results
            else MediaResult(audio_bytes=b"MP3:" + word.encode(), audio_source="tts")
        )
        if isinstance(result, BaseException):
            raise result
        return result

    @property
    def words(self) -> list[str]:
        return [word for word, _ in self.calls]


def _unit(word, english="gloss", *, source="base-list", card_type="vocab"):
    return SyntacticUnit(
        text=word,
        translation=english,
        word_count=1,
        difficulty=1,
        source=source,
        card_type=card_type,
        source_sentence="En setning." if card_type == "cloze" else "",
    )


def _add_new(db, word, **kwargs) -> int:
    db.add_collocation(_unit(word, **kwargs), language_code=LANG)
    return db.get_collocation_id_by_guid(db.get_collocation(word).guid)


def _add_seen(db, word, *, state=SRSState.REVIEW, note_id=None, **kwargs) -> int:
    db.upsert_by_guid(
        _unit(word, **kwargs),
        LANG,
        {
            Direction.RECOGNITION: DirectionState(
                direction=Direction.RECOGNITION,
                due_at=due_at_rollover_utc(anki_today()),
                state=state,
                reps=3,
                last_review=datetime.fromisoformat("2026-10-01T12:00:00+00:00"),
            )
        },
        anki_note_id=note_id,
    )
    return db.get_collocation_id_by_guid(db.get_collocation(word).guid)


def _give_audio(db, coll_id, name="tts_har.mp3"):
    db.add_media(coll_id, "audio_tts", name, f"media/{name}", name, "abc", 3)


async def _run(db, fetch, **kwargs):
    kwargs.setdefault("limit", 10)
    return await prestage_card_audio(db, language_code=LANG, fetch_fn=fetch, **kwargs)


class TestWhatItRenders:
    async def test_a_seen_card_with_no_audio_gets_audio(self, db, _media_dir):
        coll_id = _add_seen(db, "natt")
        fetch = _Fetch(MediaResult(audio_bytes=b"NATT-MP3", audio_source="tts"))

        report = await _run(db, fetch)

        filename = db.get_audio_filename(coll_id)
        assert filename == f"tts_natt_{sha256(b'NATT-MP3').hexdigest()[:8]}.mp3"
        assert (_media_dir / filename).read_bytes() == b"NATT-MP3"
        assert db.has_media_row(coll_id, "audio_tts")
        assert (report.stored, report.tts, report.forvo) == (1, 1, 0)

    async def test_what_it_stores_is_flagged_for_the_push_to_anki(self, db):
        coll_id = _add_seen(db, "natt", note_id=5001)
        await _run(db, _Fetch())
        guid = db.get_collocation("natt").guid
        assert db.get_dirty_fields(guid) == "audio"
        assert coll_id is not None

    async def test_a_forvo_recording_is_stored_as_forvo_under_the_language_prefix(self, db):
        coll_id = _add_seen(db, "natt")
        report = await _run(db, _Fetch(MediaResult(audio_bytes=b"HUMAN", audio_source="forvo")))

        assert db.get_audio_filename(coll_id) == f"no_natt_{sha256(b'HUMAN').hexdigest()[:8]}.mp3"
        assert db.has_media_row(coll_id, "audio_forvo")
        assert (report.forvo, report.tts) == (1, 0)

    async def test_it_asks_for_audio_alone_in_the_cards_language(self, db):
        _add_seen(db, "natt", english="night")
        fetch = _Fetch()
        await _run(db, fetch)

        word, kwargs = fetch.calls[0]
        assert word == "natt"
        assert kwargs["language_code"] == LANG
        assert kwargs["audio"] == "full"
        assert kwargs["image_query"] == "", "the picture is not this pass's business"

    async def test_an_upcoming_new_card_is_rendered_before_it_is_met(self, db):
        coll_id = _add_new(db, "morgen")
        await _run(db, _Fetch())
        assert db.get_audio_filename(coll_id) is not None

    async def test_a_new_card_beyond_the_lookahead_is_left_alone(self, db):
        """The point of the whole module: the 400th new card is weeks away."""
        ids = [_add_new(db, f"ord{i:03d}") for i in range(AUDIO_LOOKAHEAD + 2)]
        fetch = _Fetch()

        await _run(db, fetch, limit=AUDIO_LOOKAHEAD + 10)

        assert len(fetch.calls) == AUDIO_LOOKAHEAD
        upcoming = {cid for cid, _item, _lang in db.get_new_items(limit=AUDIO_LOOKAHEAD)}
        assert {cid for cid in ids if db.get_audio_filename(cid) is not None} == upcoming


class TestWhatItLeavesAlone:
    async def test_a_card_that_has_audio_costs_no_fetch(self, db):
        _give_audio(db, _add_seen(db, "natt"))
        _give_audio(db, _add_new(db, "morgen"), name="tts_morgen.mp3")
        fetch = _Fetch()

        report = await _run(db, fetch)

        assert fetch.calls == []
        assert report.stored == 0

    async def test_an_imported_card_is_never_given_audio(self, db):
        """An Anki-imported note owns its own Audio field; 35 Slovene and one
        Norwegian imported card have none, and that is the deck's business."""
        seen = _add_seen(db, "natt", source="anki")
        new = _add_new(db, "morgen", source="anki")
        fetch = _Fetch()

        await _run(db, fetch)

        assert fetch.calls == []
        assert db.get_audio_filename(seen) is None
        assert db.get_audio_filename(new) is None

    async def test_a_cloze_is_not_a_word_audio_card(self, db):
        db.add_collocation(_unit("fordi", card_type="cloze"), language_code=LANG)
        fetch = _Fetch()
        await _run(db, fetch)
        assert fetch.calls == []

    @pytest.mark.parametrize("state", [SRSState.SUSPENDED, SRSState.KNOWN])
    async def test_a_card_out_of_rotation_is_not_rendered(self, db, state):
        _add_seen(db, "natt", state=state)
        fetch = _Fetch()
        await _run(db, fetch)
        assert fetch.calls == []


class TestBudget:
    async def test_the_limit_bounds_the_fetches_and_seen_cards_come_first(self, db):
        _add_new(db, "morgen")
        _add_new(db, "kveld")
        _add_seen(db, "natt")
        _add_seen(db, "dag")
        fetch = _Fetch()

        report = await _run(db, fetch, limit=3)

        assert len(fetch.calls) == 3
        assert set(fetch.words[:2]) == {"natt", "dag"}, "a card already met outranks one that is only coming"
        assert (report.seen_queue, report.upcoming_queue) == (2, 2)

    async def test_zero_is_the_off_switch(self, db):
        _add_seen(db, "natt")
        fetch = _Fetch()
        report = await _run(db, fetch, limit=0)
        assert fetch.calls == []
        assert report.stored == 0

    async def test_a_pass_already_running_for_this_db_is_not_doubled(self, db):
        """Two triggers (a sync and a deck open) can fire together; rendering the
        same card twice would be a second metered request for nothing."""
        coll_id = _add_seen(db, "natt")
        started = asyncio.Event()
        release = asyncio.Event()
        calls: list[str] = []

        async def slow_fetch(word, english, **kwargs):
            calls.append(word)
            started.set()
            await release.wait()
            return MediaResult(audio_bytes=b"ONCE", audio_source="tts")

        first = asyncio.create_task(_run(db, slow_fetch))
        await started.wait()
        # Its own fetch, which returns at once: without the guard this pass
        # renders the card a second time and the test FAILS — sharing the slow
        # fetch made a missing guard hang the suite instead.
        overlapping = _Fetch()
        second = await _run(db, overlapping)
        release.set()
        await first

        assert overlapping.calls == []
        assert calls == ["natt"]
        assert second.skipped_busy is True
        assert db.get_audio_filename(coll_id) is not None
        # …and the guard is released: a later pass is not locked out for good.
        assert (await _run(db, _Fetch())).skipped_busy is False


class TestFailures:
    async def test_no_audio_back_stores_nothing_and_leaves_the_card_for_next_time(self, db):
        coll_id = _add_seen(db, "natt")
        report = await _run(db, _Fetch(MediaResult(audio_status="no_pronunciation")))

        assert db.get_audio_filename(coll_id) is None
        assert db.get_dirty_fields(db.get_collocation("natt").guid) == ""
        assert (report.stored, report.no_audio) == (0, 1)

    async def test_one_failed_fetch_does_not_cost_the_others(self, db):
        _add_seen(db, "natt")
        other = _add_seen(db, "dag")
        report = await _run(
            db,
            _Fetch(
                RuntimeError("boom https://x.test/tts?key=SECRET"), MediaResult(audio_bytes=b"DAG", audio_source="tts")
            ),
        )

        assert db.get_audio_filename(other) is not None
        assert (report.stored, report.failed) == (1, 1)
        assert report.failures == ("RuntimeError:boom https://x.test/tts?<redacted>",)

    async def test_a_systematic_fault_is_reported_once_with_its_count(self, db):
        """Twenty copies of one message bury the line; `failed=` carries how many."""
        _add_seen(db, "natt")
        _add_seen(db, "dag")
        report = await _run(db, _Fetch(ValueError("bad voice"), ValueError("bad voice")))

        assert report.failed == 2
        assert report.failures == ("ValueError:bad voice",)

    async def test_a_fetch_returning_none_is_tolerated(self, db):
        _add_seen(db, "natt")
        report = await _run(db, _Fetch(None))
        assert (report.stored, report.no_audio) == (0, 1)


class TestSummaryLine:
    async def test_every_pass_leaves_a_line_in_the_sync_log_even_an_empty_one(self, db, tmp_path):
        await _run(db, _Fetch())
        _add_seen(db, "natt")
        await _run(db, _Fetch())

        lines = (tmp_path / "sync.log").read_text().splitlines()
        assert len(lines) == 2
        assert "PRESTAGE_AUDIO stored=0 forvo=0 tts=0 no_audio=0 failed=0 seen_queue=0 upcoming_queue=0" in lines[0]
        assert "PRESTAGE_AUDIO stored=1 forvo=0 tts=1 no_audio=0 failed=0 seen_queue=1 upcoming_queue=0" in lines[1]

    async def test_failures_ride_the_line(self, db, tmp_path):
        _add_seen(db, "natt")
        await _run(db, _Fetch(ValueError("bad voice")))
        assert "failed=1" in (tmp_path / "sync.log").read_text()
        assert "failures=ValueError:bad voice" in (tmp_path / "sync.log").read_text()

    async def test_an_unwritable_log_never_breaks_the_pass(self, db, tmp_path, monkeypatch):
        blocker = tmp_path / "a-file"
        blocker.write_text("x")
        monkeypatch.setattr("app.config.settings.sync_log", str(blocker / "sync.log"))
        coll_id = _add_seen(db, "natt")

        await _run(db, _Fetch())

        assert db.get_audio_filename(coll_id) is not None


class TestMintAudioMode:
    """Which audio a sync mint asks for: everything, or only what is free."""

    def test_a_metered_card_voice_is_deferred_to_this_pass(self):
        assert mint_audio_mode("ceb") == "forvo"

    def test_an_unmetered_card_voice_is_still_rendered_at_mint(self):
        assert mint_audio_mode("no") == "full"

    def test_the_metered_provider_list_is_configuration(self, monkeypatch):
        monkeypatch.setattr("app.config.settings.deferred_card_tts_providers", [])
        assert mint_audio_mode("ceb") == "full"


class TestReachesAnki:
    """The whole path, through the REAL writer: a note minted without audio has
    its ``Audio`` field filled by the sync after the pre-stage rendered it.

    Each half is tested on its own elsewhere; this is the join. The collection
    helper is the Slovene one, so the metered provider is pinned to that
    language's rather than the helper being re-cut for another language.
    """

    async def test_minted_without_audio_then_staged_then_pushed(self, db, tmp_path, monkeypatch):
        from app.plugins.anki_sync.sync import AnkiSync, OfflineWriter
        from tests._helpers.anki_sync_create_new import FakeReader, _make_dual_collection_conn

        monkeypatch.setattr("app.config.settings.deferred_card_tts_providers", ["azure"])
        tt_media = tmp_path / "media"
        monkeypatch.setattr("app.plugins.anki_sync.sync._MEDIA_DIR", tt_media)
        anki_media = tmp_path / "collection.media"
        anki_media.mkdir()
        conn = _make_dual_collection_conn()
        sync = AnkiSync(
            db=db, _reader=FakeReader(), _writer=OfflineWriter(conn, media_dir=anki_media), language_code="sl"
        )
        db.add_collocation(_unit("noč", "night"), language_code="sl")
        asked: list[str] = []

        async def mint_media(word, english, *, used_image_urls, audio="full", **kwargs):
            asked.append(audio)
            return MediaResult(audio_status="no_pronunciation")

        def _audio_field() -> str:
            flds = conn.execute("SELECT flds FROM notes").fetchone()["flds"].split("\x1f")
            return flds[2]  # Slovene, English, Audio, …

        await sync.sync_create_new(deck_name="0. Slovene", model_name="Slovene Vocabulary", _media_fn=mint_media)
        assert asked == ["forvo"]
        assert _audio_field() == ""

        report = await prestage_card_audio(
            db, language_code="sl", limit=5, fetch_fn=_Fetch(MediaResult(audio_bytes=b"NOC", audio_source="tts"))
        )
        assert report.stored == 1
        assert _audio_field() == "", "the pre-stage never writes the collection"

        conn.execute("UPDATE notes SET usn = 5")
        sync.sync_push()

        name = f"tts_noč_{sha256(b'NOC').hexdigest()[:8]}.mp3"
        assert _audio_field() == f"[sound:{name}]"
        assert (anki_media / name).read_bytes() == b"NOC"
        assert conn.execute("SELECT usn FROM notes").fetchone()["usn"] == -1
        assert db.get_dirty_fields(db.get_collocation("noč").guid) == ""


class TestDeckOpenTrigger:
    """Opening a deck tops up word audio — the only trigger a learner without
    Anki has. Sociable: the real endpoint and the real media pipeline, with the
    Forvo HTTP client and ffmpeg (the designated boundaries) standing in.
    """

    @pytest.fixture
    def app_db(self, monkeypatch):
        from app.main import app

        database = SRSDatabase(":memory:")
        app.state.srs_db = database
        yield database
        database.close()
        delattr(app.state, "srs_db")

    @pytest.fixture
    def forvo(self, monkeypatch):
        """A Forvo that has a Norwegian recording of every word; counts page hits."""
        import base64

        import httpx

        hits: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "forvo.com":
                hits.append(request.url.path)
                play = base64.b64encode(b"1/2/natt.mp3").decode()
                return httpx.Response(
                    200, text=f"<div id='language-container-no'><article>Play(1,'{play}'</article></div>"
                )
            return httpx.Response(200, content=b"FORVO-MP3")

        monkeypatch.setattr(
            "app.cards.media.forvo._make_client", lambda: httpx.Client(transport=httpx.MockTransport(handler))
        )
        monkeypatch.setattr("app.cards.media.normalize._measure_loudness", lambda p: {})
        monkeypatch.setattr(
            "app.cards.media.normalize._apply_normalization",
            lambda src, dst, stats, target_lufs: dst.write_bytes(src.read_bytes()),
        )
        return hits

    @staticmethod
    async def _open_deck(**params):
        from httpx import ASGITransport, AsyncClient

        from app.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return await client.get("/api/srs/review-queue", params=params, headers={"X-TT-Language": LANG})

    async def test_a_deck_open_gives_an_upcoming_card_its_audio(self, app_db, forvo, monkeypatch):
        monkeypatch.setattr("app.config.settings.prestage_audio_limit", 5)
        coll_id = _add_new(app_db, "natt")

        response = await self._open_deck(session_start="true")

        assert response.status_code == 200
        assert forvo == ["/word/natt/"]
        filename = app_db.get_audio_filename(coll_id)
        assert filename == f"no_natt_{sha256(b'FORVO-MP3').hexdigest()[:8]}.mp3"
        assert app_db.has_media_row(coll_id, "audio_forvo")

    async def test_the_per_grade_refetch_does_not_run_it(self, app_db, forvo, monkeypatch):
        monkeypatch.setattr("app.config.settings.prestage_audio_limit", 5)
        coll_id = _add_new(app_db, "natt")

        assert (await self._open_deck()).status_code == 200

        assert forvo == []
        assert app_db.get_audio_filename(coll_id) is None

    async def test_the_limit_setting_turns_it_off(self, app_db, forvo):
        """0 is what the test suite itself runs under (conftest)."""
        coll_id = _add_new(app_db, "natt")

        assert (await self._open_deck(session_start="true")).status_code == 200

        assert forvo == []
        assert app_db.get_audio_filename(coll_id) is None
