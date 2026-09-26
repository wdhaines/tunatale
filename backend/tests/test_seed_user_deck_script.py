"""The seed_user_deck CLI: finds the learner and the destination, refuses the wrong ones."""

from __future__ import annotations

import sqlite3

import pytest

from app.auth.database import AuthDatabase
from app.config import settings
from app.models.syntactic_unit import SyntacticUnit
from app.srs.database import SRSDatabase
from app.storage.store import ContentStore
from scripts.seed_user_deck import main

PASSWORD = "correct horse battery staple"


@pytest.fixture
def env(tmp_path, monkeypatch):
    owner_db = tmp_path / "data" / "tunatale_no.db"
    db = SRSDatabase(str(owner_db))
    ContentStore(str(owner_db))
    db.add_collocation(
        SyntacticUnit(text="hus", translation="house", word_count=1, difficulty=1, source="corpus"), language_code="no"
    )
    monkeypatch.setattr(settings, "database_urls", {"no": f"sqlite:///{owner_db}"})
    monkeypatch.setattr(settings, "user_data_dir", None)
    monkeypatch.setattr(settings, "owner_email", "")
    auth_url = f"sqlite:///{tmp_path / 'auth.db'}"
    monkeypatch.setattr(settings, "auth_database_url", auth_url)
    auth = AuthDatabase(auth_url)
    auth.create_user("owner@example.com", PASSWORD)
    learner = auth.create_user("learner@example.com", PASSWORD)
    return tmp_path / "data" / "users" / str(learner.id) / "tunatale_no.db"


def test_a_dry_run_names_the_destination_and_writes_nothing(env, capsys):
    assert main(["--language", "no", "--email", "learner@example.com"]) == 0
    assert str(env) in capsys.readouterr().out
    assert not env.exists()


def test_apply_seeds_the_learners_deck(env, capsys):
    assert main(["--language", "no", "--email", "learner@example.com", "--apply"]) == 0
    assert "Seeded 1 cards" in capsys.readouterr().out
    assert sqlite3.connect(env).execute("SELECT text FROM collocations").fetchall() == [("hus",)]


def test_a_second_apply_refuses(env, capsys):
    main(["--language", "no", "--email", "learner@example.com", "--apply"])
    assert main(["--language", "no", "--email", "learner@example.com", "--apply"]) == 1
    assert "already exists" in capsys.readouterr().err


def test_positionless_new_cards_are_reported(env, capsys):
    main(["--language", "no", "--email", "learner@example.com", "--apply"])
    assert "no position" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["--language", "no", "--email", "nobody@example.com"], "No account"),
        (["--language", "no", "--email", "owner@example.com"], "OWNER"),
        (["--language", "sl", "--email", "learner@example.com"], "not in DATABASE_URLS"),
    ],
)
def test_refusals(env, capsys, argv, message):
    assert main(argv) == 1
    assert message in capsys.readouterr().err
    assert not env.exists()
