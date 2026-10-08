"""Redoing the word audio of cards a sync voiced in the wrong language (tunatale-r8hk).

The situation it repairs, measured on the live Cebuano deck 2026-10-06: 702
seeded cards, 12 holding a Forvo recording from the NORWEGIAN section and 690 a
clip in a Norwegian TTS voice, because the sync's media generator read the
default language. What must hold for the repair:

- it touches exactly the cards the caller names (by ``source`` or by word);
- the wrong audio is gone from TT and flagged, so the next sync clears or
  replaces it in the Anki note — including when Forvo has nothing to give;
- only the free half happens here: Forvo, in the right language, never TTS;
- a second learner's DB costs no second request (``MemoFetch``).
"""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256

import pytest

from app.cards.media.audio_repair import MemoFetch, apply_audio_repair, plan_audio_repair
from app.cards.media.pipeline import MediaResult
from app.models.srs_item import Direction, DirectionState, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.anki_mirror.rollover import anki_today, due_at_rollover_utc
from app.srs.database import SRSDatabase

LANG = "ceb"


@pytest.fixture
def db():
    database = SRSDatabase(":memory:")
    yield database
    database.close()


@pytest.fixture(autouse=True)
def _media_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("app.cards.media.vocab_media._MEDIA_DIR", tmp_path / "media")
    return tmp_path / "media"


def _card(db, word, *, source="base-list", seen=False, card_type="vocab", audio=("audio_tts", None), note_id=None):
    unit = SyntacticUnit(
        text=word,
        translation="gloss",
        word_count=1,
        difficulty=1,
        source=source,
        card_type=card_type,
        source_sentence="Usa ka tudling." if card_type == "cloze" else "",
    )
    if seen:
        state = DirectionState(
            direction=Direction.RECOGNITION,
            due_at=due_at_rollover_utc(anki_today()),
            state=SRSState.REVIEW,
            reps=3,
            last_review=datetime.fromisoformat("2026-10-01T12:00:00+00:00"),
        )
        db.upsert_by_guid(unit, LANG, {Direction.RECOGNITION: state}, anki_note_id=note_id)
    else:
        db.add_collocation(unit, language_code=LANG)
    coll_id = db.get_collocation_id_by_guid(db.get_collocation(word).guid)
    if audio is not None:
        kind, name = audio
        name = name or f"tts_{word}.mp3"
        db.add_media(coll_id, kind, name, f"media/{name}", name, "old", 3)
    return coll_id


class _Fetch:
    def __init__(self, forvo_has=()):
        self.calls: list[tuple[str, dict]] = []
        self._forvo_has = set(forvo_has)

    async def __call__(self, word, english, **kwargs):
        self.calls.append((word, kwargs))
        if word in self._forvo_has and kwargs.get("forvo_enabled") is not False:
            return MediaResult(audio_bytes=b"HUMAN:" + word.encode(), audio_source="forvo", audio_status="found")
        if kwargs["audio"] == "forvo":
            return MediaResult(audio_status="no_pronunciation")
        return MediaResult(audio_bytes=b"TTS:" + word.encode(), audio_source="tts")


class TestPlan:
    def test_it_names_the_cards_of_the_given_sources_and_words_only(self, db):
        _card(db, "gabii", source="cognate")
        _card(db, "iro", source="base-list")
        _card(db, "dala", source="user")
        _card(db, "gusto", source="user")
        _card(db, "kay", source="base-list", card_type="cloze", audio=None)

        plan = plan_audio_repair(db, sources={"base-list", "cognate"}, words={"dala"}, language_code=LANG)

        assert sorted(r.text for r in plan) == ["dala", "gabii", "iro"]

    def test_it_records_what_each_card_has_and_whether_it_was_met(self, db):
        _card(db, "gabii", seen=True, note_id=5001)
        _card(db, "iro", audio=None)

        by_word = {r.text: r for r in plan_audio_repair(db, sources={"base-list"}, language_code=LANG)}

        assert (by_word["gabii"].old_filename, by_word["gabii"].seen, by_word["gabii"].linked) == (
            "tts_gabii.mp3",
            True,
            True,
        )
        assert (by_word["iro"].old_filename, by_word["iro"].seen, by_word["iro"].linked) == (None, False, False)

    # "Already redone" is read off the file name, because nothing else records it:
    # only this repair and the audio pre-stage write `<prefix>_<stem>_<sha8>.mp3`,
    # with prefix `tts` for a render and the language code for a Forvo recording.
    # The property: the name is EXACTLY one of those two shapes for THIS card in
    # THIS language. Anything looser re-labels wrong audio as repaired, and a
    # second run then leaves it in place for good.
    @pytest.mark.parametrize(
        ("kind", "name", "repaired"),
        [
            ("audio_tts", "tts_gabii_0a1b2c3d.mp3", True),  # a render from the pre-stage or a repair
            ("audio_forvo", "ceb_gabii_0a1b2c3d.mp3", True),  # a Forvo recording from this language's section
            ("audio_tts", "tts_gabii.mp3", False),  # the legacy name the wrong audio carries
            ("audio_forvo", "ceb_gabii.mp3", False),
            ("audio_forvo", "no_gabii_0a1b2c3d.mp3", False),  # hash-named, but another language's section
            ("audio_tts", "tts_gabii_0A1B2C3D.mp3", False),  # not a hexdigest: those are lowercase
            ("audio_tts", "tts_gabii_0a1b2c3.mp3", False),  # seven digits
            ("audio_tts", "tts_gabii_0a1b2c3d4.mp3", False),  # nine
            ("audio_tts", "tts_gabii_0a1b2c3g.mp3", False),  # not hex
            ("audio_tts", "tts_gabii_0a1b2c3d.ogg", False),
            ("audio_tts", "xtts_gabii_0a1b2c3d.mp3", False),  # the prefix is the whole prefix
            ("audio_tts", "tts_gabii_0a1b2c3d.mp3.bak", False),  # the name ends at .mp3
            ("audio_tts", "tts_gabiixx_0a1b2c3d.mp3", False),  # another word's file
            ("audio_tts", "tts_iro_0a1b2c3d.mp3", False),
        ],
    )
    def test_a_card_counts_as_redone_only_under_a_content_hash_name_of_its_own(self, db, kind, name, repaired):
        _card(db, "gabii", audio=(kind, name))

        (repair,) = plan_audio_repair(db, sources={"base-list"}, language_code=LANG)

        assert repair.repaired is repaired

    def test_a_card_with_no_audio_is_not_redone(self, db):
        _card(db, "iro", audio=None)

        (repair,) = plan_audio_repair(db, sources={"base-list"}, language_code=LANG)

        assert repair.repaired is False

    def test_the_name_is_matched_through_the_same_stem_the_writers_use(self, db):
        # safe_stem drops punctuation and joins words with "_"; the raw text
        # would never match the name the pre-stage actually wrote.
        _card(db, "maayong buntag!", audio=("audio_tts", "tts_maayong_buntag_0a1b2c3d.mp3"))

        (repair,) = plan_audio_repair(db, sources={"base-list"}, language_code=LANG)

        assert repair.repaired is True

    def test_a_legacy_name_whose_last_word_looks_like_a_digest_is_not_redone(self, db):
        # `tts_<word>.mp3` for a two-word card whose second word is eight hex
        # letters. A pattern that only looks at the tail calls this repaired.
        _card(db, "kape deadbeef", audio=("audio_tts", "tts_kape_deadbeef.mp3"))

        (repair,) = plan_audio_repair(db, sources={"base-list"}, language_code=LANG)

        assert repair.repaired is False

    def test_a_dot_in_the_language_code_is_not_a_wildcard(self, db):
        # The prefix is text, not a pattern.
        _card(db, "gabii", audio=("audio_forvo", "cXb_gabii_0a1b2c3d.mp3"))

        (repair,) = plan_audio_repair(db, sources={"base-list"}, language_code="c.b")

        assert repair.repaired is False


class TestApply:
    async def test_a_card_forvo_has_gets_the_recording_under_a_new_name(self, db, _media_dir):
        coll_id = _card(db, "gabii", audio=("audio_forvo", "ceb_gabii.mp3"))
        fetch = _Fetch(forvo_has={"gabii"})

        report = await apply_audio_repair(
            db, plan_audio_repair(db, sources={"base-list"}, language_code=LANG), LANG, fetch_fn=fetch
        )

        name = f"ceb_gabii_{sha256(b'HUMAN:gabii').hexdigest()[:8]}.mp3"
        assert db.get_audio_filename(coll_id) == name, "never the old name: that file is the wrong recording"
        assert (_media_dir / name).read_bytes() == b"HUMAN:gabii"
        assert db.list_media_kinds_for_collocation(coll_id) == {"audio_forvo"}
        assert db.get_dirty_fields(db.get_collocation("gabii").guid) == "audio"
        assert (report.dropped, report.forvo) == (1, 1)

    async def test_a_card_forvo_lacks_loses_its_wrong_audio_and_is_still_flagged(self, db):
        """The push that clears the wrong recording from the note is the same push."""
        seen = _card(db, "gabii", seen=True)
        unseen = _card(db, "iro")

        report = await apply_audio_repair(
            db, plan_audio_repair(db, sources={"base-list"}, language_code=LANG), LANG, fetch_fn=_Fetch()
        )

        assert db.get_audio_filename(seen) is None
        assert db.get_audio_filename(unseen) is None
        assert db.get_dirty_fields(db.get_collocation("gabii").guid) == "audio"
        assert db.get_dirty_fields(db.get_collocation("iro").guid) == "audio"
        assert report == (2, 0, 1, 1)

    async def test_it_asks_forvo_only_in_the_repair_language(self, db):
        _card(db, "gabii")
        fetch = _Fetch()

        await apply_audio_repair(
            db, plan_audio_repair(db, sources={"base-list"}, language_code=LANG), LANG, fetch_fn=fetch
        )

        word, kwargs = fetch.calls[0]
        assert (word, kwargs["language_code"], kwargs["audio"], kwargs["image_query"]) == ("gabii", LANG, "forvo", "")

    async def test_a_fetch_returning_none_counts_as_nothing_found(self, db):
        _card(db, "gabii")

        async def nothing(word, english, **kwargs):
            return None

        report = await apply_audio_repair(
            db, plan_audio_repair(db, sources={"base-list"}, language_code=LANG), LANG, fetch_fn=nothing
        )
        assert (report.forvo, report.awaiting_tts_unseen) == (0, 1)

    async def test_the_delay_paces_the_requests(self, db, monkeypatch):
        _card(db, "gabii")
        _card(db, "iro")
        slept: list[float] = []

        async def fake_sleep(seconds):
            slept.append(seconds)

        monkeypatch.setattr("asyncio.sleep", fake_sleep)
        await apply_audio_repair(
            db, plan_audio_repair(db, sources={"base-list"}, language_code=LANG), LANG, fetch_fn=_Fetch(), delay=1.5
        )
        assert slept == [1.5, 1.5]

    async def test_an_untouched_card_keeps_its_audio(self, db):
        """The control: a card outside the plan is not a card this repairs."""
        _card(db, "gabii")
        other = _card(db, "gusto", source="user", audio=("audio_forvo", "ceb_gusto.mp3"))

        await apply_audio_repair(
            db, plan_audio_repair(db, sources={"base-list"}, language_code=LANG), LANG, fetch_fn=_Fetch()
        )

        assert db.get_audio_filename(other) == "ceb_gusto.mp3"
        assert db.get_dirty_fields(db.get_collocation("gusto").guid) == ""


class TestMemoFetch:
    async def test_a_second_lookup_of_a_word_makes_no_second_request(self):
        inner = _Fetch(forvo_has={"gabii"})
        fetch = MemoFetch(inner)

        first = await fetch("gabii", "night", audio="forvo", language_code=LANG)
        second = await fetch("gabii", "night", audio="forvo", language_code=LANG)

        assert first is second
        assert len(inner.calls) == 1

    async def test_a_tts_render_is_made_once_and_reused(self):
        """A metered voice is not deterministic: two renders are two FILES."""
        inner = _Fetch()
        fetch = MemoFetch(inner)

        first = await fetch("iro", "dog", audio="full", language_code=LANG)
        second = await fetch("iro", "dog", audio="full", language_code=LANG)

        assert first is second and first.audio_bytes == b"TTS:iro"
        assert len(inner.calls) == 1

    async def test_a_word_forvo_already_gave_is_not_fetched_again_for_full(self):
        inner = _Fetch(forvo_has={"gabii"})
        fetch = MemoFetch(inner)

        swept = await fetch("gabii", "night", audio="forvo", language_code=LANG)
        full = await fetch("gabii", "night", audio="full", language_code=LANG)

        assert full is swept
        assert len(inner.calls) == 1

    async def test_a_word_forvo_already_lacked_goes_straight_to_tts(self):
        inner = _Fetch()
        fetch = MemoFetch(inner)

        await fetch("iro", "dog", audio="forvo", language_code=LANG)
        full = await fetch("iro", "dog", audio="full", language_code=LANG)

        assert full.audio_source == "tts"
        assert inner.calls[1][1]["forvo_enabled"] is False, "no second Forvo request for a known miss"

    async def test_a_word_never_swept_keeps_the_forvo_step(self):
        inner = _Fetch(forvo_has={"gabii"})
        fetch = MemoFetch(inner)

        full = await fetch("gabii", "night", audio="full", language_code=LANG)

        assert full.audio_source == "forvo"
        assert "forvo_enabled" not in inner.calls[0][1]

    def test_it_wraps_the_real_pipeline_by_default(self):
        from app.cards.media.pipeline import fetch_card_media

        assert MemoFetch()._fetch is fetch_card_media
