"""Frequency-informed ranking of listen creation candidates (the D2 seam swap).

⚠️ **ORCHESTRATOR-AUTHORED ORACLE. DO NOT EDIT WHILE IMPLEMENTING.**
Brief: bd `tunatale-bth` (closed). This file is authored
ahead of the implementation on purpose, per that brief's 2026-07-31 SPLIT note:
ranking order is NEW semantics, and an executor who writes both the behaviour
and its test can only prove the two agree with each other. If an assertion here
looks wrong, STOP and report it — do not adjust it to match what you built.

The change under test: `_rank_listen_candidates` ranks untracked creation
candidates by CORPUS frequency (wordfreq zipf, commonest first) instead of raw
in-lesson occurrence count, so a lesson-local filler word stops outranking a
high-utility common word.

Why this matters more than it looks: since `bp-listen-skip-consumes-slot`
shipped, a skip consumes its rank slot and promotes nothing, and the over-budget
tail is read-only. The sort order ALONE decides which lemmas become cards — the
user's only lever is veto. The rank index is also what stamps `will_create` in
`get_listen_preview`, which draws the visible cut line in the modal.

Sort key (final, do not re-litigate): `(-zipf(lem), -occurrences[lem], index)`.
OOV lemmas (zipf 0.0 — typically proper nouns) sink to the end; names stop
becoming early cards, which is intended.

`zipf=None` must reproduce today's behaviour EXACTLY. It is the fallback for a
language with no `wordfreq_lang`, and it must not be deleted even though both
registered languages set one.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.lesson import Lesson, Phrase, Section, SectionType
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401

PREVIEW_URL = "/api/srs/lesson/lesson-1/listen-preview"
LISTEN_URL = "/api/srs/listen"

# ── The inversion fixture ────────────────────────────────────────────────
#
# Four Slovene nouns whose corpus frequency runs OPPOSITE to their in-lesson
# occurrence count, so the two rankings are exact reverses of one another and
# no assertion below can pass by accident:
#
#   lemma     occurrences   zipf(sl)   occurrence rank   zipf rank
#   žirafa         4          0.00           1              4  (OOV)
#   pingvin        3          3.17           2              3
#   mesto          2          5.79           3              2
#   miza           1          4.25           4              1  ← see note
#
# Note miza/mesto: zipf puts mesto (5.79) above miza (4.25), so the zipf order
# is mesto, miza, pingvin, žirafa — NOT a literal reversal of the occurrence
# order. That is deliberate; a perfect mirror would let an implementation that
# merely reverses the old list pass. The zipf values are asserted at runtime
# rather than hardcoded (see `test_the_inversion_premise_holds_in_this_corpus`)
# so a wordfreq data update fails loudly here instead of silently rotting the
# fixture out from under the ranking assertions.
#
# All four are plain nouns: `is_function_word_for` is False for each, so they
# create vocab cards and no cloze-audio synthesis is triggered.
_SENTENCE = "žirafa pingvin mesto miza žirafa pingvin mesto žirafa pingvin žirafa"

_BY_OCCURRENCE = ["žirafa", "pingvin", "mesto", "miza"]  # today
_BY_ZIPF = ["mesto", "miza", "pingvin", "žirafa"]  # after this brief
_ALL = sorted(_BY_ZIPF)


def _setup(language_code: str = "sl"):
    from app.srs.database import SRSDatabase
    from app.storage.store import ContentStore

    lesson = Lesson(
        title="Day 1",
        language_code=language_code,
        sections=[
            Section(
                section_type=SectionType.NATURAL_SPEED,
                phrases=[
                    Phrase(
                        text=_SENTENCE,
                        voice_id="female-1",
                        language_code=language_code,
                        role="female-1",
                    )
                ],
            )
        ],
        key_phrases=[],
    )
    db = SRSDatabase(":memory:")
    store = ContentStore(":memory:")
    store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
    app.state.srs_db = db
    app.state.content_store = store
    return db


async def _get_preview() -> dict:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(PREVIEW_URL)
    assert resp.status_code == 200
    return resp.json()


async def _post_listen(payload: dict) -> dict:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(LISTEN_URL, json=payload)
    assert resp.status_code == 200
    return resp.json()


def _created(db, lemmas: list[str]) -> set[str]:
    return {lem for lem in lemmas if db.get_collocation_by_lemma(lem) is not None}


# ── Unit: the seam itself ────────────────────────────────────────────────


class TestRankListenCandidatesFrequencyMode:
    """`_rank_listen_candidates` gains a keyword-only `zipf` callable."""

    def test_zipf_none_is_byte_identical_to_today(self):
        """The fallback arm for a language with no `wordfreq_lang`.

        Pinned against an explicit fixture AND against the no-kwarg call, so the
        default cannot drift away from the explicit None. Do NOT delete this
        arm because both registered languages happen to set `wordfreq_lang`.
        """
        from app.api.srs import _rank_listen_candidates

        lemmas = ["a", "b", "c"]
        occ = {"a": 1, "b": 3, "c": 2}
        expected = [("lemma", "b"), ("lemma", "c"), ("lemma", "a")]

        assert _rank_listen_candidates([], lemmas, occ, zipf=None) == expected
        assert _rank_listen_candidates([], lemmas, occ) == expected

    def test_frequency_descending_with_occurrence_tiebreak(self):
        """Commonest first; equal frequency falls back to occurrence count."""
        from app.api.srs import _rank_listen_candidates

        zipf = {"a": 5.0, "b": 5.0, "c": 3.0}.__getitem__
        # a and b tie on zipf, so b's higher occurrence promotes it above a,
        # even though a appears first in the input.
        ranked = _rank_listen_candidates([], ["a", "b", "c"], {"a": 1, "b": 2, "c": 9}, zipf=zipf)
        assert ranked == [("lemma", "b"), ("lemma", "a"), ("lemma", "c")]

    def test_full_ties_keep_first_appearance_order(self):
        """Equal zipf AND equal occurrence → the sort stays stable."""
        from app.api.srs import _rank_listen_candidates

        zipf = {"x": 4.0, "y": 4.0, "z": 4.0}.__getitem__
        ranked = _rank_listen_candidates([], ["x", "y", "z"], {"x": 2, "y": 2, "z": 2}, zipf=zipf)
        assert ranked == [("lemma", "x"), ("lemma", "y"), ("lemma", "z")]

    def test_oov_sinks_below_everything_regardless_of_occurrence(self):
        """A zipf of 0.0 is the OOV signal — typically a proper noun.

        `oov` dominates on occurrence count by 10x and must STILL land last.
        This is the assertion that stops names becoming a lesson's first cards.
        """
        from app.api.srs import _rank_listen_candidates

        zipf = {"oov": 0.0, "rare": 2.0, "common": 6.0}.__getitem__
        ranked = _rank_listen_candidates([], ["oov", "rare", "common"], {"oov": 50, "rare": 2, "common": 1}, zipf=zipf)
        assert ranked == [("lemma", "common"), ("lemma", "rare"), ("lemma", "oov")]

    @pytest.mark.parametrize("mode", ["fallback", "frequency"])
    def test_key_phrases_lead_and_keep_lesson_order_in_both_modes(self, mode):
        """The key-phrase arm is untouched by this brief: kps first, lesson
        order, never reordered by frequency."""
        from app.api.srs import _rank_listen_candidates

        zipf = None if mode == "fallback" else {"a": 1.0, "b": 9.0}.__getitem__
        ranked = _rank_listen_candidates(["kp2", "kp1"], ["a", "b"], {"a": 5, "b": 1}, zipf=zipf)

        assert ranked[:2] == [("kp", "kp2"), ("kp", "kp1")]
        assert {kind for kind, _ in ranked[2:]} == {"lemma"}

    def test_empty_inputs(self):
        from app.api.srs import _rank_listen_candidates

        assert _rank_listen_candidates([], [], {}, zipf=None) == []


class TestWordfreqLanguageResolution:
    """Language resolution goes through the plugin registry, never a literal."""

    def test_registered_languages_declare_a_wordfreq_code(self):
        from app.languages import get_wordfreq_lang

        # Both shipping languages rank by frequency. The literals live in the
        # plugin registration modules — NOT here, and not in core `app/**`.
        assert get_wordfreq_lang("sl")
        assert get_wordfreq_lang("no")

    def test_unregistered_language_disables_frequency_ranking(self):
        """The `None` path is reachable without a third plugin: an unknown code
        has no config, so it has no wordfreq code, so ranking falls back."""
        from app.api.srs import _zipf_for
        from app.languages import get_wordfreq_lang

        assert get_wordfreq_lang("xx") is None
        assert _zipf_for("xx") is None

    def test_resolved_callable_returns_real_corpus_frequencies(self):
        from app.api.srs import _zipf_for

        zipf = _zipf_for("sl")
        assert zipf is not None
        assert zipf("mesto") > zipf("pingvin") > 0.0


# ── API: the two call sites, against real wordfreq ───────────────────────


class TestListenCreationRanksByFrequency:
    def test_the_inversion_premise_holds_in_this_corpus(self):
        """Guard on the fixture itself, not on the feature.

        If a wordfreq data update ever moves these words, this fails FIRST and
        names the cause, instead of the ranking assertions failing with a
        confusing diff. Deliberately not hardcoded, per the brief.
        """
        from wordfreq import zipf_frequency

        z = {w: zipf_frequency(w, "sl") for w in _ALL}

        assert z["žirafa"] == 0.0, f"žirafa must be OOV for the sink test; got {z['žirafa']}"
        assert z["mesto"] > z["miza"] > z["pingvin"] > 0.0, z
        # The inversion: the LEAST frequent word is the most repeated one.
        assert sorted(_ALL, key=lambda w: -z[w]) == _BY_ZIPF

    async def test_preview_ranks_creates_by_frequency_not_occurrence(self):
        db = _setup()
        db.set_anki_state_cache("daily_new_cap", "4")

        preview = await _get_preview()
        creates = [c["text"] for c in preview["candidates"] if c["kind"] == "create"]

        assert creates == _BY_ZIPF
        assert creates != _BY_OCCURRENCE, "still ranking by in-lesson occurrence count"

    async def test_a_one_card_budget_creates_the_commonest_word(self):
        """The whole point, in one assertion. `žirafa` is repeated 4x and would
        win under the old ranking; `mesto` is the word actually worth knowing.
        """
        db = _setup()
        db.set_anki_state_cache("daily_new_cap", "1")

        listen = await _post_listen({"lesson_id": "lesson-1"})

        assert listen["created"] == 1
        assert _created(db, _ALL) == {"mesto"}

    async def test_an_oov_proper_noun_is_created_last(self):
        """Budget of 3 of 4: the OOV word is the one left behind."""
        db = _setup()
        db.set_anki_state_cache("daily_new_cap", "3")

        await _post_listen({"lesson_id": "lesson-1"})

        assert _created(db, _ALL) == {"mesto", "miza", "pingvin"}

    async def test_preview_and_commit_agree_on_the_same_set(self):
        """Preview↔commit parity — replaces the old review-queue ordering test,
        which pinned a call site that no longer exists (`get_lesson_review_queue`
        stopped ranking with this seam on 2026-07-27).

        Both endpoints must apply the SAME sort. Asserting the sets, not the
        counts: a count-only assertion passes while the two rank differently,
        which is exactly the divergence this guards (the 6a5c718 bug class).
        """
        db = _setup()
        db.set_anki_state_cache("daily_new_cap", "2")

        preview = await _get_preview()
        promised = {c["text"] for c in preview["candidates"] if c["kind"] == "create" and c["will_create"]}

        await _post_listen({"lesson_id": "lesson-1"})

        assert promised == _created(db, _ALL)
        assert promised == {"mesto", "miza"}
