"""The reconcile runs as the language it was ASKED to sync (w4m7.8 finding).

peer_sync hands main() a per-language copy of settings (`_tt_settings`), but the
engine read the GLOBAL `settings.target_language`, which is the process's .env
default. On a live instance whose default is Norwegian, a Slovene or Tagalog
sync would key reverse-imported notes, guids and media as Norwegian. The first
Tagalog sync reverse-imports all 609 Pimsleur notes, so this must hold first.
"""

from __future__ import annotations

from contextlib import contextmanager

from app.config import settings
from app.plugins.anki_sync.sync import main
from app.srs.database import SRSDatabase
from tests.test_anki_sync_main import _patch_all_refreshes


def test_a_reverse_imported_note_takes_the_synced_language_not_the_global_default(tmp_path, monkeypatch):
    from tests._helpers.anki_sync_create_new import _make_dual_collection_conn

    _patch_all_refreshes(monkeypatch)
    # The process default is Norwegian; this sync is for Slovene.
    monkeypatch.setattr(settings, "target_language", "no")

    anki_conn = _make_dual_collection_conn()
    fields = ["voda", "water", "", "", "", "", ""]
    anki_conn.execute(
        "INSERT INTO notes (id, guid, mid, mod, usn, tags, flds, sfld, csum, flags, data) "
        "VALUES (4242, 'g-voda', 1000001, 0, 0, '', ?, 'voda', 0, 0, '')",
        ("\x1f".join(fields),),
    )
    anki_conn.execute(
        "INSERT INTO cards (id, nid, did, ord, mod, usn, type, queue, due, ivl, factor, reps, lapses, left, odue, odid, flags, data) "
        "VALUES (42420, 4242, 12345, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, '')"
    )
    anki_conn.commit()

    class SloveneSync:
        anki_collection_path = "unused"
        anki_deck_name = "0. Slovene"
        anki_model_name = "Slovene Vocabulary"
        target_language = "sl"
        database_url = "sqlite:///:memory:"

    @contextmanager
    def fake_safe_open(path, mode):
        yield type("Ctx", (), {"conn": anki_conn})()

    tt_db = SRSDatabase(":memory:")
    assert (
        main(
            argv=[], _settings=SloveneSync(), _safe_open_fn=fake_safe_open, _sync_log_path=tmp_path / "s.log", _db=tt_db
        )
        == 0
    )
    item = tt_db.get_collocation("voda")
    assert item is not None, "the Anki-only note was not reverse-imported"
    # The GUID folds in the language; the row must be keyed as Slovene.
    from app.common.guid import compute_guid

    assert compute_guid("voda", "sl", "") != compute_guid("voda", "no", ""), "control: the guids must differ"
    assert item.guid == compute_guid("voda", "sl", ""), "keyed as the process default, not the synced language"
