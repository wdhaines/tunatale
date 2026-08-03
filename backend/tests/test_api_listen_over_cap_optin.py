"""Opting a single over-budget row past the daily new-card cap — 2026-08 brief.

Orchestrator-authored oracle. ⚠️ DO NOT EDIT while implementing — this is the
oracle, not a test of your implementation. If an assertion looks wrong, STOP and
report with evidence.

An over-budget ("tail") row is a *system* state, not a user decision: nothing is
pre-marked, nothing is promoted, and the live/tail divider never slides. The one
way past the daily cap is a deliberate per-row opt-in, which is exactly Anki's
own Custom Study → "Increase today's new card limit". It is a user-initiated
limit increase, NOT a parity bug.

The two populations differ and are tested separately:

* an over-budget **create** row spends no allowance — a created card is NEW-state
  and merely joins the pool, which ``new_quota = max(0, cap - introduced_today)``
  still gates at serve time;
* an over-budget **NEW-state** row really does introduce a card, stamping
  ``introduced_at``. That is the deliberate overage.

Both self-limit: ``count_new_created_today`` and ``count_new_introduced_today``
feed the next budget computation, so an overage today drives the next listen's
budget toward 0 on its own. There is no persisted override flag to track.

⚠️ Every test below re-derives the live/tail split from the preview before
acting on it. That is deliberate: a test whose opt-in target drifts OUT of the
tail would still pass while exercising nothing, which is precisely how two
skip-consumes-slot tests were silently neutered by the frequency-ranking change
on 2026-08-03.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.lesson import Lesson, Phrase, Section, SectionType
from app.models.srs_item import Direction, SRSState
from app.models.syntactic_unit import SyntacticUnit
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401

PREVIEW_URL = "/api/srs/lesson/lesson-1/listen-preview"
LISTEN_URL = "/api/srs/listen"
COMMIT_URL = "/api/srs/lesson/lesson-1/commit-pending"

# Same fixture as test_api_listen_creation_skip_consumes_slot.py, and for the
# same reason — the ranking is corpus frequency (wordfreq zipf, commonest
# first), not in-lesson occurrence count:
#
#   occurrences: banka=3, kava=2, hotel=1, center=1, mesto=1
#   zipf(sl):    mesto=5.79, center=5.21, hotel=4.98, banka=4.39, kava=4.09
#
# With a cap of 2:  live = [mesto, center]   tail = [hotel, banka, kava]
#
# `kava` is therefore the LAST tail row, three ranks past the budget — the
# target that distinguishes a gated `continue` from the `break` this brief
# replaces. A `break` stops at `hotel` and never reaches it.
_SENTENCE = "hotel kava banka kava banka banka center mesto"
_EXPECTED_RANK = ["mesto", "center", "hotel", "banka", "kava"]
_EXPECTED_LIVE = ["mesto", "center"]
_EXPECTED_TAIL = ["hotel", "banka", "kava"]


def _setup(key_phrases=None, phrase_text: str = _SENTENCE):
    from app.srs.database import SRSDatabase
    from app.storage.store import ContentStore

    lesson = Lesson(
        title="Day 1",
        language_code="sl",
        sections=[
            Section(
                section_type=SectionType.NATURAL_SPEED,
                phrases=[Phrase(text=phrase_text, voice_id="female-1", language_code="sl", role="female-1")],
            )
        ],
        key_phrases=key_phrases or [],
    )
    db = SRSDatabase(":memory:")
    store = ContentStore(":memory:")
    store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
    app.state.srs_db = db
    app.state.content_store = store
    return db


def _seed_new_state(db, text: str, *, created_days_ago: int = 3) -> None:
    """A tracked vocab card whose recognition direction is still NEW.

    Backdated by default: a card created inside today's Anki-day window is FREE
    against the shared introduction budget, so a "charged" fixture must be old.
    """
    unit = SyntacticUnit(text=text, translation=f"t-{text}", word_count=1, difficulty=1, source="test")
    db.add_collocation(unit, language_code="sl")
    stamp = (datetime.now(UTC) - timedelta(days=created_days_ago)).strftime("%Y-%m-%d %H:%M:%S")
    guid = db.get_collocation(text).guid
    with db._get_conn() as conn:
        conn.execute("UPDATE collocations SET created_at = ? WHERE guid = ?", (stamp, guid))


def _set_cap(db, cap: int) -> None:
    db.set_anki_state_cache("daily_new_cap", str(cap))


async def _get_preview() -> dict:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(PREVIEW_URL)
    assert resp.status_code == 200
    return resp.json()


async def _post_listen(payload: dict) -> dict:
    payload = {"lesson_id": "lesson-1", **payload}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(LISTEN_URL, json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _created(db, lemmas: list[str]) -> set[str]:
    return {lem for lem in lemmas if db.get_collocation_by_lemma(lem) is not None}


def _pending_texts(db) -> set[str]:
    with db._get_conn() as conn:
        rows = conn.execute(
            """
            SELECT c.text FROM pending_listen_grades p
            JOIN collocations c ON c.id = p.collocation_id
            """
        ).fetchall()
    return {r[0] for r in rows}


async def _create_split() -> tuple[list[str], list[str]]:
    """(live, tail) create-row texts, in rank order, straight from the preview."""
    creates = [c for c in (await _get_preview())["candidates"] if c["kind"] == "create"]
    return (
        [c["text"] for c in creates if c["will_create"]],
        [c["text"] for c in creates if not c["will_create"]],
    )


class TestOverCapCreateRow:
    """An opted-in create row is created; its unnamed tail neighbours are not."""

    async def test_the_fixture_still_puts_three_rows_in_the_tail(self):
        """Guard for every test in this file: if the ranking moves, the opt-in
        targets below stop being tail rows and their tests silently pass while
        exercising nothing. Fail loudly here instead."""
        db = _setup()
        _set_cap(db, 2)

        live, tail = await _create_split()
        assert live == _EXPECTED_LIVE
        assert tail == _EXPECTED_TAIL

    async def test_opting_in_one_tail_row_creates_it_and_only_it(self):
        db = _setup()
        _set_cap(db, 2)

        _live, tail = await _create_split()
        assert tail[0] == "hotel"

        listen = await _post_listen({"over_cap_words": ["hotel"]})

        assert listen["created"] == 3
        assert _created(db, _EXPECTED_RANK) == {"mesto", "center", "hotel"}
        assert _created(db, ["banka", "kava"]) == set(), "unnamed tail neighbours must not be created"
        assert listen["remaining_candidates"] == 2

    async def test_opting_in_the_LAST_tail_row_reaches_it(self):
        """The `break` → gated `continue` regression test, and the single most
        likely thing in this brief to get wrong.

        `kava` sits at rank index 4 with a creation budget of 2. A loop that
        `break`s at the first over-budget row stops at `hotel` (index 2) and
        never evaluates `kava` at all, so this test fails against a `break`
        implementation and passes against a gated `continue`. Do not omit it.
        """
        db = _setup()
        _set_cap(db, 2)

        _live, tail = await _create_split()
        assert tail[-1] == "kava", "the opt-in target must be the LAST tail row"
        assert tail.index("kava") == 2, "…and several ranks past the budget"

        listen = await _post_listen({"over_cap_words": ["kava"]})

        assert listen["created"] == 3
        assert _created(db, _EXPECTED_RANK) == {"mesto", "center", "kava"}
        assert _created(db, ["hotel", "banka"]) == set(), "rows ranked between must stay uncreated"

    async def test_two_opt_ins_both_land(self):
        """Opting in is per-row and additive; nothing about it is one-shot."""
        db = _setup()
        _set_cap(db, 2)

        listen = await _post_listen({"over_cap_words": ["hotel", "kava"]})

        assert listen["created"] == 4
        assert _created(db, _EXPECTED_RANK) == {"mesto", "center", "hotel", "kava"}
        assert _created(db, ["banka"]) == set()


class TestSkipBeatsOptIn:
    async def test_skip_wins_when_a_lemma_is_named_in_both(self):
        """A row cannot honestly be both refused and demanded. `skip` is the
        explicit user decision, so it takes precedence — and, as ever, it
        consumes its slot and promotes nothing into it."""
        db = _setup()
        _set_cap(db, 2)

        listen = await _post_listen({"word_ratings": {"hotel": "skip"}, "over_cap_words": ["hotel"]})

        assert listen["created"] == 2
        assert _created(db, _EXPECTED_RANK) == {"mesto", "center"}
        assert _created(db, ["hotel"]) == set(), "skip must beat the opt-in"


class TestValidation:
    """A name is honoured ONLY if it is genuinely a candidate genuinely past the
    budget. Without this a malformed payload creates unbounded cards."""

    async def test_an_unknown_lemma_is_ignored_silently(self):
        db = _setup()
        _set_cap(db, 2)

        listen = await _post_listen({"over_cap_words": ["zzzneobstojece", "kaviarna"]})

        assert listen["created"] == 2
        assert _created(db, _EXPECTED_RANK) == {"mesto", "center"}
        assert db.get_collocation_by_lemma("zzzneobstojece") is None
        assert db.get_collocation_by_lemma("kaviarna") is None

    async def test_an_already_in_budget_lemma_changes_nothing(self):
        """Naming a live row is inert — it was going to be created anyway, and
        it must not consume or shift anything."""
        db = _setup()
        _set_cap(db, 2)

        listen = await _post_listen({"over_cap_words": ["mesto"]})

        assert listen["created"] == 2
        assert _created(db, _EXPECTED_RANK) == {"mesto", "center"}
        assert listen["remaining_candidates"] == 3


class TestEmptyPayloadIsTodaysBehaviour:
    async def test_no_opt_ins_reproduces_the_current_commit_exactly(self):
        db = _setup()
        _set_cap(db, 2)

        listen = await _post_listen({})

        assert listen["created"] == 2
        assert _created(db, _EXPECTED_RANK) == {"mesto", "center"}
        assert listen["remaining_candidates"] == 3

    async def test_explicit_empty_lists_are_the_same_as_omitting_them(self):
        db = _setup()
        _set_cap(db, 2)

        listen = await _post_listen({"over_cap_words": [], "over_cap_kps": []})

        assert listen["created"] == 2
        assert _created(db, _EXPECTED_RANK) == {"mesto", "center"}


class TestOverCapNewStateRow:
    """The population that actually spends Anki's daily new allowance."""

    async def test_an_opted_in_tail_new_state_row_is_staged(self):
        db = _setup(phrase_text="Banka riba")
        _set_cap(db, 1)
        _seed_new_state(db, "banka")
        _seed_new_state(db, "riba")

        cands = [c for c in (await _get_preview())["candidates"] if c["grade_class"] == "new"]
        live = {c["text"] for c in cands if c["will_create"]}
        tail = {c["text"] for c in cands if not c["will_create"]}
        assert len(live) == 1 and len(tail) == 1, "fixture must produce exactly one tail row"
        tail_text = next(iter(tail))

        await _post_listen({"over_cap_words": [tail_text]})

        assert _pending_texts(db) == live | tail, "the opted-in tail row must stage like a live one"

    async def test_an_untouched_tail_new_state_row_is_still_not_staged(self):
        db = _setup(phrase_text="Banka riba")
        _set_cap(db, 1)
        _seed_new_state(db, "banka")
        _seed_new_state(db, "riba")

        cands = [c for c in (await _get_preview())["candidates"] if c["grade_class"] == "new"]
        live = {c["text"] for c in cands if c["will_create"]}

        await _post_listen({})

        assert _pending_texts(db) == live

    async def test_releasing_an_opted_in_row_really_spends_the_allowance(self):
        """The deliberate overage, end to end: the release stamps `introduced_at`
        and `count_new_introduced_today` goes to TWO against a cap of ONE."""
        from app.srs.anki_mirror.rollover import anki_today

        db = _setup(phrase_text="Banka riba")
        _set_cap(db, 1)
        _seed_new_state(db, "banka")
        _seed_new_state(db, "riba")

        today = anki_today()
        assert db.count_new_introduced_today(today) == 0

        cands = [c for c in (await _get_preview())["candidates"] if c["grade_class"] == "new"]
        tail_text = next(c["text"] for c in cands if not c["will_create"])

        await _post_listen({"over_cap_words": [tail_text]})

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(COMMIT_URL)
        assert resp.status_code == 200

        rec = db.get_collocation(tail_text).directions[Direction.RECOGNITION]
        assert rec.state is not SRSState.NEW, "release must leave NEW"
        assert rec.introduced_at is not None, "introduced_at must be stamped"
        assert db.count_new_introduced_today(today) == 2, "one over the cap of 1, on purpose"


class TestOverCapKeyPhrase:
    """`over_cap_kps` is the key-phrase half of the same mechanism. Key phrases
    are never *created* by a listen, so this arm is NEW-state only."""

    async def test_an_opted_in_tail_key_phrase_is_staged(self):
        from app.models.lesson import KeyPhraseInfo

        db = _setup(
            phrase_text="Banka",
            key_phrases=[
                KeyPhraseInfo(phrase="dober dan", translation="good day"),
                KeyPhraseInfo(phrase="lahko noc", translation="good night"),
            ],
        )
        _set_cap(db, 1)
        _seed_new_state(db, "dober dan")
        _seed_new_state(db, "lahko noc")

        cands = [c for c in (await _get_preview())["candidates"] if c["grade_class"] == "new"]
        live = {c["text"] for c in cands if c["will_create"]}
        tail = {c["text"] for c in cands if not c["will_create"]}
        assert len(live) == 1 and len(tail) == 1
        tail_text = next(iter(tail))

        await _post_listen({"over_cap_kps": [tail_text]})

        assert _pending_texts(db) == live | tail

    async def test_a_key_phrase_named_in_over_cap_words_is_not_honoured(self):
        """The two lists are not interchangeable — a key phrase must be named in
        `over_cap_kps`, mirroring the `confirmed_words` / `confirmed_kps` split."""
        from app.models.lesson import KeyPhraseInfo

        db = _setup(
            phrase_text="Banka",
            key_phrases=[
                KeyPhraseInfo(phrase="dober dan", translation="good day"),
                KeyPhraseInfo(phrase="lahko noc", translation="good night"),
            ],
        )
        _set_cap(db, 1)
        _seed_new_state(db, "dober dan")
        _seed_new_state(db, "lahko noc")

        cands = [c for c in (await _get_preview())["candidates"] if c["grade_class"] == "new"]
        live = {c["text"] for c in cands if c["will_create"]}
        tail_text = next(c["text"] for c in cands if not c["will_create"])

        await _post_listen({"over_cap_words": [tail_text]})

        assert _pending_texts(db) == live
