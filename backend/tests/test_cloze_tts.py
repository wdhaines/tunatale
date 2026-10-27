"""Tests for synthesizing cloze TTS audio."""

from __future__ import annotations

import re

import pytest

from app.audio.cloze_tts import synthesize_cloze_audios


@pytest.fixture
def srs_db():
    from app.srs.database import SRSDatabase

    with SRSDatabase(":memory:") as db:
        yield db


def _add_cloze_collocation(srs_db, text: str = "vsak", sentence: str = "Odprto je vsak dan"):
    from app.models.syntactic_unit import SyntacticUnit

    unit = SyntacticUnit(
        text=text,
        translation="every",
        word_count=1,
        difficulty=1,
        source="llm",
        lemma=text,
        card_type="cloze",
        source_sentence=sentence,
    )
    srs_db.add_collocation(unit, language_code="sl")
    coll = srs_db.get_collocation_by_lemma_with_id(text)
    assert coll is not None
    return coll[0]  # collocation_id


async def test_synthesize_writes_two_media_rows_and_files(monkeypatch, tmp_path):
    """synthesize_cloze_audios writes two media rows and two audio files."""
    from app.srs.database import SRSDatabase

    db = SRSDatabase(":memory:")
    collocation_id = _add_cloze_collocation(db)

    fake_mp3 = b"fake-mp3-data"

    async def _fake_tts(text, voice="sl-SI-PetraNeural"):
        return fake_mp3

    import app.audio.cloze_tts as cloze_tts_mod

    monkeypatch.setattr(cloze_tts_mod, "generate_tts_audio", _fake_tts)

    await synthesize_cloze_audios(db, collocation_id, "Odprto je vsak dan", "vsak", media_dir=tmp_path)

    with db._get_conn() as conn:
        rows = conn.execute(
            "SELECT kind, filename, anki_filename FROM media WHERE collocation_id = ? ORDER BY kind",
            (collocation_id,),
        ).fetchall()

    kinds = {r["kind"] for r in rows}
    assert "audio_tts_sentence" in kinds
    assert "audio_tts" in kinds

    # Named by the voice and the text (tunatale-xj83): no fixed name to pin, only the shape
    by_kind = {r["kind"]: r for r in rows}
    expected_sentence_file = by_kind["audio_tts_sentence"]["filename"]
    word_file = by_kind["audio_tts"]["filename"]
    assert re.fullmatch(r"tts_sentence_[0-9a-f]{16}\.mp3", expected_sentence_file)
    assert re.fullmatch(r"tts_vsak_v[0-9a-f]{6}\.mp3", word_file)
    assert (tmp_path / expected_sentence_file).exists()
    assert (tmp_path / word_file).exists()

    # anki_filename must equal the real filename (not "") so the sync's
    # refresh_media reconciliation matches the [sound:] ref in Back Extra and
    # keeps the row instead of collapsing it every sync.
    assert by_kind["audio_tts_sentence"]["anki_filename"] == expected_sentence_file
    assert by_kind["audio_tts"]["anki_filename"] == word_file


async def test_synthesize_idempotent(monkeypatch, tmp_path):
    """Second call does not re-synthesize or duplicate rows."""
    from app.srs.database import SRSDatabase

    db = SRSDatabase(":memory:")
    collocation_id = _add_cloze_collocation(db)

    fake_mp3 = b"fake-mp3-data"
    call_count = 0

    async def _fake_tts(text, voice="sl-SI-PetraNeural"):
        nonlocal call_count
        call_count += 1
        return fake_mp3

    import app.audio.cloze_tts as cloze_tts_mod

    monkeypatch.setattr(cloze_tts_mod, "generate_tts_audio", _fake_tts)

    await synthesize_cloze_audios(db, collocation_id, "Odprto je vsak dan", "vsak", media_dir=tmp_path)

    first_calls = call_count

    await synthesize_cloze_audios(db, collocation_id, "Odprto je vsak dan", "vsak", media_dir=tmp_path)

    with db._get_conn() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM media WHERE collocation_id = ?",
            (collocation_id,),
        ).fetchone()[0]

    assert count == 2
    assert call_count == first_calls


async def test_sentence_dedup(monkeypatch, tmp_path):
    """Two collocations sharing one sentence share the same sentence filename."""
    from app.srs.database import SRSDatabase

    db = SRSDatabase(":memory:")
    collocation_id_1 = _add_cloze_collocation(db, "vsak", "Odprto je vsak dan")

    unit2 = __import__("app.models.syntactic_unit", fromlist=["SyntacticUnit"]).SyntacticUnit(
        text="dan",
        translation="day",
        word_count=1,
        difficulty=1,
        source="llm",
        lemma="dan",
        card_type="cloze",
        source_sentence="Odprto je vsak dan",
    )
    db.add_collocation(unit2, language_code="sl")
    collocation_id_2 = db.get_collocation_by_lemma_with_id("dan")[0]

    call_count = 0

    async def _fake_tts(text, voice="sl-SI-PetraNeural"):
        nonlocal call_count
        call_count += 1
        return b"fake"

    import app.audio.cloze_tts as cloze_tts_mod

    monkeypatch.setattr(cloze_tts_mod, "generate_tts_audio", _fake_tts)

    await synthesize_cloze_audios(db, collocation_id_1, "Odprto je vsak dan", "vsak", media_dir=tmp_path)
    await synthesize_cloze_audios(db, collocation_id_2, "Odprto je vsak dan", "dan", media_dir=tmp_path)

    with db._get_conn() as conn:
        sent_rows = conn.execute(
            "SELECT filename FROM media WHERE kind = 'audio_tts_sentence' ORDER BY id",
        ).fetchall()

    assert len(sent_rows) == 2
    assert sent_rows[0]["filename"] == sent_rows[1]["filename"]


async def test_synthesize_skips_on_tts_failure(monkeypatch, tmp_path):
    """When generate_tts_audio returns None, no file or media row is created."""
    from app.srs.database import SRSDatabase

    db = SRSDatabase(":memory:")
    collocation_id = _add_cloze_collocation(db)

    async def _fake_tts(text, voice="sl-SI-PetraNeural"):
        return None

    import app.audio.cloze_tts as cloze_tts_mod

    monkeypatch.setattr(cloze_tts_mod, "generate_tts_audio", _fake_tts)

    await synthesize_cloze_audios(db, collocation_id, "Odprto je vsak dan", "vsak", media_dir=tmp_path)

    with db._get_conn() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM media WHERE collocation_id = ?",
            (collocation_id,),
        ).fetchone()[0]

    assert count == 0


async def test_synthesize_marks_audio_dirty(monkeypatch, tmp_path):
    """After sentence audio synthesis, the collocation's dirty_fields includes 'audio'."""
    from app.srs.database import SRSDatabase

    db = SRSDatabase(":memory:")
    collocation_id = _add_cloze_collocation(db)

    async def _fake_tts(text, voice="sl-SI-PetraNeural"):
        return b"fake-mp3"

    import app.audio.cloze_tts as cloze_tts_mod

    monkeypatch.setattr(cloze_tts_mod, "generate_tts_audio", _fake_tts)

    await synthesize_cloze_audios(db, collocation_id, "Odprto je vsak dan", "vsak", media_dir=tmp_path)

    item = db.get_collocation_by_lemma("vsak")
    assert item is not None
    dirty = db.get_dirty_fields(item.guid)
    assert "audio" in dirty.split(",")


async def test_synthesize_handles_missing_guid_gracefully(monkeypatch, tmp_path):
    """When get_guid_by_collocation_id returns None, synthesize does not crash."""
    from unittest.mock import MagicMock

    from app.srs.database import SRSDatabase

    db = SRSDatabase(":memory:")
    collocation_id = _add_cloze_collocation(db)

    async def _fake_tts(text, voice="sl-SI-PetraNeural"):
        return b"fake-mp3"

    import app.audio.cloze_tts as cloze_tts_mod

    monkeypatch.setattr(cloze_tts_mod, "generate_tts_audio", _fake_tts)
    db.add_dirty_field = MagicMock()
    monkeypatch.setattr(db, "get_guid_by_collocation_id", lambda _: None)

    await synthesize_cloze_audios(db, collocation_id, "Odprto je vsak dan", "vsak", media_dir=tmp_path)

    with db._get_conn() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM media WHERE collocation_id = ?",
            (collocation_id,),
        ).fetchone()[0]
    assert count == 2  # sentence + word: wrote_sentence is True, but dirty-field skipped
    db.add_dirty_field.assert_not_called()


async def test_synthesize_idempotent_dirty_marking(monkeypatch, tmp_path):
    """Re-running synthesize_cloze_audios does not duplicate the 'audio' token in dirty_fields."""
    from app.srs.database import SRSDatabase

    db = SRSDatabase(":memory:")
    collocation_id = _add_cloze_collocation(db)

    async def _fake_tts(text, voice="sl-SI-PetraNeural"):
        return b"fake-mp3"

    import app.audio.cloze_tts as cloze_tts_mod

    monkeypatch.setattr(cloze_tts_mod, "generate_tts_audio", _fake_tts)

    await synthesize_cloze_audios(db, collocation_id, "Odprto je vsak dan", "vsak", media_dir=tmp_path)
    await synthesize_cloze_audios(db, collocation_id, "Odprto je vsak dan", "vsak", media_dir=tmp_path)

    item = db.get_collocation_by_lemma("vsak")
    assert item is not None
    dirty = db.get_dirty_fields(item.guid)
    tokens = dirty.split(",")
    assert tokens == sorted(set(tokens))  # no dupes
    assert "audio" in tokens


# ── A clip is the voice's (tunatale-xj83) ─────────────────────────────────────────────
# One media dir serves every language and learner. Named by the word alone, a Cebuano
# cloze for 'gusto' found Tagalog's tts_gusto.mp3 on disk and took the Tagalog voice.


async def test_the_same_word_in_two_voices_gets_two_clips(monkeypatch, tmp_path):
    import app.audio.cloze_tts as cloze_tts_mod
    from app.models.syntactic_unit import SyntacticUnit
    from app.srs.database import SRSDatabase

    rendered: list[tuple[str, str]] = []

    async def _fake_tts(text, voice="x"):
        rendered.append((text, voice))
        return f"{voice}:{text}".encode()

    monkeypatch.setattr(cloze_tts_mod, "generate_tts_audio", _fake_tts)
    files = {}
    for lang, voice in (("tl", "tl-PH-BlessicaNeural"), ("ceb", "ceb-PH-KoreGemini")):
        with SRSDatabase(":memory:") as db:
            unit = SyntacticUnit(
                text="gusto",
                translation="want",
                word_count=1,
                difficulty=1,
                source="llm",
                lemma="gusto",
                card_type="cloze",
                source_sentence="Gusto ko.",
            )
            db.add_collocation(unit, language_code=lang)
            cid = db.get_collocation_by_lemma_with_id("gusto")[0]
            await synthesize_cloze_audios(db, cid, "Gusto ko.", "gusto", voice=voice, media_dir=tmp_path)
            with db._get_conn() as conn:
                rows = conn.execute("SELECT kind, filename FROM media WHERE collocation_id = ?", (cid,)).fetchall()
            files[lang] = {r["kind"]: r["filename"] for r in rows}

    assert files["tl"]["audio_tts"] != files["ceb"]["audio_tts"]
    assert files["tl"]["audio_tts_sentence"] != files["ceb"]["audio_tts_sentence"]
    assert (tmp_path / files["ceb"]["audio_tts"]).read_bytes() == b"ceb-PH-KoreGemini:gusto"
    assert len(rendered) == 4  # each voice rendered its own word and sentence


async def test_the_same_word_in_one_voice_is_rendered_once(monkeypatch, tmp_path):
    # Many cloze cards blank one function word: its clip is shared within a voice.
    import app.audio.cloze_tts as cloze_tts_mod

    rendered: list[str] = []

    async def _fake_tts(text, voice="x"):
        rendered.append(text)
        return f"{voice}:{text}".encode()

    monkeypatch.setattr(cloze_tts_mod, "generate_tts_audio", _fake_tts)
    from app.srs.database import SRSDatabase

    with SRSDatabase(":memory:") as db:
        a = _add_cloze_collocation(db, "vsak", "Odprto je vsak dan")
        await synthesize_cloze_audios(db, a, "Odprto je vsak dan", "vsak", voice="v1", media_dir=tmp_path)
    with SRSDatabase(":memory:") as db:
        b = _add_cloze_collocation(db, "vsak", "Vsak dan delam")
        await synthesize_cloze_audios(db, b, "Vsak dan delam", "vsak", voice="v1", media_dir=tmp_path)
    assert rendered.count("vsak") == 1
