"""The repair_card_audio CLI: dry run by default, --apply writes, one run spans learners."""

from __future__ import annotations

from datetime import datetime

import pytest

from app.cards.media.pipeline import MediaResult
from app.config import settings
from app.models.srs_item import Direction, DirectionState, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.anki_mirror.rollover import anki_today, due_at_rollover_utc
from app.srs.database import SRSDatabase
from scripts.repair_card_audio import main


@pytest.fixture(autouse=True)
def _media_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("app.cards.media.vocab_media._MEDIA_DIR", tmp_path / "media")
    monkeypatch.setattr("app.config.settings.sync_log", str(tmp_path / "sync.log"))


def _seeded(url: str) -> SRSDatabase:
    """`gabii` already met, `iro` not — both voiced wrongly; `gusto` is the user's own."""
    db = SRSDatabase(url)
    for word, source, seen in (("gabii", "cognate", True), ("iro", "base-list", False), ("gusto", "user", False)):
        unit = SyntacticUnit(text=word, translation="gloss", word_count=1, difficulty=1, source=source)
        if seen:
            state = DirectionState(
                direction=Direction.RECOGNITION,
                due_at=due_at_rollover_utc(anki_today()),
                state=SRSState.REVIEW,
                reps=3,
                last_review=datetime.fromisoformat("2026-10-01T12:00:00+00:00"),
            )
            db.upsert_by_guid(unit, "ceb", {Direction.RECOGNITION: state})
        else:
            db.add_collocation(unit, "ceb")
        coll_id = db.get_collocation_id_by_guid(db.get_collocation(word).guid)
        db.add_media(coll_id, "audio_tts", f"tts_{word}.mp3", f"media/tts_{word}.mp3", f"tts_{word}.mp3", "old", 3)
    return db


def _audio(db: SRSDatabase, word: str) -> str | None:
    return db.get_audio_filename(db.get_collocation_id_by_guid(db.get_collocation(word).guid))


class _Fetch:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, word, english, *, audio="full", **kwargs):
        self.calls.append((word, audio))
        if audio == "forvo":
            return MediaResult(audio_status="no_pronunciation")
        return MediaResult(audio_bytes=b"TTS:" + word.encode(), audio_source="tts")


ARGS = ["--language", "ceb", "--source", "base-list", "--source", "cognate", "--forvo-delay", "0"]


def test_dry_run_prints_the_plan_and_writes_nothing(tmp_path, monkeypatch, capsys) -> None:
    url = f"sqlite:///{tmp_path / 'ceb.db'}"
    monkeypatch.setattr(settings, "database_urls", {"ceb": url})
    db = _seeded(url)
    fetch = _Fetch()

    assert main([*ARGS, "--tts-limit", "50"], fetch_fn=fetch) == 0

    out = capsys.readouterr().out
    assert "2 card(s) to redo: 1 already met, 1 not yet." in out
    assert "TTS: at most 2 render(s), 5 characters across the cards already met." in out
    assert fetch.calls == []
    assert _audio(db, "gabii") == "tts_gabii.mp3"


def test_apply_redoes_both_learners_and_renders_each_word_once(tmp_path, capsys) -> None:
    owner_url = f"sqlite:///{tmp_path / 'ceb.db'}"
    learner_url = f"sqlite:///{tmp_path / 'learner2_ceb.db'}"
    owner, learner = _seeded(owner_url), _seeded(learner_url)
    fetch = _Fetch()

    assert main([*ARGS, "--db", owner_url, "--db", learner_url, "--tts-limit", "50", "--apply"], fetch_fn=fetch) == 0

    out = capsys.readouterr().out
    assert out.count("dropped 2, Forvo gave 0; awaiting TTS: 1 met, 1 not yet.") == 2
    assert "Sync the language to carry this to Anki." in out
    # Both learners hold the SAME new file for a word, from ONE render of it.
    for word in ("gabii", "iro"):
        assert _audio(owner, word) == _audio(learner, word)
        assert _audio(owner, word).startswith(f"tts_{word}_")
        assert fetch.calls.count((word, "full")) == 1
        assert fetch.calls.count((word, "forvo")) == 1
    # The user's own card was never named.
    assert _audio(owner, "gusto") == "tts_gusto.mp3"


def test_without_a_tts_limit_only_the_free_half_runs(tmp_path) -> None:
    url = f"sqlite:///{tmp_path / 'ceb.db'}"
    db = _seeded(url)
    fetch = _Fetch()

    assert main([*ARGS, "--db", url, "--apply"], fetch_fn=fetch) == 0

    assert {audio for _word, audio in fetch.calls} == {"forvo"}
    assert _audio(db, "gabii") is None


def test_naming_no_cards_is_refused(capsys) -> None:
    with pytest.raises(SystemExit):
        main(["--language", "ceb"])
    assert "name the cards to redo" in capsys.readouterr().err
