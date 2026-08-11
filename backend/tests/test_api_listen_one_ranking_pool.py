"""One ranking pool: NEW-state rows and creation candidates compete on zipf (F-2).

⚠️ **ORCHESTRATOR-AUTHORED ORACLE. DO NOT EDIT WHILE IMPLEMENTING.**
Brief: bd `tunatale-9c0`, source `.beads-tasks/briefs/findings-listen-preview-2026-08.md`
§ F-2. Authored ahead of the implementation on purpose: the ranking order is NEW
semantics, and an executor who writes both the behaviour and its test can only
prove the two agree with each other. If an assertion here looks wrong, STOP and
report it — do not adjust it to match what you built.

**What changed.** `_allocate_new_state_budget` used to run first and hand the
whole introduction budget to NEW-state rows, returning `max(0, budget - charged)`
as the creation budget. With >= cap NEW-state rows that is always 0, so **a
creation candidate could never outrank a NEW-state row however common it was** —
the frequency ranking shipped in `9e42b83` was dead code against real lesson
data. NEW-state rows were not ranked at all either; they landed in first-
appearance order, so dialogue position decided which existing cards got
introduced.

**The decision (user, 2026-08-04): one pool, ranked by corpus frequency across
both kinds.** This deliberately abandons the "finish the cards already in the
deck before adding more" rule that `_allocate_new_state_budget`'s docstring
stated. `test_new_state_takes_precedence_over_creation` in
`test_api_listen_new_state_cards.py` encoded that rule and is inverted by this
file's `test_a_common_create_outranks_a_rarer_new_state_row`.

**Two things the one pool does NOT touch, both asserted below:**

* *Created today is free.* A card created earlier today already holds a slot via
  `count_new_created_today`, which is subtracted when the budget is computed;
  charging it again double-counts the same card. Free rows stay live regardless
  of frequency and never enter the pool.
* *Key phrases are never frequency-ranked.* `_rank_listen_candidates` has always
  held key phrases ahead of the lemmas in lesson order — they are the lesson's
  pedagogical core. A multi-word phrase is OOV in wordfreq (zipf 0.0), so
  ranking them in the pool would sink every key phrase below every word and they
  would stop being introduced at all. Charged key phrases therefore lead the
  charged order, and only NEW-state *words* compete with creates.

**The mirror risk this file also pins.** One pool means NEW-state rows can now be
*starved* instead — a lesson full of high-frequency untracked lemmas could push
every existing NEW card below the cut, which is F-2's own complaint pointed the
other way. `test_both_kinds_appear_above_the_cut` is the guard.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.lesson import Lesson, Phrase, Section, SectionType
from app.models.syntactic_unit import SyntacticUnit
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401

PREVIEW_URL = "/api/srs/lesson/lesson-1/listen-preview"
LISTEN_URL = "/api/srs/listen"

# ── The interleaving fixture ─────────────────────────────────────────────
#
# Four Slovene nouns, alternating kind down the frequency ranking, so no
# assertion below can pass under an implementation that keeps the two kinds in
# separate budgets:
#
#   lemma   zipf(sl)   kind        zipf rank   under the OLD split
#   delo      5.85     create          1       never live (budget exhausted)
#   voda      5.12     NEW-state       2       live
#   okno      4.54     create          3       never live
#   riba      4.14     NEW-state       4       live
#
# Old behaviour with budget 2: {voda, riba} — both NEW-state, no create.
# New behaviour with budget 2: {delo, voda} — one of each.
# The two sets are disjoint except for `voda`, so a half-done implementation
# fails rather than passes weakly.
#
# All four are plain nouns: `is_function_word_for` is False for each, so they
# create vocab cards and no cloze-audio synthesis is triggered.
_SENTENCE = "delo voda okno riba"

_CREATES = ["delo", "okno"]
_NEW_STATE = ["voda", "riba"]
_ALL = _CREATES + _NEW_STATE
_BY_ZIPF = ["delo", "voda", "okno", "riba"]


def _setup(*, key_phrases=None, sentence: str = _SENTENCE, language_code: str = "sl"):
    from app.srs.database import SRSDatabase
    from app.storage.store import ContentStore

    lesson = Lesson(
        title="Day 1",
        language_code=language_code,
        sections=[
            Section(
                section_type=SectionType.NATURAL_SPEED,
                phrases=[Phrase(text=sentence, voice_id="female-1", language_code=language_code, role="female-1")],
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

    Backdated by default: a card created today is FREE against the shared
    introduction budget, so a fixture that means to be *charged* must be old.
    """
    unit = SyntacticUnit(text=text, translation=f"t-{text}", word_count=1, difficulty=1, source="test")
    db.add_collocation(unit, language_code="sl")
    if created_days_ago:
        stamp = (datetime.now(UTC) - timedelta(days=created_days_ago)).strftime("%Y-%m-%d %H:%M:%S")
        guid = db.get_collocation(text).guid
        with db._get_conn() as conn:
            conn.execute("UPDATE collocations SET created_at = ? WHERE guid = ?", (stamp, guid))


def _set_new_cap(db, cap: int) -> None:
    db.set_anki_state_cache("daily_new_cap", str(cap))


async def _preview() -> dict:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(PREVIEW_URL)
    assert resp.status_code == 200
    return resp.json()


async def _post_listen(payload: dict) -> dict:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(LISTEN_URL, json=payload)
    assert resp.status_code == 200
    return resp.json()


def _live_texts(preview: dict) -> set[str]:
    """Every row above the cut, both kinds — what the modal shows as checked."""
    return {c["text"] for c in preview["candidates"] if c.get("will_create") is True}


def _tail_texts(preview: dict) -> set[str]:
    return {c["text"] for c in preview["candidates"] if c.get("will_create") is False}


def _created(db, lemmas: list[str]) -> set[str]:
    return {lem for lem in lemmas if db.get_collocation_by_lemma(lem) is not None}


def _pending_texts(db) -> set[str]:
    with db._get_conn() as conn:
        rows = conn.execute(
            "SELECT c.text FROM pending_listen_grades p JOIN collocations c ON c.id = p.collocation_id"
        ).fetchall()
    return {r[0] for r in rows}


def _new_state_fixture(cap: int):
    """The interleaving fixture: two creates and two charged NEW-state rows."""
    db = _setup()
    _set_new_cap(db, cap)
    for w in _NEW_STATE:
        _seed_new_state(db, w)
    return db


# ── The fixture's premise ────────────────────────────────────────────────


class TestTheInterleavingPremiseHolds:
    def test_the_four_lemmas_alternate_kind_down_the_ranking(self):
        """Guard on the fixture, not the feature.

        If a wordfreq data update moves these words, this fails FIRST and names
        the cause instead of the ranking assertions failing with a confusing
        diff. Deliberately measured, not hardcoded.
        """
        from wordfreq import zipf_frequency

        z = {w: zipf_frequency(w, "sl") for w in _ALL}

        assert sorted(_ALL, key=lambda w: -z[w]) == _BY_ZIPF, z
        # The interleave is the whole point: kinds must alternate, or a
        # separate-budget implementation could still pass.
        kinds = ["create" if w in _CREATES else "new" for w in _BY_ZIPF]
        assert kinds == ["create", "new", "create", "new"], kinds


# ── Unit: the allocator ──────────────────────────────────────────────────


class TestAllocateIntroPool:
    """`_allocate_intro_pool` replaces `_allocate_new_state_budget`.

    Signature: ``(new_state_rows, creation_lemmas, budget, *, zipf, occurrences)``
    where each ``new_state_rows`` entry is
    ``(payload, created_today, text, is_key_phrase)``.
    Returns ``(live_new, tail_new, ranked_creates, live_creates)``.
    """

    def _call(self, rows, lemmas, budget, *, zipf=None, occurrences=None):
        from app.api.srs import _allocate_intro_pool

        return _allocate_intro_pool(
            rows, lemmas, budget, zipf=zipf, occurrences=occurrences if occurrences is not None else {}
        )

    def test_one_pool_ranks_both_kinds_together(self):
        """The headline. A create ranked above a NEW-state row takes the slot."""
        zipf = {"common_create": 6.0, "rare_new": 2.0}.__getitem__
        rows = [("NEW_ROW", False, "rare_new", False)]

        live_new, tail_new, ranked, live_creates = self._call(rows, ["common_create"], 1, zipf=zipf)

        assert live_creates == ["common_create"], "the commoner create must win the only slot"
        assert live_new == []
        assert tail_new == ["NEW_ROW"]
        assert ranked == ["common_create"]

    def test_a_commoner_new_state_row_still_beats_a_rarer_create(self):
        """The mirror of the headline — the pool is symmetric, not create-biased."""
        zipf = {"common_new": 6.0, "rare_create": 2.0}.__getitem__
        rows = [("NEW_ROW", False, "common_new", False)]

        live_new, tail_new, ranked, live_creates = self._call(rows, ["rare_create"], 1, zipf=zipf)

        assert live_new == ["NEW_ROW"]
        assert tail_new == []
        assert live_creates == []
        assert ranked == ["rare_create"], "the tail create is still disclosed, just not live"

    def test_free_rows_cost_nothing_and_stay_live_at_zero_budget(self):
        """ "Created today is free" survives the rewrite. A free row holds a slot
        already counted by count_new_created_today; charging it double-counts."""
        zipf = {"free_word": 0.0, "hot_create": 9.0}.__getitem__
        rows = [("FREE", True, "free_word", False)]

        live_new, tail_new, _ranked, live_creates = self._call(rows, ["hot_create"], 0, zipf=zipf)

        assert live_new == ["FREE"], "a free row is live even at zero budget"
        assert tail_new == []
        assert live_creates == [], "and it did not consume the (empty) budget either"

    def test_free_rows_do_not_displace_charged_rows_from_the_budget(self):
        """A free row is live *in addition to* the budget, not out of it."""
        zipf = {"free_word": 1.0, "charged_word": 2.0, "lem": 3.0}.__getitem__
        rows = [("FREE", True, "free_word", False), ("CHARGED", False, "charged_word", False)]

        live_new, tail_new, _ranked, live_creates = self._call(rows, ["lem"], 2, zipf=zipf)

        assert set(live_new) == {"FREE", "CHARGED"}
        assert tail_new == []
        assert live_creates == ["lem"], "budget 2 covers the create and the charged row"

    def test_key_phrases_are_charged_but_never_frequency_ranked(self):
        """A phrase is OOV (zipf 0.0); ranking it in the pool would sink every
        key phrase below every word and stop them being introduced at all."""
        zipf = {"dober dan": 0.0, "hot": 9.0}.__getitem__
        rows = [("KP", False, "dober dan", True)]

        live_new, tail_new, _ranked, live_creates = self._call(rows, ["hot"], 1, zipf=zipf)

        assert live_new == ["KP"], "the key phrase leads the charged order despite zipf 0.0"
        assert tail_new == []
        assert live_creates == []

    def test_key_phrases_still_overflow_into_the_tail(self):
        """Leading the order is not an unbudgeted path — they overflow like
        anything else."""
        zipf = {"a": 0.0, "b": 0.0}.__getitem__
        rows = [("KP1", False, "a", True), ("KP2", False, "b", True)]

        live_new, tail_new, _ranked, _live_creates = self._call(rows, [], 1, zipf=zipf)

        assert live_new == ["KP1"]
        assert tail_new == ["KP2"]

    def test_occurrence_breaks_a_frequency_tie_across_kinds(self):
        zipf = {"n": 4.0, "c": 4.0}.__getitem__
        rows = [("NEW_ROW", False, "n", False)]

        live_new, _tail, _ranked, live_creates = self._call(rows, ["c"], 1, zipf=zipf, occurrences={"n": 1, "c": 5})

        assert live_creates == ["c"], "equal zipf → the more repeated lemma wins"
        assert live_new == []

    def test_zipf_none_falls_back_to_occurrence_across_both_kinds(self):
        """The fallback arm for a language with no `wordfreq_lang`. It must rank
        the SAME single pool, not revert to the old two-budget split."""
        rows = [("NEW_ROW", False, "n", False)]

        live_new, tail_new, _ranked, live_creates = self._call(rows, ["c"], 1, zipf=None, occurrences={"n": 1, "c": 5})

        assert live_creates == ["c"]
        assert live_new == []
        assert tail_new == ["NEW_ROW"]

    def test_every_create_is_disclosed_in_rank_order_live_or_not(self):
        """`ranked_creates` is the full disclosure list the tail is drawn from."""
        zipf = {"a": 1.0, "b": 5.0, "c": 3.0}.__getitem__

        _live_new, _tail_new, ranked, live_creates = self._call([], ["a", "b", "c"], 1, zipf=zipf)

        assert ranked == ["b", "c", "a"]
        assert live_creates == ["b"]

    def test_empty_inputs(self):
        assert self._call([], [], 3) == ([], [], [], [])

    def test_zero_budget_sends_every_charged_row_to_the_tail(self):
        zipf = {"n": 5.0, "c": 6.0}.__getitem__
        rows = [("NEW_ROW", False, "n", False)]

        live_new, tail_new, ranked, live_creates = self._call(rows, ["c"], 0, zipf=zipf)

        assert live_new == []
        assert tail_new == ["NEW_ROW"]
        assert live_creates == []
        assert ranked == ["c"], "a zero budget still discloses the tail"


# ── API: both call sites, against real wordfreq ──────────────────────────


class TestOnePoolAtTheApi:
    async def test_a_common_create_outranks_a_rarer_new_state_row(self):
        """The finding, in one assertion.

        `delo` (5.85, untracked) beats `voda` (5.12, already carded and NEW).
        Under the old split this was structurally impossible: NEW-state rows
        took the whole budget first, so `creation_budget` was 0 and NO create
        could ever be live while any charged NEW-state row existed.
        """
        db = _new_state_fixture(cap=1)

        preview = await _preview()

        assert _live_texts(preview) == {"delo"}
        await _post_listen({"lesson_id": "lesson-1"})
        assert _created(db, _CREATES) == {"delo"}
        assert _pending_texts(db) == set(), "no NEW-state row was live, so none was staged"

    async def test_both_kinds_appear_above_the_cut(self):
        """The mirror risk: one pool must not starve NEW-state rows either.

        Budget 2 over the interleaved ranking → the top create AND the top
        NEW-state row. An implementation that merely swapped the priority order
        (creates first, then introductions) would put {delo, okno} here and fail.
        """
        db = _new_state_fixture(cap=2)

        preview = await _preview()
        live = _live_texts(preview)

        assert live == {"delo", "voda"}
        assert live & set(_CREATES), "creates starved"
        assert live & set(_NEW_STATE), "NEW-state rows starved"

        await _post_listen({"lesson_id": "lesson-1"})
        assert _created(db, _CREATES) == {"delo"}
        assert _pending_texts(db) == {"voda"}

    async def test_preview_and_commit_agree_across_both_kinds(self):
        """Preview↔commit parity, the 6a5c718 divergence class.

        Asserting the SET the preview promised against what the commit actually
        did, for both kinds at once — a count-only assertion passes while the
        two sides rank differently, which is exactly what this guards.
        """
        db = _new_state_fixture(cap=3)

        preview = await _preview()
        promised = _live_texts(preview)
        assert promised == {"delo", "voda", "okno"}

        await _post_listen({"lesson_id": "lesson-1"})

        landed = _created(db, _CREATES) | _pending_texts(db)
        assert landed == promised

    async def test_the_full_tail_is_still_disclosed(self):
        """Every candidate of both kinds is returned; only the flag differs."""
        _db = _new_state_fixture(cap=1)

        preview = await _preview()

        assert _live_texts(preview) | _tail_texts(preview) == set(_ALL)
        assert _tail_texts(preview) == {"voda", "okno", "riba"}

    async def test_a_card_created_today_is_still_free(self):
        """Regression guard on the rule the one pool must not break.

        `riba` is the RAREST lemma in the fixture, so under a pure ranking it
        would be last and — at budget 0 — never live. Created today, it holds a
        slot already subtracted from the budget, so it stays live regardless.
        """
        db = _setup()
        _set_new_cap(db, 0)
        _seed_new_state(db, "riba", created_days_ago=0)  # created today → free

        preview = await _preview()

        assert "riba" in _live_texts(preview)
        assert _live_texts(preview) == {"riba"}, "nothing else may be live at zero budget"

        await _post_listen({"lesson_id": "lesson-1"})
        assert _pending_texts(db) == {"riba"}
        assert _created(db, _CREATES) == set(), "zero budget creates nothing"

    async def test_a_new_state_key_phrase_leads_the_pool_despite_being_oov(self):
        """A phrase is OOV in wordfreq. Ranked in the pool it would sink below
        every word; it must keep its slot ahead of them."""
        from app.models.lesson import KeyPhraseInfo

        db = _setup(key_phrases=[KeyPhraseInfo(phrase="dober dan", translation="good day")])
        _set_new_cap(db, 1)
        _seed_new_state(db, "dober dan")

        preview = await _preview()

        assert _live_texts(preview) == {"dober dan"}
        await _post_listen({"lesson_id": "lesson-1"})
        assert _pending_texts(db) == {"dober dan"}
        assert _created(db, _CREATES) == set()


class TestSkipAndOptInSurviveTheOnePool:
    async def test_a_skipped_create_still_consumes_its_slot(self):
        """`bp-listen-skip-consumes-slot`: a skip promotes nothing. The gated
        `continue` in the creation loop must stay a `continue`, never a `break`
        (`25f2cab`) — a `break` here would also stop the opt-in test below."""
        db = _new_state_fixture(cap=1)

        await _post_listen({"lesson_id": "lesson-1", "word_ratings": {"delo": "skip"}})

        assert _created(db, _CREATES) == set(), "the slot was consumed, not promoted"
        assert _pending_texts(db) == set(), "and nothing was promoted into it"

    async def test_an_over_cap_opt_in_reaches_a_create_below_the_cut(self):
        """`okno` ranks third; at budget 1 it is deep in the tail. Naming it
        must still create it — which only works if the loop `continue`s past
        the over-budget rows instead of breaking out at the first one."""
        db = _new_state_fixture(cap=1)

        await _post_listen({"lesson_id": "lesson-1", "over_cap_words": ["okno"]})

        assert "okno" in _created(db, _CREATES)
