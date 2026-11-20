"""C3: the affix cards a Grade Save adds equal the listen's, for the same lesson and budget (tunatale-5i1p.2).

The affix pace gate reads the focus cards ``_add_contrast_cards`` mints, so the
Grade page must mint them exactly as the listen does. The expected lists are
the ones ``test_api_grammar_lesson.py::test_the_listen_honours_what_the_preview_rows_were_set_to``
pins for ``POST /listen``; here the same choices are sent the Grade page's way
(``declined`` / ``over_cap`` with ``kind: "contrast"``). A word pattern adds
no pair cards either way (tunatale-ve4p.36).
"""

from __future__ import annotations

import pytest

from tests._helpers.api_app_state import _clean_app_state  # noqa: F401
from tests.test_api_grammar_lesson import (  # noqa: F401
    _GIDALA,
    _GIINOM,
    _GIKAON,
    _TWO_LEFT,
    AFFIX_CARD,
    _client,
    _contrast_rows,
    _drill_lesson,
    _focus_lesson,
    _focus_rows,
    deck,
)


def _keys(kind: str, forms: list[str]) -> list[dict]:
    return [{"kind": kind, "key": f} for f in forms]


@pytest.mark.parametrize(
    ("cap", "body", "added"),
    [
        ("20", {}, [_GIINOM, _GIKAON, _GIDALA]),
        (_TWO_LEFT, {}, [_GIINOM, _GIKAON]),
        ("20", {"declined": _keys("contrast", [_GIKAON])}, [_GIINOM, _GIDALA]),
        # A decline keeps its slot: the card past the budget is not promoted into it.
        (_TWO_LEFT, {"declined": _keys("contrast", [_GIINOM])}, [_GIKAON]),
        (_TWO_LEFT, {"over_cap": _keys("contrast", [_GIDALA])}, [_GIINOM, _GIKAON, _GIDALA]),
        (
            _TWO_LEFT,
            {"declined": _keys("contrast", [_GIDALA, "nope"]), "over_cap": _keys("contrast", [_GIDALA, "nope"])},
            [_GIINOM, _GIKAON],
        ),
    ],
)
async def test_c3_a_save_adds_the_focus_cards_the_listen_would(deck, srs_db, cap, body, added):  # noqa: F811
    store, pipeline, _ = deck
    srs_db.set_anki_state_cache("daily_new_cap", cap)
    pipeline.start()
    async with _client() as client:
        lesson_id = await _focus_lesson(store, srs_db, pipeline)
        preview = (await client.get(f"/api/srs/content/{lesson_id}/listen-preview")).json()
        plan = (await client.get(f"/api/srs/content/{lesson_id}/grade-plan")).json()
        saved = await client.post(f"/api/srs/content/{lesson_id}/grade", json=body)
    await pipeline.shutdown()

    assert saved.status_code == 200, saved.text
    assert plan["suggestions"]["contrast"] == preview["affix_cards"]
    assert [row["text"] for row in _focus_rows(srs_db)] == added
    assert saved.json()["created"] == len(added)


async def test_c3_a_save_of_a_word_pattern_lesson_adds_no_pair_cards(deck, srs_db):  # noqa: F811
    store, pipeline, _ = deck
    pipeline.start()
    async with _client() as client:
        lesson_id = await _drill_lesson(store, srs_db, pipeline)
        plan = (await client.get(f"/api/srs/content/{lesson_id}/grade-plan")).json()
        saved = await client.post(f"/api/srs/content/{lesson_id}/grade", json={})
    await pipeline.shutdown()

    assert saved.status_code == 200, saved.text
    assert plan["suggestions"]["contrast"] == []
    assert _contrast_rows(srs_db) == []
    assert saved.json()["created"] == AFFIX_CARD
