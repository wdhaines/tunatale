"""The affix drill card through the routes (tunatale-5i1p.6): the study mark mints it, the Grade plan lists it.

Both ways of marking a lesson studied (``POST /listen`` and the Grade page's
Save) go through ``_record_study_pass``, which mints the pattern's card once
and schedules its speech after the response. The deck is
``test_api_grammar_lesson.py::deck``: a real pipeline builds the mo- / mi- drill
lesson from the learner's understood roots (``inom, adto, uban`` there, plus
``lakaw`` and ``gawas`` here, so the chain is the owner's).
"""

from __future__ import annotations

import base64
import logging
from datetime import UTC, date, datetime, time

import httpx
import respx

from app.config import settings
from app.main import app
from app.models.srs_item import Direction, DirectionState, SRSState
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401
from tests.test_api_grammar_lesson import _client, _contrast_rows, _drill_lesson, _understand, deck  # noqa: F401
from tests.test_gemini_tts import SYNTHESIS_URL, _svc

KEY = "sent:affix-mo-mi:drill-1"


def _affix_rows(db) -> list[dict]:
    with db._get_conn() as conn:
        rows = conn.execute(
            "SELECT c.id, c.text, c.source, d.state FROM collocations c"
            " JOIN collocation_directions d ON d.collocation_id = c.id WHERE c.disambig_key = ?",
            (KEY,),
        ).fetchall()
    return [dict(row) for row in rows]


def _media_kinds(db, cid: int) -> set[str]:
    with db._get_conn() as conn:
        return {r["kind"] for r in conn.execute("SELECT kind FROM media WHERE collocation_id = ?", (cid,))}


class _DownTTS:
    async def synthesize(self, *args, **kwargs):
        raise RuntimeError("TTS down")


async def test_a_listen_mints_the_card_once_and_counts_it(deck, srs_db, caplog):  # noqa: F811
    store, pipeline, _ = deck
    for root in ("lakaw", "gawas"):
        _understand(srs_db, root)
    app.state.tts = _DownTTS()
    pipeline.start()
    async with _client() as client:
        lesson_id = await _drill_lesson(store, srs_db, pipeline)
        with caplog.at_level(logging.WARNING):
            first = await client.post("/api/srs/listen", json={"content_id": lesson_id})
        again = await client.post("/api/srs/listen", json={"content_id": lesson_id})
    await pipeline.shutdown()

    [row] = _affix_rows(srs_db)
    assert (row["text"], row["source"], row["state"]) == ("Molakaw ko ugma.", "user", "new")
    # Counted with what the listen created: its contrast cards and this card.
    assert first.json()["created"] == len(_contrast_rows(srs_db)) + 1
    assert again.json()["created"] == 0  # the second study mints no second card
    # The speech ran after the response, through the app's TTS service, and failed open.
    assert f"DRILL_CARD_SYNTH_FAILED key={KEY}" in caplog.text
    assert _media_kinds(srs_db, row["id"]) == set()


@respx.mock(assert_all_called=True)
async def test_a_grade_save_mints_it_and_voices_it_through_the_apps_tts(
    deck,  # noqa: F811
    srs_db,
    tmp_path,
    monkeypatch,
    respx_mock,
):
    import app.api.srs as srs_mod

    store, pipeline, _ = deck
    for root in ("lakaw", "gawas"):
        _understand(srs_db, root)
    cache, media = tmp_path / "cache", tmp_path / "media"
    media.mkdir()
    monkeypatch.setattr(settings, "tts_cache_dir", cache)
    monkeypatch.setattr(srs_mod, "_MEDIA_DIR", media)
    app.state.tts = _svc(cache_dir=cache)
    audio = {"audioContent": base64.b64encode(b"ID3-clip").decode()}
    route = respx_mock.post(SYNTHESIS_URL).mock(return_value=httpx.Response(200, json=audio))
    pipeline.start()
    async with _client() as client:
        lesson_id = await _drill_lesson(store, srs_db, pipeline)
        saved = await client.post(f"/api/srs/content/{lesson_id}/grade", json={})
    await pipeline.shutdown()

    assert saved.status_code == 200, saved.text
    [row] = _affix_rows(srs_db)
    assert route.call_count == 13
    assert len(_media_kinds(srs_db, row["id"])) == 13


async def test_the_grade_plan_lists_the_card_in_say_it_once_it_is_in_review(deck, srs_db):  # noqa: F811
    store, pipeline, _ = deck
    for root in ("lakaw", "gawas"):
        _understand(srs_db, root)
    app.state.tts = _DownTTS()
    pipeline.start()
    async with _client() as client:
        lesson_id = await _drill_lesson(store, srs_db, pipeline)
        await client.post("/api/srs/listen", json={"content_id": lesson_id})
        [row] = _affix_rows(srs_db)
        guid = srs_db.get_collocation_by_id(row["id"])[1].guid
        srs_db.update_direction(
            guid,
            Direction.PRODUCTION,
            DirectionState(
                direction=Direction.PRODUCTION,
                due_at=datetime.combine(date.today(), time(4, 0), tzinfo=UTC),
                stability=5.0,
                difficulty=4.0,
                reps=1,
                state=SRSState.REVIEW,
            ),
        )
        plan = (await client.get(f"/api/srs/content/{lesson_id}/grade-plan")).json()
    await pipeline.shutdown()

    [listed] = [c for c in plan["cards"] if c["item_id"] == row["id"]]
    assert (listed["kind"], listed["direction"]) == ("drill", "production")
    assert listed["text"] == "Milakaw ko gahapon."  # step 1: no answer has left it in review yet
    assert listed["drill"]["cue"] == "gahapon"
