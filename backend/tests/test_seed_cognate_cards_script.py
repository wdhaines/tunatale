"""The seed_cognate_cards CLI: dry run writes nothing; --apply mints, then seeds (u8nz.7)."""

from __future__ import annotations

import json

import pytest

from app.config import settings
from app.models.srs_item import Direction, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.database import SRSDatabase
from scripts.seed_cognate_cards import main


@pytest.fixture
def dbs(tmp_path, monkeypatch):
    source_url, target_url = f"sqlite:///{tmp_path / 'tl.db'}", f"sqlite:///{tmp_path / 'ceb.db'}"
    monkeypatch.setattr(settings, "database_urls", {"tl": source_url, "ceb": target_url})
    source = SRSDatabase(source_url)
    for text, gloss in [("tubig", "water"), ("Amerikana", "coat"), ("kumain", "to eat")]:
        source.add_collocation(SyntacticUnit(text, gloss, 1, 1, "test"), "tl")
    item = source.get_collocation("tubig")
    for d in Direction:
        ds = item.directions[d]
        ds.state, ds.stability, ds.difficulty, ds.reps = SRSState.REVIEW, 40.0, 4.2, 3
        source.update_direction(item.guid, d, ds)
    dictionary = tmp_path / "ceb.jsonl"
    dictionary.write_text(
        "\n".join(
            json.dumps({"word": w, "senses": [{"glosses": [g]}]})
            for w, g in [("tubig", "water"), ("Amerikana", "American woman")]
        ),
        encoding="utf-8",
    )
    return SRSDatabase(target_url), dictionary


def test_a_dry_run_prints_the_plan_and_writes_nothing(dbs, capsys):
    target, dictionary = dbs
    assert main(["--dictionary", str(dictionary)]) == 0
    out = capsys.readouterr().out
    assert "cognate" in out and "tubig" in out
    assert "FALSE FRIENDS (1)" in out and "Amerikana" in out
    assert "Dry run" in out
    assert target.count_collocations() == 0


def test_apply_mints_then_seeds_after_the_sync(dbs, capsys):
    target, dictionary = dbs
    main(["--dictionary", str(dictionary), "--apply"])
    assert "wait for their card to be minted" in capsys.readouterr().out
    tubig = target.get_collocation("tubig")
    assert tubig.directions[Direction.RECOGNITION].state == SRSState.NEW
    assert target.get_collocation("Amerikana") is None

    target.set_anki_ids(tubig.guid, 1, {Direction.RECOGNITION: 10, Direction.PRODUCTION: 11})  # the sync
    main(["--dictionary", str(dictionary), "--apply"])
    assert "Seeded 2 direction(s)" in capsys.readouterr().out
    assert target.get_collocation("tubig").directions[Direction.PRODUCTION].state == SRSState.REVIEW

    main(["--dictionary", str(dictionary), "--apply"])
    assert "2 direction(s) already have a schedule" in capsys.readouterr().out
