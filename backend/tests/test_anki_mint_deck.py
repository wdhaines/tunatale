"""TT-minted notes go into the language's MINT deck (tunatale-w4m7.8, step 2).

User decision, 2026-09-23: new TT-minted Tagalog cards go into a subdeck,
`2. Pimsleur Tagalog::TunaTale`, while TT keeps READING the whole Pimsleur tree
(step 1). So a language may register a mint deck distinct from its read deck;
without one, minting targets the read deck exactly as before (sl/no unchanged).

Decks are registered by Anki deck ID (2026-11-05): the user renamed
`3. Bisaya` to `1. Binisayâ` and Anki kept its id, so TT looks up the current
name of each id once the collection is open.
"""

from __future__ import annotations

from contextlib import contextmanager

from app.languages import _CONFIGS, discover, get_mint_deck_id
from app.plugins.anki_sync.sync import main
from app.srs.database import SRSDatabase
from tests.test_anki_sync_main import _patch_all_refreshes


def test_a_language_without_a_mint_deck_mints_into_its_read_deck():
    assert get_mint_deck_id("sl") is None
    assert get_mint_deck_id("no") is None


def test_tagalog_mints_into_the_tunatale_subdeck():
    assert get_mint_deck_id("tl") == 1790175595142  # "2. Pimsleur Tagalog::TunaTale"


def test_an_unknown_language_has_no_mint_deck():
    assert get_mint_deck_id("xx") is None


class _FakeSettings:
    anki_collection_path = "unused"
    anki_deck_name = "0. Slovene"
    anki_deck_id = None
    anki_model_name = "Slovene Vocabulary"
    target_language = "sl"
    database_url = "sqlite:///:memory:"


def _run_main(anki_conn, tt_db, tmp_path, settings=None):
    @contextmanager
    def fake_safe_open(path, mode):
        yield type("Ctx", (), {"conn": anki_conn})()

    return main(
        argv=[],
        _settings=settings or _FakeSettings(),
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
    monkeypatch.setattr(_CONFIGS["sl"], "mint_deck_id", 23456)
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


def test_a_missing_mint_subdeck_fails_loudly(tmp_path, monkeypatch, capsys):
    # The user creates the subdeck in Anki; until then minting must not fall
    # back to the parent silently. A registered id absent from the collection
    # stops the sync before any phase runs.
    from tests._helpers.anki_sync_create_new import _make_dual_collection_conn

    discover()
    monkeypatch.setattr(_CONFIGS["sl"], "mint_deck_id", 23456)
    _patch_all_refreshes(monkeypatch)
    anki_conn = _make_dual_collection_conn()
    tt_db = SRSDatabase(":memory:")
    _unlinked(tt_db)

    assert _run_main(anki_conn, tt_db, tmp_path) == 1
    assert "23456" in capsys.readouterr().err
    assert anki_conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0] == 0


def _renamed_collection():
    """The user's 2026-11-05 shape: the read deck and its mint subdeck were
    renamed in Anki and kept their ids."""
    from tests._helpers.anki_sync_create_new import _make_dual_collection_conn

    anki_conn = _make_dual_collection_conn()
    anki_conn.execute("UPDATE decks SET name='1. Binisay\u00e2' WHERE id=12345")
    anki_conn.execute("INSERT INTO decks VALUES (23456, '1. Binisay\u00e2\x1fTunaTale', 0, 0, x'')")
    return anki_conn


def test_main_reads_and_mints_by_deck_id_after_a_rename(tmp_path, monkeypatch):
    discover()
    monkeypatch.setattr(_CONFIGS["sl"], "mint_deck_id", 23456)
    _patch_all_refreshes(monkeypatch)
    anki_conn = _renamed_collection()
    tt_db = SRSDatabase(":memory:")
    _unlinked(tt_db)
    settings = _FakeSettings()
    settings.anki_deck_name = None  # a registry-wired language carries only the id
    settings.anki_deck_id = 12345

    assert _run_main(anki_conn, tt_db, tmp_path, settings) == 0
    assert {r[0] for r in anki_conn.execute("SELECT did FROM cards").fetchall()} == {23456}


def test_main_reads_into_a_renamed_read_deck_when_there_is_no_mint_deck(tmp_path, monkeypatch):
    _patch_all_refreshes(monkeypatch)
    anki_conn = _renamed_collection()
    tt_db = SRSDatabase(":memory:")
    _unlinked(tt_db)
    settings = _FakeSettings()
    settings.anki_deck_name = None
    settings.anki_deck_id = 12345

    assert _run_main(anki_conn, tt_db, tmp_path, settings) == 0
    assert {r[0] for r in anki_conn.execute("SELECT did FROM cards").fetchall()} == {12345}


def test_a_missing_read_deck_id_fails_loudly(tmp_path, monkeypatch, capsys):
    from tests._helpers.anki_sync_create_new import _make_dual_collection_conn

    _patch_all_refreshes(monkeypatch)
    anki_conn = _make_dual_collection_conn()
    tt_db = SRSDatabase(":memory:")
    _unlinked(tt_db)
    settings = _FakeSettings()
    settings.anki_deck_name = None
    settings.anki_deck_id = 999

    assert _run_main(anki_conn, tt_db, tmp_path, settings) == 1
    assert "999" in capsys.readouterr().err
    assert anki_conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0] == 0
