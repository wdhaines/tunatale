"""A drill card is minted at the FRONT of the new queue (tunatale-5i1p.8).

Brief: ``.beads-tasks/briefs/brief-5i1p8-drill-card-front-placement-2026-11.md``
(oracle P1-P6). A drill row has ``source = course``, so before this change its
note was a course note: tagged ``tt-course`` and placed after the course's newest
new card. Measured live 2026-11-14: position -1100239, 1,064 new cards ahead of
it. Owner: "drill cards will need to be near the front by some mechanism"; every
drill card goes to the front, the question-and-answer step cards included (A).

The mint position is the whole mechanism: TT's ``get_new_items`` follows the
mirrored gather direction and takes ``anki_due`` at the next pull.
"""

from __future__ import annotations

import pytest

from app.models.srs_item import Direction
from app.plugins.anki_sync.sync import AnkiSync, OfflineReader, OfflineWriter
from app.plugins.anki_sync.sync_writer import COURSE_REGION_GAP, COURSE_TAG, MINED_TAG_PREFIX
from app.srs.database import SRSDatabase
from app.srs.lesson_card_mint import mint_lesson_cards
from tests._helpers.anki_sync_create_new import FakeReader
from tests._helpers.anki_sync_pull import FakeReader as RecordsReader
from tests.test_anki_sentence_note_sync import _conn
from tests.test_drill_card import DRILLS
from tests.test_lesson_drill_cards import EXPECTED_KEYS, _cards

LANG = "no"
DECK = "0. Slovene"  # the helper collection's deck

#: The live shape: a course region far below, frequency cards above it.
COURSE_DUES = [-1100344, -1100300, -1100240]
FREQUENCY_DUES = [1, 500, 1002369]


def _seed(anki, nid: int, due: int, tags: str = "") -> None:
    anki.execute(
        "INSERT INTO notes (id, guid, mid, mod, usn, tags, flds, sfld, csum, flags, data) "
        "VALUES (?, ?, 1000002, 0, 0, ?, 'x', 'x', 0, 0, '')",
        (nid, f"seed{nid}", tags),
    )
    anki.execute(
        "INSERT INTO cards (id, nid, did, ord, mod, usn, type, queue, due, ivl, factor, reps, lapses, left, "
        "odue, odid, flags, data) VALUES (?, ?, 12345, 0, 0, 0, 0, 0, ?, 0, 0, 0, 0, 0, 0, 0, 0, '')",
        (nid * 10, nid, due),
    )
    anki.commit()


def _deck():
    anki = _conn()
    for i, due in enumerate(COURSE_DUES):
        _seed(anki, 100 + i, due, f" {COURSE_TAG} ")
    for i, due in enumerate(FREQUENCY_DUES):
        _seed(anki, 200 + i, due)
    return anki


def _mint_rows(db: SRSDatabase) -> dict[str, int]:
    return {card.unit.disambig_key: cid for cid, card in mint_lesson_cards(db, LANG, _cards(), drills=DRILLS)}


async def _create(db: SRSDatabase, anki, *, descending: bool) -> None:
    writer = OfflineWriter(anki, gather_descending=descending)
    await AnkiSync(db=db, _reader=FakeReader(), _writer=writer, language_code=LANG).sync_create_new(
        deck_name=DECK, model_name="Slovene Vocabulary"
    )


def _note_id(db: SRSDatabase, cid: int) -> int:
    _, item, _ = db.get_collocation_by_id(cid)
    return item.anki_note_id


def _due(anki, nid: int) -> int:
    return anki.execute("SELECT due FROM cards WHERE nid = ?", (nid,)).fetchone()[0]


def _tags(anki, nid: int) -> list[str]:
    return anki.execute("SELECT tags FROM notes WHERE id = ?", (nid,)).fetchone()[0].split()


def _new_dues(anki, exclude: set[int]) -> list[int]:
    rows = anki.execute("SELECT nid, due FROM cards WHERE type = 0").fetchall()
    return [due for nid, due in rows if nid not in exclude]


def _served(anki, nids: dict[str, int], *, descending: bool) -> list[str]:
    """The drill keys in Anki's new order: by position, in the gather direction."""
    return sorted(nids, key=lambda k: _due(anki, nids[k]), reverse=descending)


@pytest.fixture(autouse=True)
def _no_tts(monkeypatch):
    import app.audio.cloze_tts as cloze_tts_mod

    async def _down(text, voice=None):
        raise RuntimeError("TTS down")

    monkeypatch.setattr(cloze_tts_mod, "generate_tts_audio", _down)


class TestFrontPlacement:
    @pytest.mark.parametrize("descending", [False, True], ids=["deck-gather", "highest-position"])
    async def test_p1_every_drill_card_goes_ahead_of_every_other_new_card(self, descending):
        db = SRSDatabase(":memory:")
        rows = _mint_rows(db)
        anki = _deck()

        await _create(db, anki, descending=descending)

        nids = {k: _note_id(db, cid) for k, cid in rows.items()}
        others = _new_dues(anki, set(nids.values()))
        for key, nid in nids.items():
            if descending:
                assert _due(anki, nid) > max(others), key
            else:
                assert _due(anki, nid) < min(others), key

    async def test_p2_a_drill_note_is_not_a_course_note(self):
        db = SRSDatabase(":memory:")
        rows = _mint_rows(db)
        anki = _deck()

        await _create(db, anki, descending=False)

        for key, cid in rows.items():
            tags = _tags(anki, _note_id(db, cid))
            assert sorted(tags) == ["drill", "sentence", "tunatale"], key
            assert not any(t.startswith(MINED_TAG_PREFIX) for t in tags), key

    @pytest.mark.parametrize("descending", [False, True], ids=["deck-gather", "highest-position"])
    async def test_p4_one_lessons_drill_cards_serve_in_card_list_order(self, descending):
        db = SRSDatabase(":memory:")
        rows = _mint_rows(db)
        anki = _deck()

        await _create(db, anki, descending=descending)

        nids = {k: _note_id(db, cid) for k, cid in rows.items()}
        assert _served(anki, nids, descending=descending) == EXPECTED_KEYS

    @pytest.mark.parametrize("descending", [False, True], ids=["deck-gather", "highest-position"])
    async def test_p4_tt_serves_the_same_order_after_the_pull(self, descending):
        db = SRSDatabase(":memory:")
        rows = _mint_rows(db)
        anki = _deck()
        await _create(db, anki, descending=descending)
        records = OfflineReader(anki, DECK, language_code=LANG).get_note_records()
        AnkiSync(db=db, _reader=RecordsReader(records), _writer=OfflineWriter(anki), language_code=LANG).sync_pull()

        served = db.get_new_items(limit=50, direction=Direction.PRODUCTION, gather_descending=descending)

        by_id = {cid: key for key, cid in rows.items()}
        assert [by_id[cid] for cid, _, _ in served if cid in by_id] == EXPECTED_KEYS


class TestCourseUnmoved:
    async def test_p3_the_next_course_card_goes_where_it_would_have_gone(self):
        control = _deck()
        expected = _due(
            control, OfflineWriter(control).create_cloze_note(DECK, "{{c1::En}}", language_code=LANG, course=True)
        )

        db = SRSDatabase(":memory:")
        _mint_rows(db)
        anki = _deck()
        await _create(db, anki, descending=False)
        course = OfflineWriter(anki).create_cloze_note(DECK, "{{c1::En}}", language_code=LANG, course=True)

        assert _due(anki, course) == expected

    async def test_p3_with_the_course_learned_the_next_course_card_opens_a_region_not_beside_a_drill_card(self):
        db = SRSDatabase(":memory:")
        rows = _mint_rows(db)
        anki = _deck()
        await _create(db, anki, descending=False)
        anki.execute(
            f"UPDATE cards SET type = 2, queue = 2 WHERE nid IN (SELECT id FROM notes WHERE tags LIKE '% {COURSE_TAG} %')"
        )
        anki.commit()
        drill_dues = [_due(anki, _note_id(db, cid)) for cid in rows.values()]

        course = OfflineWriter(anki).create_cloze_note(DECK, "{{c1::To}}", language_code=LANG, course=True)

        assert _due(anki, course) == min(_new_dues(anki, {course})) - COURSE_REGION_GAP
        assert _due(anki, course) not in {d + 1 for d in drill_dues}


class TestRotationDoesNotMove:
    async def test_p6_a_rotation_rewrite_keeps_the_cards_position_and_tags(self):
        db = SRSDatabase(":memory:")
        rows = _mint_rows(db)
        anki = _deck()
        await _create(db, anki, descending=False)
        cid = rows["sent:test-01:drill-1"]
        nid = _note_id(db, cid)
        due, tags = _due(anki, nid), _tags(anki, nid)
        from app.models.srs_item import RevlogRow

        db.append_revlog(  # an answer that landed it in review: the next step (5i1p.10)
            RevlogRow(
                id=1_700_000_000_000,
                collocation_id=cid,
                direction=Direction.PRODUCTION,
                button_chosen=4,
                interval=4,
                last_interval=0,
                factor=5000,
                taken_millis=1000,
                review_kind=0,
                anki_card_id=None,
            )
        )

        n = AnkiSync(db=db, _reader=FakeReader(), _writer=OfflineWriter(anki), language_code=LANG).rotate_drill_notes()

        assert n >= 1
        assert _due(anki, nid) == due
        assert _tags(anki, nid) == tags
        assert COURSE_TAG not in _tags(anki, nid)
