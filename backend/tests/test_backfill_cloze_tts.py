"""Tests for the cloze TTS backfill CLI."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.audio.backfill_cloze_tts import backfill_cloze_tts


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'test.db'}"


@pytest.fixture(autouse=True)
def _patch_media_dir(monkeypatch, tmp_path: Path) -> None:
    """Point cloze_tts._MEDIA_DIR at a temp directory so tests don't pollute
    the real backend/media/ and aren't affected by pre-existing files there."""
    import app.audio.cloze_tts as cloze_tts_mod

    monkeypatch.setattr(cloze_tts_mod, "_MEDIA_DIR", tmp_path)


def _seed_cloze_row(db, text: str, sentence: str, lemma: str | None = None):
    from app.models.syntactic_unit import SyntacticUnit

    unit = SyntacticUnit(
        text=text,
        translation="test",
        word_count=1,
        difficulty=1,
        source="llm",
        lemma=lemma or text,
        card_type="cloze",
        source_sentence=sentence,
    )
    db.add_collocation(unit, language_code="sl")


def test_backfill_synthesizes_for_missing_audio(monkeypatch, db_path):
    """Backfill synthesizes for cloze rows missing both sentence and word audio."""
    import app.audio.cloze_tts as cloze_tts_mod

    async def _fake_tts(text, voice="sl-SI-PetraNeural"):
        return b"fake-mp3"

    monkeypatch.setattr(cloze_tts_mod, "generate_tts_audio", _fake_tts)

    from app.srs.database import SRSDatabase

    db = SRSDatabase(db_path)
    _seed_cloze_row(db, "vsak", "Odprto je vsak dan")

    result = backfill_cloze_tts(db_path=db_path, dry_run=False)

    assert result["synthesized"] >= 1
    assert result["total"] >= 1

    db2 = SRSDatabase(db_path)
    coll = db2.get_collocation_by_lemma("vsak")
    assert coll is not None
    coll_id = db2.get_collocation_by_lemma_with_id("vsak")[0]
    assert db2.get_sentence_audio_filename(coll_id) is not None
    assert db2.get_audio_filename(coll_id) is not None


def test_backfill_dry_run(monkeypatch, db_path):
    """Dry-run does not create audio files or media rows."""
    import app.audio.cloze_tts as cloze_tts_mod

    call_count = 0

    async def _fake_tts(text, voice="sl-SI-PetraNeural"):
        nonlocal call_count
        call_count += 1
        return b"fake-mp3"

    monkeypatch.setattr(cloze_tts_mod, "generate_tts_audio", _fake_tts)

    from app.srs.database import SRSDatabase

    db = SRSDatabase(db_path)
    _seed_cloze_row(db, "vsak", "Odprto je vsak dan")

    result = backfill_cloze_tts(db_path=db_path, dry_run=True)

    assert result["synthesized"] == 0
    assert call_count == 0


def test_backfill_skips_rows_with_audio(monkeypatch, db_path):
    """Rows that already have both audio kinds are skipped."""
    import app.audio.cloze_tts as cloze_tts_mod

    async def _fake_tts(text, voice="sl-SI-PetraNeural"):
        return b"fake-mp3"

    monkeypatch.setattr(cloze_tts_mod, "generate_tts_audio", _fake_tts)

    from app.srs.database import SRSDatabase

    db = SRSDatabase(db_path)
    _seed_cloze_row(db, "vsak", "Odprto je vsak dan")
    coll_id = db.get_collocation_by_lemma_with_id("vsak")[0]

    # Manually add media rows to simulate already-synthesized
    db.add_media(coll_id, "audio_tts_sentence", "tts_sentence_abc.mp3", "/tmp/s.mp3", "", "s1", 100)
    db.add_media(coll_id, "audio_tts", "tts_vsak.mp3", "/tmp/w.mp3", "", "w1", 100)

    result = backfill_cloze_tts(db_path=db_path, dry_run=False)

    assert result["synthesized"] == 0
    assert result["skipped"] == 1
    assert result["total"] == 1


def test_backfill_skips_rows_without_sentence(monkeypatch, db_path):
    """Cloze rows without a source_sentence are skipped."""
    import app.audio.cloze_tts as cloze_tts_mod

    async def _fake_tts(text, voice="sl-SI-PetraNeural"):
        return b"fake-mp3"

    monkeypatch.setattr(cloze_tts_mod, "generate_tts_audio", _fake_tts)

    from app.srs.database import SRSDatabase

    db = SRSDatabase(db_path)
    _seed_cloze_row(db, "vsak", "")

    result = backfill_cloze_tts(db_path=db_path, dry_run=False)

    assert result["synthesized"] == 0
    # The row is not counted in total because the SQL filters out empty sentences
    assert result["total"] == 0


def test_backfill_respects_limit(monkeypatch, db_path):
    """Limit parameter restricts the number of rows processed."""
    import app.audio.cloze_tts as cloze_tts_mod

    call_count = 0

    async def _fake_tts(text, voice="sl-SI-PetraNeural"):
        nonlocal call_count
        call_count += 1
        return b"fake-mp3"

    monkeypatch.setattr(cloze_tts_mod, "generate_tts_audio", _fake_tts)

    from app.srs.database import SRSDatabase

    db = SRSDatabase(db_path)
    _seed_cloze_row(db, "vsak", "Odprto je vsak dan")
    _seed_cloze_row(db, "kje", "Kje je banka?")

    result = backfill_cloze_tts(db_path=db_path, dry_run=False, limit=1)

    assert result["synthesized"] == 1
    assert result["total"] == 2


def test_backfill_skips_rows_with_null_lemma(db_path):
    """Rows with a NULL lemma are skipped."""
    from app.srs.database import SRSDatabase

    db = SRSDatabase(db_path)

    # Insert a cloze row with NULL lemma directly
    from app.common.guid import compute_guid

    guid = compute_guid("vsak", "sl", "")
    with db._get_conn() as conn:
        conn.execute(
            """INSERT INTO collocations
            (text, translation, language_code, word_count, unit_difficulty,
             source, corpus_frequency, lemma, guid, disambig_key,
             source_sentence, sentence_translation, card_type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("vsak", "every", "sl", 1, 1, "llm", 1, None, guid, "", "Odprto je vsak dan", "", "cloze"),
        )

    result = backfill_cloze_tts(db_path=db_path, dry_run=False)
    assert result["synthesized"] == 0
    assert result["skipped"] == 1
    assert result["total"] == 1


def test_backfill_handles_synthesis_failure(monkeypatch, db_path):
    """Backfill continues past a synthesizer failure."""
    import app.audio.cloze_tts as cloze_tts_mod

    call_count = 0

    async def _broken_tts(text, voice="sl-SI-PetraNeural"):
        nonlocal call_count
        call_count += 1
        msg = f"TTS failed on call {call_count}"
        raise RuntimeError(msg)

    monkeypatch.setattr(cloze_tts_mod, "generate_tts_audio", _broken_tts)

    from app.srs.database import SRSDatabase

    db = SRSDatabase(db_path)
    _seed_cloze_row(db, "vsak", "Odprto je vsak dan")

    # Verify the monkeypatch works by calling the patched function directly
    import asyncio

    try:
        asyncio.run(cloze_tts_mod.generate_tts_audio("test"))
        raise AssertionError("expected RuntimeError")
    except RuntimeError:
        pass

    result = backfill_cloze_tts(db_path=db_path, dry_run=False)

    assert result["synthesized"] == 0, f"expected no synthesized, got {result}"
    assert result["skipped"] == 1, f"expected 1 skipped, got {result}"
    assert result["total"] == 1, f"expected total 1, got {result}"
