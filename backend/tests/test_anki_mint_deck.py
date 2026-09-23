"""TT-minted notes go into the language's MINT deck (tunatale-w4m7.8, step 2).

User decision, 2026-09-23: new TT-minted Tagalog cards go into a subdeck,
`2. Pimsleur Tagalog::TunaTale`, while TT keeps READING the whole Pimsleur tree
(step 1). So a language may register a mint deck distinct from its read deck;
without one, minting targets the read deck exactly as before (sl/no unchanged).
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from app.languages import _CONFIGS, discover, get_mint_deck_name
from app.plugins.anki_sync.sync import main
from app.srs.database import SRSDatabase
from tests.test_anki_sync_main import _patch_all_refreshes


def test_a_language_without_a_mint_deck_mints_into_its_read_deck():
    assert get_mint_deck_name("sl", default="0. Slovene") == "0. Slovene"
    assert get_mint_deck_name("no", default="whatever deck") == "whatever deck"


def test_tagalog_mints_into_the_tunatale_subdeck():
    assert get_mint_deck_name("tl", default="2. Pimsleur Tagalog") == "2. Pimsleur Tagalog::TunaTale"


def test_an_unknown_language_falls_back_to_the_default():
    assert get_mint_deck_name("xx", default="D") == "D"


class _FakeSettings:
    anki_collection_path = "unused"
    anki_deck_name = "0. Slovene"
    anki_model_name = "Slovene Vocabulary"
    target_language = "sl"
    database_url = "sqlite:///:memory:"


def _run_main(anki_conn, tt_db, tmp_path):
    @contextmanager
    def fake_safe_open(path, mode):
        yield type("Ctx", (), {"conn": anki_conn})()

    return main(
        argv=[],
        _settings=_FakeSettings(),
        _safe_open_fn=fake_safe_open,
        _sync_log_path=tmp_path / "sync.log",
        _db=tt_db,
    )


def _unlinked(tt_db):
    from app.models.syntactic_unit import SyntacticUnit

    tt_db.add_collocation(
        SyntacticUnit(text="oprostiti", translation="to excuse", word_count=1, difficulty=1, source="user")
    )


def test_main_mints_into_the_registered_mint_subdeck(tmp_path, monkeypatch):
    from tests._helpers.anki_sync_create_new import _make_dual_collection_conn

    discover()
    monkeypatch.setattr(_CONFIGS["sl"], "mint_deck_name", "0. Slovene::TunaTale")
    _patch_all_refreshes(monkeypatch)
    anki_conn = _make_dual_collection_conn()
    # The modern decks table stores the subdeck separator as \x1f.
    anki_conn.execute("INSERT INTO decks VALUES (23456, '0. Slovene\x1fTunaTale', 0, 0, x'')")
    tt_db = SRSDatabase(":memory:")
    _unlinked(tt_db)

    assert _run_main(anki_conn, tt_db, tmp_path) == 0
    dids = {r[0] for r in anki_conn.execute("SELECT did FROM cards").fetchall()}
    assert dids == {23456}


def test_main_without_a_mint_deck_still_mints_into_the_read_deck(tmp_path, monkeypatch):
    from tests._helpers.anki_sync_create_new import _make_dual_collection_conn

    _patch_all_refreshes(monkeypatch)
    anki_conn = _make_dual_collection_conn()
    tt_db = SRSDatabase(":memory:")
    _unlinked(tt_db)

    assert _run_main(anki_conn, tt_db, tmp_path) == 0
    assert {r[0] for r in anki_conn.execute("SELECT did FROM cards").fetchall()} == {12345}


def test_a_missing_mint_subdeck_fails_loudly(tmp_path, monkeypatch):
    # The user creates the subdeck in Anki; until then minting must not fall
    # back to the parent silently.
    from tests._helpers.anki_sync_create_new import _make_dual_collection_conn

    discover()
    monkeypatch.setattr(_CONFIGS["sl"], "mint_deck_name", "0. Slovene::TunaTale")
    _patch_all_refreshes(monkeypatch)
    anki_conn = _make_dual_collection_conn()
    tt_db = SRSDatabase(":memory:")
    _unlinked(tt_db)

    with pytest.raises(ValueError, match="TunaTale"):
        _run_main(anki_conn, tt_db, tmp_path)
