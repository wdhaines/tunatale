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


# --- A second run is free (tunatale-2f5a) -----------------------------------
# The repair was started once as a background task with a ten-minute limit and
# took about 35. Had it been killed, the obvious recovery — run it again — would
# have dropped every clip it had just stored and paid for the same TTS twice.


def test_a_second_identical_run_fetches_nothing_and_renames_nothing(tmp_path, capsys) -> None:
    url = f"sqlite:///{tmp_path / 'ceb.db'}"
    db = _seeded(url)
    args = [*ARGS, "--db", url, "--tts-limit", "50", "--apply"]
    assert main(args, fetch_fn=_Fetch()) == 0
    first = {word: _audio(db, word) for word in ("gabii", "iro")}
    assert all(name is not None and name.startswith("tts_") for name in first.values())
    capsys.readouterr()

    again = _Fetch()
    assert main(args, fetch_fn=again) == 0

    out = capsys.readouterr().out
    assert again.calls == []
    assert {word: _audio(db, word) for word in ("gabii", "iro")} == first
    assert "0 card(s) to redo: 0 already met, 0 not yet." in out
    assert "2 already redone, skipped." in out
    assert "TTS: at most 0 render(s), 0 characters across the cards already met." in out


def test_the_dry_run_counts_what_it_would_skip(tmp_path, capsys) -> None:
    url = f"sqlite:///{tmp_path / 'ceb.db'}"
    _seeded(url)
    assert main([*ARGS, "--db", url, "--tts-limit", "50", "--apply"], fetch_fn=_Fetch()) == 0
    capsys.readouterr()

    fetch = _Fetch()
    assert main([*ARGS, "--db", url, "--tts-limit", "50"], fetch_fn=fetch) == 0

    out = capsys.readouterr().out
    assert fetch.calls == []
    assert "0 card(s) to redo: 0 already met, 0 not yet." in out
    assert "2 already redone, skipped." in out


def test_a_first_run_skips_nothing_and_says_so(tmp_path, capsys) -> None:
    url = f"sqlite:///{tmp_path / 'ceb.db'}"
    _seeded(url)

    assert main([*ARGS, "--db", url, "--tts-limit", "50"], fetch_fn=_Fetch()) == 0

    out = capsys.readouterr().out
    assert "2 card(s) to redo: 1 already met, 1 not yet." in out
    assert "0 already redone, skipped." in out
    assert "0 of them have no audio now" in out


def test_force_redoes_the_cards_a_run_already_redid(tmp_path, capsys) -> None:
    url = f"sqlite:///{tmp_path / 'ceb.db'}"
    _seeded(url)
    args = [*ARGS, "--db", url, "--tts-limit", "50", "--apply"]
    assert main(args, fetch_fn=_Fetch()) == 0
    capsys.readouterr()

    again = _Fetch()
    assert main([*args, "--force"], fetch_fn=again) == 0

    out = capsys.readouterr().out
    assert "2 card(s) to redo: 1 already met, 1 not yet." in out
    assert "0 already redone, skipped." in out
    for word in ("gabii", "iro"):
        assert again.calls.count((word, "forvo")) == 1
        assert again.calls.count((word, "full")) == 1


def test_only_the_cards_not_yet_redone_are_touched(tmp_path, capsys) -> None:
    url = f"sqlite:///{tmp_path / 'ceb.db'}"
    db = _seeded(url)
    one = ["--language", "ceb", "--word", "gabii", "--forvo-delay", "0", "--db", url, "--tts-limit", "50", "--apply"]
    assert main(one, fetch_fn=_Fetch()) == 0
    gabii = _audio(db, "gabii")
    assert gabii is not None and gabii.startswith("tts_gabii_")
    capsys.readouterr()

    rest = _Fetch()
    assert main([*ARGS, "--db", url, "--tts-limit", "50", "--apply"], fetch_fn=rest) == 0

    out = capsys.readouterr().out
    assert "1 card(s) to redo: 0 already met, 1 not yet." in out
    assert "1 already redone, skipped." in out
    # The estimate is for the cards to redo, not for every card named.
    assert "TTS: at most 1 render(s), 0 characters across the cards already met." in out
    assert [word for word, _audio_mode in rest.calls if word == "gabii"] == []
    assert _audio(db, "gabii") == gabii
    assert _audio(db, "iro").startswith("tts_iro_")


def test_a_card_forvo_had_nothing_for_is_looked_up_again_and_the_plan_says_so(tmp_path, capsys) -> None:
    # No TTS budget: after the first run both cards have NO audio row, which is
    # also what a card never visited looks like. Revisiting costs one Forvo
    # request and no TTS, so the run does it and the plan says how many.
    url = f"sqlite:///{tmp_path / 'ceb.db'}"
    db = _seeded(url)
    assert main([*ARGS, "--db", url, "--apply"], fetch_fn=_Fetch()) == 0
    assert _audio(db, "gabii") is None and _audio(db, "iro") is None
    capsys.readouterr()

    again = _Fetch()
    assert main([*ARGS, "--db", url, "--apply"], fetch_fn=again) == 0

    out = capsys.readouterr().out
    assert "2 card(s) to redo: 1 already met, 1 not yet." in out
    assert "0 already redone, skipped." in out
    assert "2 of them have no audio now: Forvo is asked again for each, and no TTS is spent on that." in out
    assert sorted(again.calls) == [("gabii", "forvo"), ("iro", "forvo")]


def test_naming_no_cards_is_refused(capsys) -> None:
    with pytest.raises(SystemExit):
        main(["--language", "ceb"])
    assert "name the cards to redo" in capsys.readouterr().err
