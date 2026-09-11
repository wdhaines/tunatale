"""Listen-preview rows carry both sides of a word, like the reader does.

bd tunatale-yh47.6. The reader's word payload already splits mastery into an
understand band and a produce band (``transcript.py`` → ``WordToken``); the
preview only ever sent the blended ``progress`` float, so its rows could not
show both sides. These tests pin the four fields each row now carries:
``understand_band`` / ``produce_band`` from ``mastery.py::direction_band`` over
the row's own card, and ``*_stability`` only on a real strength band.

A create row has no card at all, so both of its sides are "none" — the same
"no card" state the reader draws as a dashed rail.

Norwegian with a stubbed lemmatizer, per ``.claude/rules/testing.md``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.lesson import KeyPhraseInfo, Lesson, Phrase, Section, SectionType
from app.models.srs_item import Direction, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.lemmatizer import TokenAnalysis
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401
from tests._helpers.lemmatizer import StubLemmatizer

pytestmark = pytest.mark.anyio

PREVIEW_URL = "/api/srs/content/lesson-no/listen-preview"
SENTENCE = "Hund katt fisk hage"


def _noun(surface: str) -> TokenAnalysis:
    return TokenAnalysis(
        surface=surface, lemma=surface.lower(), upos="NOUN", case="", number="Sing", person="", gender="Masc"
    )


def _setup(monkeypatch, language_no):
    import app.api.srs as srs_mod
    from app.srs.database import SRSDatabase
    from app.storage.store import ContentStore

    stub = StubLemmatizer()
    stub.set_sentence(SENTENCE, [_noun(w) for w in SENTENCE.split()])
    monkeypatch.setattr(srs_mod, "get_lemmatizer", lambda code: stub)

    lesson = Lesson(
        title="Day 1",
        language_code="no",
        sections=[
            Section(
                section_type=SectionType.NATURAL_SPEED,
                phrases=[Phrase(text=SENTENCE, voice_id="female-1", language_code="no", role="female-1")],
            )
        ],
        key_phrases=[KeyPhraseInfo(phrase="god dag", translation="good day")],
    )
    db = SRSDatabase(":memory:")
    store = ContentStore(":memory:")
    store.save_lesson("lesson-no", "curriculum-no", 1, lesson)
    app.state.srs_db = db
    app.state.content_store = store
    app.state.language = language_no
    return db


def _add(db, text: str, word_count: int = 1) -> None:
    db.add_collocation(
        SyntacticUnit(text=text, translation=f"t-{text}", word_count=word_count, difficulty=1, source="test"),
        language_code="no",
    )


def _set(db, text: str, direction: Direction, stability: float) -> None:
    """A graduated direction, not due for two days (so the row is 'ahead')."""
    from app.srs.anki_mirror.rollover import anki_today, due_at_rollover_utc

    item = db.get_collocation(text)
    ds = item.directions[direction]
    ds.state = SRSState.REVIEW
    ds.stability = stability
    ds.reps = 4
    ds.last_review = datetime.now(UTC) - timedelta(days=3)
    ds.due_at = due_at_rollover_utc(anki_today() + timedelta(days=2))
    db.update_direction(item.guid, direction, ds)


def _drop_production(db, text: str) -> None:
    with db._get_conn() as conn:
        conn.execute(
            "DELETE FROM collocation_directions WHERE direction = 'production'"
            " AND collocation_id = (SELECT id FROM collocations WHERE text = ?)",
            (text,),
        )
        conn.commit()


async def _rows() -> dict[str, dict]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(PREVIEW_URL)
    assert resp.status_code == 200
    return {c["text"]: c for c in resp.json()["candidates"]}


def _sides(row: dict) -> tuple:
    return (row["understand_band"], row["understand_stability"], row["produce_band"], row["produce_stability"])


class TestPreviewRowsCarryBothSides:
    async def test_every_row_kind_carries_both_sides(self, monkeypatch, language_no):
        """One lesson, one row of every kind the preview emits, each pinned."""
        db = _setup(monkeypatch, language_no)
        # hund — both sides graduated: months over days.
        _add(db, "hund")
        _set(db, "hund", Direction.RECOGNITION, 45.0)
        _set(db, "hund", Direction.PRODUCTION, 5.0)
        # katt — the Norwegian import shape: recognition only, well known.
        _add(db, "katt")
        _set(db, "katt", Direction.RECOGNITION, 200.0)
        _drop_production(db, "katt")
        # fisk — a card that has never been studied on either side.
        _add(db, "fisk")
        # hage — untracked, so it is a create row.
        # god dag — a key phrase: weeks over a never-studied production.
        _add(db, "god dag", word_count=2)
        _set(db, "god dag", Direction.RECOGNITION, 10.0)

        rows = await _rows()

        assert rows["hund"]["kind"] == "word"
        assert _sides(rows["hund"]) == ("months", 45.0, "days", 5.0)
        assert rows["katt"]["deferred_reason"] == "known"
        assert _sides(rows["katt"]) == ("solid", 200.0, "none", None)
        assert rows["fisk"]["grade_class"] == "new"
        assert _sides(rows["fisk"]) == ("new", None, "new", None)
        assert rows["hage"]["kind"] == "create"
        assert _sides(rows["hage"]) == ("none", None, "none", None)
        assert rows["god dag"]["kind"] == "kp"
        assert _sides(rows["god dag"]) == ("weeks", 10.0, "new", None)
