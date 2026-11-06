"""Attaching a sentence card's audio: the lesson's own takes become the card's media rows (h0q3.42)."""

from __future__ import annotations

import logging

from app.audio.render_cost import natural_take
from app.audio.sentence_card_audio import attach_sentence_card_audio
from app.common.guid import compute_guid
from app.models.lesson import Lesson, Phrase, Section, SectionType
from app.srs.database import SRSDatabase
from app.srs.sentence_card import Cue, SentenceCard
from tests.test_lesson_take import NB_A, NB_B, _instrument_paths

CUE_LINE, ANSWER_LINE = "Hvor bor du?", "Jeg bor i Oslo."
CARD = SentenceCard(
    "a-visit-1234",
    1,
    "Per",
    ANSWER_LINE,
    ANSWER_LINE,
    "I live in Oslo.",
    (),
    cue=Cue("Kari", CUE_LINE, CUE_LINE, "Where?"),
)


def _lesson() -> Lesson:
    return Lesson(
        title="t",
        language_code="no",
        sections=[Section(SectionType.NATURAL_SPEED, [Phrase(CUE_LINE, NB_A, "no"), Phrase(ANSWER_LINE, NB_B, "no")])],
    )


def _setup(tmp_path, card=CARD):
    db = SRSDatabase(":memory:")
    unit = card.unit()
    db.add_collocation(unit, "no")
    guid = compute_guid(unit.text, "no", unit.disambig_key)
    with db._get_conn() as conn:
        cid = conn.execute("SELECT id FROM collocations WHERE guid = ?", (guid,)).fetchone()["id"]
    cache, media = tmp_path / "cache", tmp_path / "media"
    cache.mkdir()
    media.mkdir()
    return db, guid, cid, cache, media


def _cache_both(lesson, cache):
    paths = _instrument_paths(lesson, cache)
    paths[(CUE_LINE, NB_A, False)].write_bytes(b"cue-take")
    paths[(ANSWER_LINE, NB_B, False)].write_bytes(b"answer-take")


def test_both_takes_become_media_rows_and_files(tmp_path):
    db, guid, cid, cache, media = _setup(tmp_path)
    lesson = _lesson()
    _cache_both(lesson, cache)

    answer, cue = attach_sentence_card_audio(db, cid, CARD, lesson, cache_dir=cache, media_dir=media)

    assert answer == f"take_{natural_take(lesson, ANSWER_LINE, cache_dir=cache).stem}.mp3"
    assert (media / answer).read_bytes() == b"answer-take"
    assert (media / cue).read_bytes() == b"cue-take"
    assert db.get_sentence_audio_filename(cid) == answer
    assert db.get_media_filename(cid, "audio_cue") == cue
    assert "audio" in db.get_dirty_fields(guid).split(",")


def test_voices_pick_the_right_speakers_take(tmp_path):
    card = SentenceCard("l", 1, "Per", "Ja.", "Ja.", "Yes.", (), cue=Cue("Kari", "Ja.", "Ja.", "Yes."))
    db, _guid, cid, cache, media = _setup(tmp_path, card)
    lesson = Lesson(
        "t", "no", [Section(SectionType.NATURAL_SPEED, [Phrase("Ja.", NB_A, "no"), Phrase("Ja.", NB_B, "no")])]
    )
    paths = _instrument_paths(lesson, cache)
    paths[("Ja.", NB_A, False)].write_bytes(b"A")
    paths[("Ja.", NB_B, False)].write_bytes(b"B")

    answer, cue = attach_sentence_card_audio(
        db, cid, card, lesson, cache_dir=cache, media_dir=media, answer_voice=NB_B, cue_voice=NB_A
    )

    assert (media / answer).read_bytes() == b"B"
    assert (media / cue).read_bytes() == b"A"


def test_idempotent(tmp_path):
    db, guid, cid, cache, media = _setup(tmp_path)
    lesson = _lesson()
    _cache_both(lesson, cache)
    first = attach_sentence_card_audio(db, cid, CARD, lesson, cache_dir=cache, media_dir=media)
    db.set_dirty_fields(guid, "")

    assert attach_sentence_card_audio(db, cid, CARD, lesson, cache_dir=cache, media_dir=media) == first
    assert db.list_media_kinds_for_collocation(cid) == {"audio_tts_sentence", "audio_cue"}
    assert db.get_dirty_fields(guid) == ""  # nothing new, nothing to push


def test_missing_take_is_logged_not_synthesized(tmp_path, caplog):
    db, guid, cid, cache, media = _setup(tmp_path)

    with caplog.at_level(logging.WARNING):
        assert attach_sentence_card_audio(db, cid, CARD, _lesson(), cache_dir=cache, media_dir=media) == (None, None)

    assert caplog.text.count("SENTENCE_CARD_NO_TAKE") == 2
    assert list(media.iterdir()) == []
    assert db.get_dirty_fields(guid) == ""


def test_opener_has_no_cue_audio(tmp_path):
    card = SentenceCard("l", 1, "Per", ANSWER_LINE, ANSWER_LINE, "x", ())
    db, _guid, cid, cache, media = _setup(tmp_path, card)
    lesson = _lesson()
    _cache_both(lesson, cache)

    answer, cue = attach_sentence_card_audio(db, cid, card, lesson, cache_dir=cache, media_dir=media)

    assert answer is not None
    assert cue is None


def test_a_take_already_in_media_is_reused_not_recopied(tmp_path):
    """Two cards can share a take (a cue line that is another card's answer): the file is copied once."""
    db, _guid, cid, cache, media = _setup(tmp_path)
    lesson = _lesson()
    _cache_both(lesson, cache)
    name = f"take_{natural_take(lesson, ANSWER_LINE, cache_dir=cache).name}"
    (media / name).write_bytes(b"already-here")

    answer, _cue = attach_sentence_card_audio(db, cid, CARD, lesson, cache_dir=cache, media_dir=media)

    assert answer == name
    assert (media / name).read_bytes() == b"already-here"
