"""The affix drill card: minted once per pattern from a checked chain, voiced at mint (tunatale-5i1p.6).

Brief: ``.beads-tasks/briefs/brief-ve4p-affix-drill-card-2026-11.md`` § Oracle
(C5, C6, M1-M4, A1-A3, S1 here; C1-C4 and C7 are the plugin's, in
``test_ceb_affix_chain.py``).

The card is a substitution drill card (``app.srs.drill_card``, tunatale-5i1p.4)
whose steps come from the language plugin instead of a story: keyed
``sent:affix-<pattern>:drill-1``, never a course card, its chain frozen in
``card_data`` at mint. Its 13 clips are synthesized once, through the
renderer's own planned requests, and attached from the TTS cache by the same
section-aware lookup a lesson drill card uses, so the clip the estimator prices
is the clip attached.

The deck is the owner's (``test_grammar_lesson.py::owner``); ``gawas`` is in
review there, ``abot`` still NEW.
"""

from __future__ import annotations

import base64
import logging

import httpx
import pytest
import respx

from app.audio.affix_card_audio import affix_drill_card_section, synthesize_drill_card_audio
from app.audio.synth_plan import plan_section
from app.languages import get_phoneme_planner, get_preprocessor, get_slow_word, get_tts_locale
from app.models.lesson import Lesson, Section, SectionType
from app.models.srs_item import Direction, SRSState
from app.plugins.anki_sync.sync import AnkiSync, OfflineWriter
from app.srs.affix_drill_card import affix_drill_card, mint_affix_drill_card
from app.srs.drill_card import DrillCard, DrillStep, drill_card
from app.srs.lesson_cards import COURSE_SOURCE
from app.storage.store import ContentStore
from tests._helpers.anki_sync_create_new import FakeReader
from tests.test_anki_sentence_note_sync import _conn
from tests.test_gemini_tts import SYNTHESIS_URL, _svc
from tests.test_grammar_lesson import _card, owner  # noqa: F401

KEY = "sent:affix-mo-mi:drill-1"
_DAY_3 = ["uban", "inom", "adto", "anhi", "lakaw"]
_DAY_5 = ["sulod", "adto", "inom", "anhi"]


def _drill_lesson(roots: list[str], *, pattern: str = "mo-mi", language_code: str = "ceb") -> Lesson:
    """A grammar lesson as stored: what matters here is the drill it records."""
    return Lesson(
        title="Drill",
        language_code=language_code,
        sections=[Section(section_type=SectionType.AFFIX_DRILL, phrases=[])],
        generation_metadata={"affix_drill": {"pattern": pattern, "roots": roots[:2], "new_roots": roots[2:]}},
    )


def _learn(db, *roots: str) -> None:
    for root in roots:
        _card(db, root, recognition=SRSState.REVIEW)


def _rows(db) -> list[tuple]:
    with db._get_conn() as conn:
        return conn.execute(
            "SELECT id, text, source, card_type, anki_note_id, sibling_group FROM collocations WHERE disambig_key = ?",
            (KEY,),
        ).fetchall()


# --- which card ----------------------------------------------------------


def test_the_card_is_the_owners_chain_as_a_substitution_drill(owner):  # noqa: F811
    db, store = owner
    _learn(db, "gawas")

    card = affix_drill_card(db, store, _drill_lesson(_DAY_3))

    assert (card.key, card.kind, card.model, card.text) == (KEY, "substitution", "Molakaw ko ugma.", "Molakaw ko ugma.")
    assert card.steps == (
        DrillStep("gahapon", "Milakaw ko gahapon.", "I walked yesterday."),
        DrillStep("gawas", "Migawas ko gahapon.", "I went out yesterday."),
        DrillStep("kada adlaw", "Mogawas ko kada adlaw.", "I go out every day."),
        DrillStep("unya", "Mogawas ko unya.", "I'll go out later."),
        DrillStep("ganiha", "Migawas ko ganiha.", "I went out earlier today."),
        DrillStep("wala", "Wala ko mogawas ganiha.", "I didn't go out earlier today."),
    )


def test_c5_a_card_only_root_some_listened_drill_records_is_skipped(owner):  # noqa: F811
    db, store = owner
    _learn(db, "gawas", "abot")
    store.save_lesson("old-drill", "funeral", 3, _drill_lesson(["inom", "gawas"]))
    db.record_listen("old-drill")

    card = affix_drill_card(db, store, _drill_lesson(_DAY_3))

    assert card.steps[1] == DrillStep("abot", "Miabot ko gahapon.", "I arrived yesterday.")


def test_c5_control_an_unlistened_drill_that_records_it_does_not_skip_it(owner):  # noqa: F811
    db, store = owner
    _learn(db, "gawas", "abot")
    store.save_lesson("old-drill", "funeral", 3, _drill_lesson(["inom", "gawas"]))
    # A listen of another pattern's drill, and of a lesson that is gone, change nothing either.
    store.save_lesson("maka-drill", "funeral", 4, _drill_lesson(["gawas"], pattern="maka-naka"))
    db.record_listen("maka-drill")
    db.record_listen("deleted-lesson")

    assert affix_drill_card(db, store, _drill_lesson(_DAY_3)).steps[1].cue == "gawas"


def test_a_card_only_root_the_learner_has_not_learned_is_not_taken(owner):  # noqa: F811
    db, store = owner
    _card(db, "gawas", recognition=SRSState.NEW)  # in the deck, not yet learned

    assert affix_drill_card(db, store, _drill_lesson(_DAY_3)).steps[1].cue == "balik"


@pytest.mark.parametrize(
    "lesson",
    [
        _drill_lesson(_DAY_3, pattern="maka-naka"),  # a pattern with no chain
        _drill_lesson(_DAY_3, pattern="no-such"),  # a pattern the language does not have
        _drill_lesson(["eta"], pattern="mo-mi", language_code="no"),  # a language with no hook
        Lesson(title="Story", language_code="ceb", sections=[]),  # no affix drill at all
    ],
)
def test_c6_nothing_else_has_a_card(owner, lesson):  # noqa: F811
    db, store = owner
    _learn(db, "gawas")

    assert affix_drill_card(db, store, lesson) is None
    assert mint_affix_drill_card(db, store, lesson) is None
    assert _rows(db) == []


def test_c4_a_drill_of_no_chain_root_mints_nothing(owner):  # noqa: F811
    db, store = owner
    _learn(db, "gawas")

    assert mint_affix_drill_card(db, store, _drill_lesson(["uban", "adto", "sulod"])) is None
    assert _rows(db) == []


# --- the mint ---------------------------------------------------------------


def test_m1_one_new_row_no_anki_ids_not_a_course_card(owner):  # noqa: F811
    db, store = owner
    _learn(db, "gawas")

    cid, card = mint_affix_drill_card(db, store, _drill_lesson(_DAY_3))

    [(row_id, text, source, card_type, note_id, group)] = _rows(db)
    assert (row_id, text, card_type, note_id, group) == (cid, "Molakaw ko ugma.", "cloze", None, KEY)
    assert source != COURSE_SOURCE
    _, item, _ = db.get_collocation_by_id(cid)
    assert list(item.directions) == [Direction.PRODUCTION]
    prod = item.directions[Direction.PRODUCTION]
    assert (prod.state, prod.anki_card_id, prod.last_review) == (SRSState.NEW, None, None)
    assert drill_card(item.syntactic_unit) == card


def test_m2_one_card_per_pattern(owner):  # noqa: F811
    db, store = owner
    _learn(db, "gawas")
    assert mint_affix_drill_card(db, store, _drill_lesson(_DAY_3)) is not None

    # The same lesson again, and another mo- / mi- lesson whose chain would differ.
    assert mint_affix_drill_card(db, store, _drill_lesson(_DAY_3)) is None
    assert mint_affix_drill_card(db, store, _drill_lesson(_DAY_5)) is None
    assert len(_rows(db)) == 1


def test_m3_the_minted_row_serves_a_substitution_drill(owner):  # noqa: F811
    db, store = owner
    _learn(db, "gawas")
    cid, _ = mint_affix_drill_card(db, store, _drill_lesson(_DAY_3))
    card = drill_card(db.get_collocation_by_id(cid)[1].syntactic_unit)

    first, second, again = card.view(0), card.view(1), card.view(6)

    assert (first.before, first.cue, first.answer) == ("Molakaw ko ugma.", "gahapon", "Milakaw ko gahapon.")
    assert (second.before, second.cue, second.answer) == ("Milakaw ko gahapon.", "gawas", "Migawas ko gahapon.")
    assert again == first


def test_m4_the_chain_is_frozen_at_mint(owner):  # noqa: F811
    db, store = owner
    _learn(db, "gawas")
    cid, minted = mint_affix_drill_card(db, store, _drill_lesson(_DAY_3))

    # What the plugin would say now differs (another lesson, another root A) ...
    now = affix_drill_card(db, store, _drill_lesson(_DAY_5))
    assert now.model != minted.model
    # ... and the row still holds the chain it was minted with.
    assert drill_card(db.get_collocation_by_id(cid)[1].syntactic_unit) == minted


# --- the speech -----------------------------------------------------------------


def _requests(card) -> list:
    section = affix_drill_card_section(card, "ceb")
    return plan_section(
        section,
        "ceb",
        preprocessor=get_preprocessor("ceb"),
        planner=get_phoneme_planner("ceb"),
        locale_for=get_tts_locale,
        slow_word=get_slow_word("ceb"),
    )


def _media(db, cid: int) -> dict[str, str]:
    with db._get_conn() as conn:
        rows = conn.execute("SELECT kind, filename FROM media WHERE collocation_id = ?", (cid,)).fetchall()
    return {row["kind"]: row["filename"] for row in rows}


def test_a1_every_clip_is_planned_with_its_per_word_readings(owner):  # noqa: F811
    db, store = owner
    _learn(db, "gawas")
    card = affix_drill_card(db, store, _drill_lesson(_DAY_3))

    requests = _requests(card)

    assert [r.text for r in requests] == [text for _, text in card.media_kinds()]
    assert len(requests) == 13
    assert all(r.phonemes for r in requests), [r.text for r in requests if not r.phonemes]


@respx.mock(assert_all_called=True)
async def test_a1_a_mint_synthesizes_13_clips_and_attaches_each_from_the_cache(owner, tmp_path, respx_mock):  # noqa: F811
    db, store = owner
    _learn(db, "gawas")
    cid, card = mint_affix_drill_card(db, store, _drill_lesson(_DAY_3))
    audio = {"audioContent": base64.b64encode(b"ID3-clip").decode()}
    route = respx_mock.post(SYNTHESIS_URL).mock(return_value=httpx.Response(200, json=audio))
    cache, media = tmp_path / "cache", tmp_path / "media"
    media.mkdir()

    await synthesize_drill_card_audio(db, cid, card, "ceb", tts=_svc(cache_dir=cache), cache_dir=cache, media_dir=media)

    assert route.call_count == 13
    kinds = _media(db, cid)
    assert sorted(kinds) == sorted(kind for kind, _ in card.media_kinds())
    assert {k for k in kinds if k.startswith("drill_cue_")} == {f"drill_cue_{k}" for k in range(6)}
    assert all((media / name).exists() for name in kinds.values())
    assert "audio" in db.get_dirty_fields(db.get_collocation_by_id(cid)[1].guid)

    # Attaching again is idempotent and asks the vendor for nothing more.
    await synthesize_drill_card_audio(db, cid, card, "ceb", tts=_svc(cache_dir=cache), cache_dir=cache, media_dir=media)
    assert route.call_count == 13
    assert _media(db, cid) == kinds


class _DownTTS:
    """The vendor is down: every request fails, as a TTS outage does."""

    async def synthesize(self, *args, **kwargs):
        raise RuntimeError("TTS down")


async def test_a2_a_synthesis_failure_leaves_the_card_without_sound(owner, tmp_path, caplog):  # noqa: F811
    db, store = owner
    _learn(db, "gawas")
    cid, card = mint_affix_drill_card(db, store, _drill_lesson(_DAY_3))

    with caplog.at_level(logging.WARNING):
        await synthesize_drill_card_audio(db, cid, card, "ceb", tts=_DownTTS(), cache_dir=tmp_path, media_dir=tmp_path)

    assert _media(db, cid) == {}
    assert len(_rows(db)) == 1
    assert f"DRILL_CARD_SYNTH_FAILED key={KEY}" in caplog.text


async def test_a2_no_tts_service_leaves_the_card_without_sound(owner, tmp_path, caplog):  # noqa: F811
    db, store = owner
    _learn(db, "gawas")
    cid, card = mint_affix_drill_card(db, store, _drill_lesson(_DAY_3))

    with caplog.at_level(logging.WARNING):
        await synthesize_drill_card_audio(db, cid, card, "ceb", tts=None, cache_dir=tmp_path, media_dir=tmp_path)

    assert _media(db, cid) == {}
    assert f"DRILL_CARD_SYNTH_FAILED key={KEY}" in caplog.text


# --- one sync -----------------------------------------------------------------------


async def test_s1_one_sync_makes_one_tt_sentence_note_at_step_one(owner):  # noqa: F811
    from app.srs.sentence_note import drill_note_fields, drill_source_label

    db, store = owner
    _learn(db, "gawas")
    cid, card = mint_affix_drill_card(db, store, _drill_lesson(_DAY_3))
    anki = _conn()

    sync = AnkiSync(db=db, _reader=FakeReader(), _writer=OfflineWriter(anki), language_code="ceb")
    await sync.sync_create_new(deck_name="0. Slovene", model_name="Slovene Vocabulary")

    _, item, _ = db.get_collocation_by_id(cid)
    note = anki.execute("SELECT flds, tags, mid FROM notes WHERE id = ?", (item.anki_note_id,)).fetchone()
    name = anki.execute("SELECT name FROM notetypes WHERE id = ?", (note["mid"],)).fetchone()[0]
    cards = anki.execute("SELECT ord FROM cards WHERE nid = ?", (item.anki_note_id,)).fetchall()
    assert name == "TT Sentence"
    assert [c["ord"] for c in cards] == [0]
    assert note["flds"] == "\x1f".join(drill_note_fields(card.view(0), media={}, source=drill_source_label(card)))
    assert note["tags"].split() == ["tunatale", "sentence", "drill"]


def test_a_store_without_the_lesson_listened_is_fine(owner):  # noqa: F811
    """A content store with nothing in it: the card is still built from the lesson in hand."""
    db, _ = owner
    _learn(db, "gawas")

    assert affix_drill_card(db, ContentStore(":memory:"), _drill_lesson(_DAY_3)).model == "Molakaw ko ugma."


@respx.mock(assert_all_called=True)
async def test_a_clip_the_cache_does_not_hold_is_logged_and_left_out(owner, tmp_path, caplog, respx_mock):  # noqa: F811
    """An adapter caching somewhere else than *cache_dir*: synthesized, but nothing to attach from."""
    db, store = owner
    _learn(db, "gawas")
    cid, card = mint_affix_drill_card(db, store, _drill_lesson(_DAY_3))
    audio = {"audioContent": base64.b64encode(b"ID3-clip").decode()}
    respx_mock.post(SYNTHESIS_URL).mock(return_value=httpx.Response(200, json=audio))

    with caplog.at_level(logging.WARNING):
        await synthesize_drill_card_audio(
            db,
            cid,
            card,
            "ceb",
            tts=_svc(cache_dir=tmp_path / "elsewhere"),
            cache_dir=tmp_path / "cache",
            media_dir=tmp_path,
        )

    assert _media(db, cid) == {}
    assert "audio" not in db.get_dirty_fields(db.get_collocation_by_id(cid)[1].guid)
    assert caplog.text.count(f"DRILL_CARD_NO_TAKE key={KEY}") == 13


# --- audit follow-ups (PR #426) ------------------------------------------------------


def test_mining_a_blank_on_the_card_is_refused(owner):  # noqa: F811
    """``sentence_mining.add_blank`` refuses a drill row (5i1p.4, U8); the affix card is one."""
    from app.srs.sentence_mining import LineNotFound, add_blank

    db, store = owner
    _learn(db, "gawas")
    mint_affix_drill_card(db, store, _drill_lesson(_DAY_3))

    with pytest.raises(LineNotFound):
        add_blank(db, "ceb", KEY, (0, 7), "will walk")
    assert len(_rows(db)) == 1


def test_the_notes_source_label_is_the_patterns_title(owner):  # noqa: F811
    from app.srs.sentence_note import drill_source_label

    db, store = owner
    _learn(db, "gawas")

    card = affix_drill_card(db, store, _drill_lesson(_DAY_3))

    assert drill_source_label(card) == "Affix drill: mo- / mi-"
    # Control: a lesson's drill card keeps its set-and-number label.
    assert drill_source_label(DrillCard("wolff-1-practice-1", 2, "substitution", "m", ())) == (
        "Wolff 1 practice 1 · drill 2"
    )
    # A set that only looks like an affix card's falls back to the same rule.
    assert drill_source_label(DrillCard("affix-nope", 1, "substitution", "m", ())) == "Affix nope · drill 1"


def test_a_pattern_with_no_chain_reads_no_listened_lesson(owner):  # noqa: F811
    """The listened-lesson scan runs only when the plugin asks about a root."""
    db, store = owner
    reads: list[str] = []

    class _CountingStore:
        def get_readable_content(self, lesson_id):
            reads.append(lesson_id)
            return store.get_readable_content(lesson_id)

    db.record_listen("wake-1")

    assert affix_drill_card(db, _CountingStore(), _drill_lesson(_DAY_3, pattern="maka-naka")) is None
    assert reads == []
    # Control: a pattern with a chain does read it.
    _learn(db, "gawas")
    assert affix_drill_card(db, _CountingStore(), _drill_lesson(_DAY_3)) is not None
    assert reads == ["wake-1"]
