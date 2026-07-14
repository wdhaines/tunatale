"""Tests for the FSRS fallback log (cards with missing/malformed cards.data)."""

import json

from app.models.srs_item import SRSState
from app.plugins.anki_sync.sqlite_reader import parse_fsrs_data


def test_fallback_creates_log_entry(tmp_path):
    log = tmp_path / "fallback.log"
    state = parse_fsrs_data(card_id=77, ord=0, data_str="", queue=0, reps=0, lapses=0, fallback_log_path=log)
    assert log.exists()
    assert "77" in log.read_text()
    assert state.stability == 1.0
    assert state.difficulty == 5.0
    assert state.state == SRSState.NEW


def test_fallback_log_append_only(tmp_path):
    log = tmp_path / "fallback.log"
    parse_fsrs_data(card_id=10, ord=0, data_str="", queue=0, reps=0, lapses=0, fallback_log_path=log)
    parse_fsrs_data(card_id=20, ord=0, data_str="", queue=0, reps=0, lapses=0, fallback_log_path=log)
    lines = log.read_text().strip().splitlines()
    assert "10" in lines
    assert "20" in lines
    assert len(lines) == 2


def test_valid_data_does_not_write_log(tmp_path):
    log = tmp_path / "fallback.log"
    parse_fsrs_data(
        card_id=99,
        ord=0,
        data_str=json.dumps({"s": 10.0, "d": 5.0}),
        queue=2,
        reps=3,
        lapses=0,
        fallback_log_path=log,
    )
    assert not log.exists()


def test_fallback_log_parent_created_automatically(tmp_path):
    log = tmp_path / "nested" / "dir" / "fallback.log"
    parse_fsrs_data(card_id=1, ord=0, data_str="", queue=0, reps=0, lapses=0, fallback_log_path=log)
    assert log.exists()
