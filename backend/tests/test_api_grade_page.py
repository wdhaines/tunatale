"""The Grade page's backend: read a lesson's grade plan, apply an explicit grade set (tunatale-5i1p.2).

Contract: .beads-tasks/briefs/design-grade-page-2026-11.md § Slice 2. The
oracle's case names (G1-G11, S1-S2, C1-C3, X2) are kept on the tests below so
an auditor can walk the table.

The master oracle is differential: a recognition grade sent to ``POST /grade``
must leave the database exactly as today's ``POST /listen`` does with the same
grades confirmed and every other row skipped. Both routes call
``_apply_grade_now``; the twin databases below are seeded identically, one per
route, and compared column by column with only ids and grade instants masked.

Explicit grades only: a row the page did not send is untouched (no revlog row,
no pending row, the direction row byte-identical). That retires the popup's
polarity, where an absent row meant "Good".
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

import app.api.srs as srs_mod
from app.main import app
from app.models.lesson import KeyPhraseInfo, Lesson, Phrase, Section, SectionType
from app.models.srs_item import Direction, DirectionState, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.anki_mirror.queue_stats import build_live_load_balancer
from app.srs.anki_mirror.rollover import anki_today, due_at_rollover_utc, local_today_rollover
from app.srs.database import SRSDatabase
from app.storage.store import ContentStore
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401
from tests._helpers.lemmatizer import StubLemmatizer

PLAN_URL = "/api/srs/content/lesson-1/grade-plan"
GRADE_URL = "/api/srs/content/lesson-1/grade"
LISTEN_URL = "/api/srs/listen"

_SENTENCE = "hus bil katt hund bok"
REC, PROD = Direction.RECOGNITION, Direction.PRODUCTION


@pytest.fixture(autouse=True)
def _stub_lemmatizer(monkeypatch):
    """Every lesson word is its own lemma: no stanza, the same answer on every host."""
    monkeypatch.setattr(srs_mod, "get_lemmatizer", lambda code: StubLemmatizer())


def _lesson(sentence: str = _SENTENCE, *, key_phrases=None, story=None) -> Lesson:
    return Lesson(
        title="Dag 1",
        language_code="no",
        sections=[
            Section(
                section_type=SectionType.NATURAL_SPEED,
                phrases=[Phrase(text=sentence, voice_id="female-1", language_code="no", role="female-1")],
            )
        ],
        key_phrases=key_phrases or [],
        generation_metadata={"story": story} if story is not None else {},
    )


def _install(db: SRSDatabase, lesson: Lesson | None = None) -> SRSDatabase:
    store = ContentStore(":memory:")
    store.save_lesson("lesson-1", "curriculum-1", 1, lesson or _lesson())
    app.state.srs_db = db
    app.state.content_store = store
    return db


def _card(db, text: str) -> int:
    """A vocab card for *text*, both directions NEW."""
    db.add_collocation(
        SyntacticUnit(text=text, translation=f"t-{text}", word_count=1, difficulty=1, source="test", lemma=text),
        language_code="no",
    )
    item = db.get_collocation(text)
    return db.get_collocation_id_by_guid(item.guid)


def _review(db, text: str, direction: Direction = REC, *, due_in: int = 0, graded_today: bool = False) -> int:
    """Put *text*'s *direction* in REVIEW, due *due_in* days from today (negative = overdue).

    ``due_at`` is day-level at 04:00 UTC (``rollover.py::due_at_rollover_utc``),
    the encoding every REVIEW row carries; an instant-flavoured seed misreads
    as "ahead" late in the UTC day.
    """
    item = db.get_collocation(text)
    last = datetime.now(UTC) if graded_today else datetime.now(UTC) - timedelta(days=5)
    db.update_direction(
        item.guid,
        direction,
        DirectionState(
            direction=direction,
            state=SRSState.REVIEW,
            due_at=due_at_rollover_utc(anki_today() + timedelta(days=due_in)),
            last_review=last,
            stability=10.0,
            difficulty=5.0,
            reps=5,
        ),
    )
    return db.get_collocation_id_by_guid(item.guid)


def _set_state(db, text: str, direction: Direction, state: SRSState, *, due_at: datetime | None = None) -> None:
    item = db.get_collocation(text)
    db.update_direction(
        item.guid,
        direction,
        DirectionState(
            direction=direction,
            state=state,
            due_at=due_at or datetime.now(UTC) - timedelta(days=1),
            last_review=datetime.now(UTC) - timedelta(minutes=20),
            stability=1.0,
            difficulty=5.0,
            reps=1,
        ),
    )


async def _get(url: str, params: dict | None = None):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(url, params=params)


async def _post(url: str, body: dict):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post(url, json=body)


async def _plan(params: dict | None = None) -> dict:
    resp = await _get(PLAN_URL, params)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _grade(body: dict) -> dict:
    resp = await _post(GRADE_URL, body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _g(item_id: int, rating: str, direction: Direction = REC) -> dict:
    return {"item_id": item_id, "direction": direction.value, "rating": rating}


def _rows(db, table: str, where: str = "", params: tuple = ()) -> list[dict]:
    with db._get_conn() as conn:
        return [dict(r) for r in conn.execute(f"SELECT * FROM {table} {where}", params).fetchall()]


def _direction_row(db, cid: int, direction: Direction) -> dict:
    return _rows(db, "collocation_directions", "WHERE collocation_id = ? AND direction = ?", (cid, direction.value))[0]


def _revlog(db, cid: int | None = None) -> list[dict]:
    if cid is None:
        return _rows(db, "tt_revlog", "ORDER BY id")
    return _rows(db, "tt_revlog", "WHERE collocation_id = ? ORDER BY id", (cid,))


def _reviews_today(db) -> int:
    return db.count_reviews_completed_today(anki_today())


def _cutoff(db) -> str | None:
    row = db.get_anki_state_cache("learning_cutoff")
    return None if row is None else row[0]


# ── The plan ────────────────────────────────────────────────────────────────


class TestPlanRows:
    async def test_a_row_per_gradable_direction_with_its_due_date_and_class(self, srs_db):
        db = _install(srs_db)
        for w in ("hus", "bil", "katt", "hund", "bok"):
            _card(db, w)
        hus = _review(db, "hus", due_in=-1)
        bil = _review(db, "bil", due_in=2)
        _review(db, "bil", PROD, due_in=1)
        _set_state(db, "katt", REC, SRSState.LEARNING, due_at=datetime.now(UTC) - timedelta(minutes=1))
        katt = db.get_collocation_id_by_guid(db.get_collocation("katt").guid)

        plan = await _plan()

        got = {(c["item_id"], c["direction"], c["grade_class"]) for c in plan["cards"]}
        assert got == {
            (hus, "recognition", "due"),
            (bil, "recognition", "ahead"),
            (bil, "production", "ahead"),
            (katt, "recognition", "learning"),
        }
        row = next(c for c in plan["cards"] if c["item_id"] == hus)
        assert (row["kind"], row["text"], row["gloss"]) == ("word", "hus", "t-hus")
        assert row["due_at"].startswith((anki_today() - timedelta(days=1)).isoformat())

    async def test_never_new_suspended_buried_unripe_or_graded_today(self, srs_db):
        db = _install(srs_db)
        for w in ("hus", "bil", "katt", "hund", "bok"):
            _card(db, w)
        _set_state(db, "bil", REC, SRSState.SUSPENDED)
        _set_state(db, "katt", REC, SRSState.BURIED)
        _set_state(db, "hund", REC, SRSState.LEARNING, due_at=datetime.now(UTC) + timedelta(minutes=9))
        _review(db, "bok", due_in=-1, graded_today=True)

        plan = await _plan()

        assert plan["cards"] == []
        # Every word has a NEW production direction, and hus a NEW recognition too.
        assert plan["waiting_new"] == 6

    async def test_the_range_filters_review_rows_and_counts_the_rest_as_later(self, srs_db):
        db = _install(srs_db)
        for w in ("hus", "bil", "katt"):
            _card(db, w)
        hus = _review(db, "hus", due_in=0)
        bil = _review(db, "bil", due_in=3)
        _review(db, "katt", due_in=4)
        _review(db, "katt", PROD, due_in=9)

        today_only = await _plan({"range_days": 0})
        three = await _plan({"range_days": 3})
        everything = await _plan()

        assert [c["item_id"] for c in today_only["cards"]] == [hus]
        assert today_only["later"] == {"recognition": 2, "production": 1}
        assert [c["item_id"] for c in three["cards"]] == [hus, bil]
        assert three["later"] == {"recognition": 1, "production": 1}
        assert len(everything["cards"]) == 4
        assert everything["later"] == {"recognition": 0, "production": 0}

    async def test_rows_come_soonest_first(self, srs_db):
        db = _install(srs_db)
        for w in ("hus", "bil", "katt"):
            _card(db, w)
        late = _review(db, "hus", due_in=5)
        soon = _review(db, "bil", due_in=1)
        overdue = _review(db, "katt", due_in=-2)

        plan = await _plan()

        assert [c["item_id"] for c in plan["cards"]] == [overdue, soon, late]

    async def test_a_negative_range_is_refused(self, srs_db):
        _install(srs_db)
        resp = await _get(PLAN_URL, {"range_days": -1})
        assert resp.status_code == 422

    async def test_an_unknown_lesson_is_404(self, srs_db):
        _install(srs_db)
        assert (await _get("/api/srs/content/nope/grade-plan")).status_code == 404
        assert (await _post("/api/srs/content/nope/grade", {})).status_code == 404

    async def test_a_key_phrase_card_is_a_phrase_row(self, srs_db):
        db = _install(srs_db, _lesson(key_phrases=[KeyPhraseInfo(phrase="god morgen", translation="good morning")]))
        _card(db, "god morgen")
        kp = _review(db, "god morgen", due_in=-1)

        plan = await _plan()

        row = next(c for c in plan["cards"] if c["item_id"] == kp)
        assert (row["kind"], row["text"], row["gloss"]) == ("phrase", "god morgen", "t-god morgen")

    async def test_a_function_word_cloze_is_not_a_row(self, srs_db):
        """The popup never graded a word's cloze, and the plan does not start to (out of scope here)."""
        db = _install(srs_db)
        db.add_collocation(
            SyntacticUnit(
                text="hus",
                translation="house",
                word_count=1,
                difficulty=1,
                source="test",
                lemma="hus",
                card_type="cloze",
                source_sentence="{{c1::hus}} bil katt hund bok",
            ),
            language_code="no",
        )
        _review(db, "hus", PROD, due_in=-1)

        plan = await _plan()

        assert plan["cards"] == []

    async def test_studied_and_listen_count(self, srs_db):
        db = _install(srs_db)
        assert ((await _plan())["studied"], (await _plan())["listen_count"]) == (False, 0)
        db.record_listen("lesson-1")
        plan = await _plan()
        assert (plan["studied"], plan["listen_count"]) == (True, 1)


class TestPlanEdges:
    async def test_a_line_row_that_is_not_a_sentence_card_is_skipped(self, srs_db):
        """A row of a line whose card data no longer parses is not a row (a bad row never breaks the plan)."""
        from tests.test_lesson_card_list import _story

        db = _install(srs_db, _lesson("Hei!", story=_story()))
        await _grade({})
        broken, kept = (r[2] for r in db.list_sibling_rows("sent:test-01:3"))
        _review_by_id(db, broken, PROD, due_in=1)
        _review_by_id(db, kept, PROD, due_in=1)
        with db._get_conn() as conn:
            conn.execute("UPDATE collocations SET card_data = '' WHERE id = ?", (broken,))
            conn.commit()

        ids = {c["item_id"] for c in (await _plan())["cards"]}

        assert kept in ids
        assert broken not in ids


class TestPlanUnburies:
    """The daily unbury sweep runs before the plan is read (Fable audit, 2026-11-13).

    Anki releases yesterday's sibling-buries at rollover; TT does it in
    ``unbury_if_needed``, which before this ran only on the card-review path. A
    card buried yesterday was then silently missing from the plan until card
    reviews were opened (96 such directions on the live Norwegian deck).
    """

    def _bury_yesterday(self, db) -> int:
        hus = _card(db, "hus")
        _review(db, "hus", due_in=-1)
        _review(db, "hus", PROD, due_in=-1)
        with db._get_conn() as conn:
            conn.execute(
                "UPDATE collocation_directions SET state = 'buried', bury_kind = 'sched' "
                "WHERE collocation_id = ? AND direction = 'production'",
                (hus,),
            )
            conn.commit()
        db.set_anki_state_cache("last_unbury_day", (anki_today() - timedelta(days=1)).isoformat())
        return hus

    async def test_a_card_buried_yesterday_is_in_the_plan(self, srs_db):
        db = _install(srs_db)
        hus = self._bury_yesterday(db)

        plan = await _plan()

        assert (hus, "production") in {(c["item_id"], c["direction"]) for c in plan["cards"]}

    async def test_a_card_buried_yesterday_can_be_graded(self, srs_db):
        db = _install(srs_db)
        hus = self._bury_yesterday(db)

        result = await _grade({"card_grades": [_g(hus, "good", PROD)]})

        assert result["applied"] == 1


class TestGradeClassNullDueAt:
    """A REVIEW or (re)learning row with no due date is never a row, and is refused if sent.

    ``due_at`` is NOT NULL in a fresh schema, but migration 451 added it as a
    nullable column and the live decks were built through it. None was measured
    there (0 rows, Fable 2026-11-13); this keeps one from becoming a 500.
    """

    @pytest.mark.parametrize("state", [SRSState.REVIEW, SRSState.LEARNING, SRSState.RELEARNING])
    def test_no_due_date_is_not_gradable(self, state):
        from app.api.srs_grade import _grade_class, _Window

        rec = DirectionState(direction=REC, state=state, due_at=None, last_review=None)

        assert _grade_class(rec, _Window.read()) == (None, "no_due_date")


class TestPlanSentenceCards:
    """A lesson's own card list: its sentence cards are rows, production only, with line and span."""

    async def test_a_minted_sentence_card_is_a_row_with_its_line_and_blank(self, srs_db):
        from tests.test_lesson_card_list import _story

        db = _install(srs_db, _lesson("Hei!", story=_story()))
        await _grade({})  # the first Save mints the list
        line_key = "sent:test-01:2"
        rows = db.list_sibling_rows(line_key)
        blank_id = next(r[2] for r in rows if r[1].syntactic_unit.disambig_key != line_key)
        whole_id = next(r[2] for r in rows if r[1].syntactic_unit.disambig_key == line_key)
        _review_by_id(db, blank_id, PROD, due_in=1)

        plan = await _plan()

        row = next(c for c in plan["cards"] if c["item_id"] == blank_id)
        assert (row["kind"], row["direction"], row["line_key"]) == ("sentence", "production", line_key)
        text = row["text"]
        assert text[row["blank_start"] : row["blank_end"]] == "går"
        assert row["gloss"] == "Hi, how are you?"  # the line's English
        assert whole_id not in {c["item_id"] for c in plan["cards"]}  # still NEW: waiting, not a row


class TestPlanDrillCards:
    """A drill card in the lesson's card list is a row showing its step view (tunatale-5i1p.4, audit I3).

    The plan reads the drill through ``view(reps)`` like every other reader:
    ``text`` is the step's answer, ``gloss`` its English, ``drill`` the whole
    view. ``line_key`` stays None, so the page offers no blank to mine on it.
    """

    @staticmethod
    def _drill_id(db) -> int:
        return db.list_sibling_rows("sent:test-01:drill-1")[0][2]

    async def test_a_drill_card_is_a_row_with_its_step_view(self, srs_db):
        from tests.test_lesson_drill_cards import _story

        db = _install(srs_db, _lesson("Hei!", story=_story()))
        await _grade({})  # the first Save mints the list
        cid = self._drill_id(db)
        _review_by_id(db, cid, PROD, due_in=0)  # reps = 5: step 5 mod 4 = 1

        plan = await _plan()

        row = next(c for c in plan["cards"] if c["item_id"] == cid)
        assert (row["kind"], row["direction"], row["line_key"]) == ("drill", "production", None)
        assert (row["text"], row["gloss"]) == ("De er her.", "")
        assert (row["drill"]["step"], row["drill"]["of"], row["drill"]["cue"]) == (2, 4, "de")
        assert row["drill"]["before"] == "Hun er her."

    async def test_a_question_and_answer_step_is_its_own_row(self, srs_db):
        from tests.test_lesson_drill_cards import _story

        db = _install(srs_db, _lesson("Hei!", story=_story()))
        await _grade({})
        cid = db.list_sibling_rows("sent:test-01:drill-4:2")[0][2]
        _review_by_id(db, cid, PROD, due_in=0)

        row = next(c for c in (await _plan())["cards"] if c["item_id"] == cid)
        assert (row["kind"], row["text"], row["drill"]["cue"], row["drill"]["step"]) == (
            "drill",
            "Hun er der.",
            "Hvor er hun?",
            2,
        )

    async def test_a_drill_row_is_graded_like_any_other(self, srs_db):
        from tests.test_lesson_drill_cards import _story

        db = _install(srs_db, _lesson("Hei!", story=_story()))
        await _grade({})
        cid = self._drill_id(db)
        _review_by_id(db, cid, PROD, due_in=0)

        body = await _grade({"card_grades": [_g(cid, "good", PROD)]})

        assert body["applied"] == 1
        assert _direction_row(db, cid, PROD)["reps"] == 6


def _review_by_id(db, cid: int, direction: Direction, *, due_in: int) -> None:
    _, item, _ = db.get_collocation_by_id(cid)
    db.update_direction(
        item.guid,
        direction,
        DirectionState(
            direction=direction,
            state=SRSState.REVIEW,
            due_at=due_at_rollover_utc(anki_today() + timedelta(days=due_in)),
            last_review=datetime.now(UTC) - timedelta(days=5),
            stability=10.0,
            difficulty=5.0,
            reps=5,
        ),
    )


class TestPlanSuggestions:
    async def test_untracked_words_are_suggested_with_the_listens_budget(self, srs_db):
        db = _install(srs_db)
        db.set_anki_state_cache("daily_new_cap", "2")

        plan = await _plan()
        preview = (await _get("/api/srs/content/lesson-1/listen-preview")).json()

        creates = [c for c in preview["candidates"] if c["kind"] == "create"]
        assert [(w["key"], w["text"], w["gloss"], w["will_create"]) for w in plan["suggestions"]["words"]] == [
            (c["lemma"], c["text"], c["translation"], c["will_create"]) for c in creates
        ]
        assert sum(w["will_create"] for w in plan["suggestions"]["words"]) == 2


# ── Applying a grade set ───────────────────────────────────────────────────


class TestGradesApply:
    async def test_g1_a_due_card_logs_kind_1_and_charges_today(self, srs_db):
        db = _install(srs_db)
        _card(db, "hus")
        hus = _review(db, "hus", due_in=0)
        before = _reviews_today(db)

        result = await _grade({"card_grades": [_g(hus, "good")]})

        assert result["applied"] == 1
        assert [r["review_kind"] for r in _revlog(db, hus)] == [1]
        assert _reviews_today(db) == before + 1

    async def test_g2_a_card_not_due_logs_kind_3_and_does_not_charge_today(self, srs_db):
        db = _install(srs_db)
        _card(db, "hus")
        hus = _review(db, "hus", due_in=2)
        before = _reviews_today(db)

        await _grade({"card_grades": [_g(hus, "good")]})

        assert [r["review_kind"] for r in _revlog(db, hus)] == [3]
        assert _reviews_today(db) == before
        assert _direction_row(db, hus, REC)["reps"] == 6

    async def test_g3_production_of_a_word_leaves_its_recognition_byte_identical(self, srs_db):
        db = _install(srs_db)
        _card(db, "hus")
        hus = _review(db, "hus", due_in=2)
        _review(db, "hus", PROD, due_in=2)
        rec_before = _direction_row(db, hus, REC)

        await _grade({"card_grades": [_g(hus, "good", PROD)]})

        assert [(r["direction"], r["review_kind"]) for r in _revlog(db, hus)] == [("production", 3)]
        assert _direction_row(db, hus, REC) == rec_before

    async def test_g3_a_sentence_cards_grade_leaves_its_line_siblings_byte_identical(self, srs_db):
        from tests.test_lesson_card_list import _story

        db = _install(srs_db, _lesson("Hei!", story=_story()))
        await _grade({})
        rows = db.list_sibling_rows("sent:test-01:3")
        graded, other = rows[0][2], rows[1][2]
        _review_by_id(db, graded, PROD, due_in=2)
        _review_by_id(db, other, PROD, due_in=2)
        other_before = _direction_row(db, other, PROD)

        await _grade({"card_grades": [_g(graded, "good", PROD)]})

        assert [r["review_kind"] for r in _revlog(db, graded)] == [3]
        assert _direction_row(db, other, PROD) == other_before
        assert _revlog(db, other) == []

    async def test_g4_a_card_in_the_plan_that_is_not_sent_is_untouched(self, srs_db):
        db = _install(srs_db)
        _card(db, "hus")
        _card(db, "bil")
        hus = _review(db, "hus", due_in=0)
        bil = _review(db, "bil", due_in=1)
        assert {c["item_id"] for c in (await _plan())["cards"]} == {hus, bil}
        bil_before = _direction_row(db, bil, REC)

        await _grade({"card_grades": [_g(hus, "good")]})

        assert _revlog(db, bil) == []
        assert _direction_row(db, bil, REC) == bil_before
        assert _rows(db, "pending_listen_grades") == []

    async def test_g4_a_save_with_no_grades_grades_nothing(self, srs_db):
        db = _install(srs_db)
        _card(db, "hus")
        hus = _review(db, "hus", due_in=0)
        before = _direction_row(db, hus, REC)

        result = await _grade({"declined": [{"kind": "word", "key": w} for w in ("bil", "katt", "hund", "bok")]})

        assert result["applied"] == 0
        assert _revlog(db) == []
        assert _direction_row(db, hus, REC) == before
        assert _rows(db, "pending_listen_grades") == []

    async def test_g5_fifteen_grades_in_one_request_write_fifteen_revlog_rows(self, srs_db):
        words = [f"ord{i}" for i in range(15)]
        db = _install(srs_db, _lesson(" ".join(words)))
        ids = []
        for w in words:
            _card(db, w)
            ids.append(_review(db, w, due_in=0))

        result = await _grade({"card_grades": [_g(i, "good") for i in ids]})

        assert result["applied"] == 15
        assert len(_revlog(db)) == 15
        assert len({r["id"] for r in _revlog(db)}) == 15

    async def test_g6_the_learning_cutoff_advances_once_to_the_last_grade(self, srs_db):
        db = _install(srs_db)
        _card(db, "hus")
        _card(db, "bil")
        hus = _review(db, "hus", due_in=0)
        bil = _review(db, "bil", due_in=0)

        await _grade({"card_grades": [_g(hus, "good"), _g(bil, "good")]})

        last_ms = max(r["id"] for r in _revlog(db))
        # The revlog id is the grade's millisecond; the cutoff keeps the microseconds.
        assert int(datetime.fromisoformat(_cutoff(db)).timestamp() * 1000) == last_ms

    async def test_g6_no_grade_leaves_the_cutoff_alone(self, srs_db):
        db = _install(srs_db)
        db.set_anki_state_cache("learning_cutoff", "2026-01-01T00:00:00+00:00")

        await _grade({})

        assert _cutoff(db) == "2026-01-01T00:00:00+00:00"

    async def test_g9_a_stale_pending_row_for_a_graded_card_is_cleared(self, srs_db):
        db = _install(srs_db)
        _card(db, "hus")
        hus = _review(db, "hus", due_in=0)
        db.stage_pending_grade("another-lesson", hus, "recognition", "good", "due")

        await _grade({"card_grades": [_g(hus, "hard")]})

        assert db.get_pending_grade(hus, "recognition") is None
        assert [r["button_chosen"] for r in _revlog(db, hus)] == [2]

    async def test_g10_the_popovers_undo_is_refused_after_a_grade_page_grade(self, srs_db):
        db = _install(srs_db)
        _card(db, "hus")
        hus = _review(db, "hus", due_in=0)
        popover = await _post(f"/api/srs/items/{hus}/direction/recognition/feedback", {"rating": "good"})
        assert popover.status_code == 200, popover.text
        # Yesterday's popover grade: move the card to "graded before today, due in 2 days".
        _review(db, "hus", due_in=2)

        await _grade({"card_grades": [_g(hus, "good")]})
        undo = await _post(f"/api/srs/items/{hus}/direction/recognition/undo", {})

        assert undo.status_code == 409
        assert "superseded" in undo.json()["detail"]


class TestGradesRefused:
    """G8: a stale or wrong plan applies nothing, and names every row it refused."""

    async def _setup(self, srs_db):
        db = _install(srs_db)
        for w in ("hus", "bil", "katt", "hund"):
            _card(db, w)
        good = _review(db, "hus", due_in=0)
        today = _review(db, "bil", due_in=0, graded_today=True)
        new = db.get_collocation_id_by_guid(db.get_collocation("katt").guid)
        _set_state(db, "hund", REC, SRSState.SUSPENDED)
        suspended = db.get_collocation_id_by_guid(db.get_collocation("hund").guid)
        return db, good, today, new, suspended

    @pytest.mark.parametrize(
        ("which", "reason"),
        [("today", "graded_today"), ("new", "new"), ("suspended", "suspended")],
    )
    async def test_a_row_that_is_no_longer_gradable_is_409_and_nothing_applies(self, srs_db, which, reason):
        db, good, today, new, suspended = await self._setup(srs_db)
        bad = {"today": today, "new": new, "suspended": suspended}[which]

        resp = await _post(GRADE_URL, {"card_grades": [_g(good, "good"), _g(bad, "good")]})

        assert resp.status_code == 409
        assert resp.json()["detail"]["rows"] == [{"item_id": bad, "direction": "recognition", "reason": reason}]
        assert _revlog(db) == []
        assert db.count_listens("lesson-1") == 0

    async def test_an_unripe_learning_row_is_409(self, srs_db):
        db, good, *_ = await self._setup(srs_db)
        _set_state(db, "katt", REC, SRSState.LEARNING, due_at=datetime.now(UTC) + timedelta(minutes=9))
        katt = db.get_collocation_id_by_guid(db.get_collocation("katt").guid)

        resp = await _post(GRADE_URL, {"card_grades": [_g(katt, "good")]})

        assert resp.status_code == 409
        assert resp.json()["detail"]["rows"][0]["reason"] == "not_ripe"

    async def test_an_unknown_id_is_422_and_nothing_applies(self, srs_db):
        db, good, *_ = await self._setup(srs_db)

        resp = await _post(GRADE_URL, {"card_grades": [_g(good, "good"), _g(99999, "good")]})

        assert resp.status_code == 422
        assert resp.json()["detail"]["rows"] == [{"item_id": 99999, "direction": "recognition", "reason": "unknown"}]
        assert _revlog(db) == []

    async def test_another_lessons_card_is_422(self, srs_db):
        db, good, *_ = await self._setup(srs_db)
        _card(db, "fjell")
        other = _review(db, "fjell", due_in=0)

        resp = await _post(GRADE_URL, {"card_grades": [_g(other, "good")]})

        assert resp.status_code == 422
        assert resp.json()["detail"]["rows"][0]["reason"] == "not_in_lesson"

    async def test_a_row_sent_twice_is_422(self, srs_db):
        db, good, *_ = await self._setup(srs_db)

        resp = await _post(GRADE_URL, {"card_grades": [_g(good, "good"), _g(good, "easy")]})

        assert resp.status_code == 422
        assert resp.json()["detail"]["rows"][0]["reason"] == "duplicate"
        assert _revlog(db) == []

    async def test_a_direction_the_card_lacks_is_422(self, srs_db):
        from tests.test_lesson_card_list import _story

        db = _install(srs_db, _lesson("Hei!", story=_story()))
        await _grade({})
        sentence = db.list_sibling_rows("sent:test-01:1")[0][2]

        resp = await _post(GRADE_URL, {"card_grades": [_g(sentence, "good", REC)]})

        assert resp.status_code == 422
        assert resp.json()["detail"]["rows"][0]["reason"] == "no_direction"

    async def test_422_wins_over_409_and_both_are_named(self, srs_db):
        db, good, today, *_ = await self._setup(srs_db)

        resp = await _post(GRADE_URL, {"card_grades": [_g(today, "good"), _g(99999, "good")]})

        assert resp.status_code == 422
        assert {r["reason"] for r in resp.json()["detail"]["rows"]} == {"graded_today", "unknown"}

    async def test_skip_is_not_a_rating(self, srs_db):
        db, good, *_ = await self._setup(srs_db)
        resp = await _post(GRADE_URL, {"card_grades": [_g(good, "skip")]})
        assert resp.status_code == 422


# ── Differential: POST /grade against today's POST /listen ────────────────


def _twin(tmp_path, seed) -> tuple[SRSDatabase, SRSDatabase]:
    """Two file databases seeded by the same function: one for each route."""
    dbs = []
    for name in ("old", "new"):
        db = SRSDatabase(str(tmp_path / f"{name}.db"))
        seed(db)
        dbs.append(db)
    return dbs[0], dbs[1]


_MASKED = {"last_review", "last_review_time_ms"}


def _state(db) -> dict:
    directions = [
        {k: v for k, v in row.items() if k not in _MASKED}
        for row in _rows(db, "collocation_directions", "ORDER BY collocation_id, direction")
    ]
    revlog = [
        {k: v for k, v in row.items() if k != "id"}
        for row in _rows(db, "tt_revlog", "ORDER BY collocation_id, direction, id")
    ]
    return {
        "directions": directions,
        "revlog": revlog,
        "pending": _rows(db, "pending_listen_grades"),
        "cutoff_set": _cutoff(db) is not None,
        "collocations": [r["text"] for r in _rows(db, "collocations", "ORDER BY id")],
    }


def _seed_mixed(db) -> None:
    for w in ("hus", "bil", "katt", "hund", "bok"):
        _card(db, w)
    _review(db, "hus", due_in=0)
    _review(db, "bil", due_in=2)
    _review(db, "katt", due_in=2)
    _review(db, "hund", due_in=-3)
    _review(db, "bok", due_in=6)


async def _old_listen(db, grades: dict[int, str]) -> None:
    """Today's /listen with *grades* confirmed and every other row skipped."""
    _install(db)
    await _old_listen_in_place(grades)


async def _old_listen_in_place(grades: dict[int, str]) -> None:
    """``_old_listen`` against whatever lesson and database are installed."""
    preview = (await _get("/api/srs/content/lesson-1/listen-preview")).json()
    word_ratings = {str(c["item_id"]): "skip" for c in preview["candidates"] if c["item_id"] is not None}
    word_ratings.update({str(i): r for i, r in grades.items()})
    create_ratings = {c["lemma"]: "skip" for c in preview["candidates"] if c["kind"] == "create"}
    resp = await _post(
        LISTEN_URL,
        {
            "content_id": "lesson-1",
            "word_ratings": word_ratings,
            "create_ratings": create_ratings,
            "confirmed_words": list(grades),
        },
    )
    assert resp.status_code == 200, resp.text


class TestDifferential:
    async def test_recognition_grades_land_exactly_as_the_listens_confirmed_grades(self, tmp_path):
        old, new = _twin(tmp_path, _seed_mixed)
        ids = {w: old.get_collocation_id_by_guid(old.get_collocation(w).guid) for w in ("hus", "bil", "katt", "hund")}
        grades = {ids["hus"]: "good", ids["bil"]: "hard", ids["katt"]: "easy", ids["hund"]: "again"}

        await _old_listen(old, grades)
        _install(new)
        await _grade({"card_grades": [_g(i, r) for i, r in grades.items()], "studied": True})

        old_state, new_state = _state(old), _state(new)
        # Again puts hund in relearning: due_at is an instant, mask it, check the step.
        for st in (old_state, new_state):
            for row in st["directions"]:
                if row["state"] == "relearning":
                    row["due_at"] = "<instant>"
        assert new_state == old_state
        assert {r["review_kind"] for r in new_state["revlog"]} == {1, 3}

    async def test_g7_grades_in_one_save_share_one_balancer_as_the_old_path_does(self, tmp_path):
        """Each grade feeds the balancer the next one reads, exactly as the old path's shared balancer.

        Eight identical, Anki-linked cards graded Good in one request: every one
        lands inside the same fuzz window, so where the first lands moves the
        later picks. A balancer that does not see the batch's earlier grades (one
        built before the batch and copied per grade) puts them elsewhere; the old
        /listen path is the reference. Proven red under that sabotage and under
        no balancer at all. A balancer REBUILT per grade is not a fault: it
        replays the session's TT grades from the database
        (``get_load_balancer_session_replay``), so it equals the fed one.
        """
        words = [f"ord{i}" for i in range(8)]

        def seed(db):
            _install(db, _lesson(" ".join(words)))
            for i, w in enumerate(words):
                _card(db, w)
                db.set_anki_ids(db.get_collocation(w).guid, 9000 + i, {REC: 90000 + i})
                item = db.get_collocation(w)
                db.update_direction(
                    item.guid,
                    REC,
                    DirectionState(
                        direction=REC,
                        state=SRSState.REVIEW,
                        due_at=due_at_rollover_utc(anki_today()),
                        last_review=datetime.now(UTC) - timedelta(days=30),
                        stability=30.0,
                        difficulty=5.0,
                        reps=5,
                        anki_card_id=90000 + i,
                    ),
                )
            db.set_anki_state_cache("load_balancer_enabled", "true")
            # Without a synced col.crt the balancer is never built (None: pure fuzz),
            # which is how the first version of this test passed whatever the route did.
            crt = local_today_rollover(datetime.now(UTC)) - timedelta(days=400)
            db.set_anki_state_cache("col_crt", str(int(crt.timestamp())))

        old, new = _twin(tmp_path, seed)
        ids = [old.get_collocation_id_by_guid(old.get_collocation(w).guid) for w in words]
        assert build_live_load_balancer(new) is not None

        _install(old, _lesson(" ".join(words)))
        await _old_listen_in_place(dict.fromkeys(ids, "good"))
        _install(new, _lesson(" ".join(words)))
        await _grade({"card_grades": [_g(i, "good") for i in ids], "studied": True})

        old_due = [_direction_row(old, i, REC)["due_at"] for i in ids]
        assert [_direction_row(new, i, REC)["due_at"] for i in ids] == old_due
        assert len(set(old_due)) > 1, "the eight grades must spread across the window, or nothing is tested"

    async def test_c1_the_created_set_equals_the_old_routes_at_every_default(self, tmp_path):
        def seed(db):
            db.set_anki_state_cache("daily_new_cap", "3")
            _card(db, "hus")
            _review(db, "hus", due_in=4)

        old, new = _twin(tmp_path, seed)
        _install(old)
        resp = await _post(LISTEN_URL, {"content_id": "lesson-1"})
        assert resp.status_code == 200
        _install(new)
        result = await _grade({})

        created_old = [r["text"] for r in _rows(old, "collocations", "ORDER BY id")]
        created_new = [r["text"] for r in _rows(new, "collocations", "ORDER BY id")]
        assert created_new == created_old
        # hus was created today, so it charges one of the three: two more are made.
        assert len(created_new) == 3
        assert result["created"] == 2


# ── Suggestions on Save ─────────────────────────────────────────────────────


class TestSuggestionsOnSave:
    async def test_c2_created_rows_follow_the_card_adding_contract(self, srs_db):
        db = _install(srs_db)
        db.set_anki_state_cache("daily_new_cap", "2")

        first = await _grade({})
        again = await _grade({})

        assert first["created"] == 2
        assert again["created"] == 0
        made = _rows(
            db,
            "collocations c JOIN collocation_directions d ON d.collocation_id = c.id",
            "ORDER BY c.id",
        )
        assert len({r["collocation_id"] for r in made}) == 2
        for row in made:
            assert (row["state"], row["anki_note_id"], row["anki_card_id"], row["introduced_at"]) == (
                "new",
                None,
                None,
                None,
            )

    async def test_a_declined_word_is_ignored_and_keeps_its_slot(self, srs_db):
        db = _install(srs_db)
        db.set_anki_state_cache("daily_new_cap", "2")
        words = (await _plan())["suggestions"]["words"]
        live = [w["key"] for w in words if w["will_create"]]
        tail = [w["key"] for w in words if not w["will_create"]]

        result = await _grade({"declined": [{"kind": "word", "key": live[0]}]})

        assert result["created"] == 1
        texts = {r["text"] for r in _rows(db, "collocations")}
        assert live[1] in texts
        assert live[0] not in texts
        assert not texts & set(tail), "a decline consumes its slot: the next word is not promoted"
        assert live[0] in db.get_ignored_lemmas("no")
        assert live[0] not in {w["key"] for w in (await _plan())["suggestions"]["words"]}

    async def test_a_restored_word_leaves_the_ignore_list(self, srs_db):
        db = _install(srs_db)
        db.add_ignored_lemma("no", "hus")
        assert "hus" not in {w["key"] for w in (await _plan())["suggestions"]["words"]}

        await _grade({"restored": [{"kind": "word", "key": "hus"}], "declined": []})

        assert "hus" not in db.get_ignored_lemmas("no")
        assert "hus" in {w["key"] for w in (await _plan())["suggestions"]["words"]}

    async def test_a_word_past_the_budget_is_made_only_when_opted_in(self, srs_db):
        db = _install(srs_db)
        db.set_anki_state_cache("daily_new_cap", "1")
        words = (await _plan())["suggestions"]["words"]
        tail = [w["key"] for w in words if not w["will_create"]]

        result = await _grade({"over_cap": [{"kind": "word", "key": tail[-1]}]})

        assert result["created"] == 2
        texts = {r["text"] for r in _rows(db, "collocations")}
        assert tail[-1] in texts
        assert not texts & set(tail[:-1])


# ── The study mark ──────────────────────────────────────────────────────────


class TestStudyMark:
    async def test_s1_the_first_save_marks_studied_whatever_is_sent_and_mints_the_list(self, srs_db):
        from tests.test_lesson_card_list import _story
        from tests.test_lesson_card_mint import _expected_keys, _keys

        db = _install(srs_db, _lesson("Hei!", story=_story()))

        result = await _grade({"studied": False})

        assert db.count_listens("lesson-1") == 1
        assert result["listen_count"] == 1
        assert result["recorded_study"] is True
        assert [k for k in _keys(db) if k.startswith("sent:")] == [k for k in _expected_keys() if k.startswith("sent:")]

    async def test_s2_a_later_save_not_studied_adds_no_listen_and_no_rows(self, srs_db):
        from tests.test_lesson_card_list import _story
        from tests.test_lesson_card_mint import _keys

        db = _install(srs_db, _lesson("Hei!", story=_story()))
        await _grade({})
        keys = _keys(db)

        result = await _grade({"studied": False})

        assert db.count_listens("lesson-1") == 1
        assert result["recorded_study"] is False
        assert _keys(db) == keys

    async def test_s2_a_later_save_marked_studied_adds_a_listen(self, srs_db):
        db = _install(srs_db)
        await _grade({})

        result = await _grade({"studied": True})

        assert db.count_listens("lesson-1") == 2
        assert (result["recorded_study"], result["listen_count"]) == (True, 2)


# ── Contract ────────────────────────────────────────────────────────────────


class TestContract:
    def test_x2_both_routes_declare_a_response_model(self):
        schema = app.openapi()
        plan = schema["paths"]["/api/srs/content/{content_id}/grade-plan"]["get"]
        grade = schema["paths"]["/api/srs/content/{content_id}/grade"]["post"]
        for op in (plan, grade):
            assert "$ref" in op["responses"]["200"]["content"]["application/json"]["schema"]
