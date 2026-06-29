"""Integration tests for the Stage 2a import_seed CLI (read-only Anki path)."""

import sqlite3
from contextlib import closing
from unittest.mock import patch

import pytest

from app.anki.import_seed import import_seed
from app.anki.safety import _sha256_file


def _run(fake_anki_db, tmp_path, **kwargs):
    """Helper: run import_seed against the fake collection with temp paths."""
    defaults = dict(
        anki_collection_path=fake_anki_db,
        anki_backup_dir=tmp_path / "bak",
        anki_media_path=tmp_path / "fake_media",
        deck_name="0. Slovene",
        tunatale_db_path=str(tmp_path / "tunatale.db"),
        media_dir=tmp_path / "media",
        fallback_log_path=tmp_path / "fallback.log",
    )
    defaults.update(kwargs)
    return import_seed(**defaults)


class TestBasicImport:
    def test_creates_five_parent_rows(self, fake_anki_db, tmp_path):
        _run(fake_anki_db, tmp_path)
        with closing(sqlite3.connect(str(tmp_path / "tunatale.db"))) as db:
            count = db.execute("SELECT COUNT(*) FROM collocations").fetchone()[0]
        assert count == 5

    def test_creates_ten_direction_rows(self, fake_anki_db, tmp_path):
        _run(fake_anki_db, tmp_path)
        with closing(sqlite3.connect(str(tmp_path / "tunatale.db"))) as db:
            count = db.execute("SELECT COUNT(*) FROM collocation_directions").fetchone()[0]
        assert count == 10

    def test_fsrs_state_preserved(self, fake_anki_db, tmp_path):
        """Recognition cards with FSRS data have stability > 1.0 (not fallback)."""
        _run(fake_anki_db, tmp_path)
        with closing(sqlite3.connect(str(tmp_path / "tunatale.db"))) as db:
            row = db.execute(
                "SELECT stability FROM collocation_directions WHERE direction='recognition' LIMIT 1"
            ).fetchone()
        # note 1001 has stability=10.5 from cards.data
        assert row[0] > 1.0

    def test_suspended_card_is_suspended_in_tunatale(self, fake_anki_db, tmp_path):
        """Note 1003 production card is suspended in fake collection."""
        _run(fake_anki_db, tmp_path)
        with closing(sqlite3.connect(str(tmp_path / "tunatale.db"))) as db:
            # Find the "miza" row and check its production direction state
            row = db.execute(
                """SELECT cd.state FROM collocations c
                   JOIN collocation_directions cd ON cd.collocation_id = c.id
                   WHERE c.text = 'miza' AND cd.direction = 'production'"""
            ).fetchone()
        assert row[0] == "suspended"

    def test_returns_summary_dict(self, fake_anki_db, tmp_path):
        result = _run(fake_anki_db, tmp_path)
        assert "new_parents" in result
        assert result["new_parents"] == 5


class TestIdempotency:
    def test_second_run_adds_no_new_parents(self, fake_anki_db, tmp_path):
        _run(fake_anki_db, tmp_path)
        db_path = str(tmp_path / "tunatale.db")
        with closing(sqlite3.connect(db_path)) as db:
            before = db.execute("SELECT COUNT(*) FROM collocations").fetchone()[0]
        _run(fake_anki_db, tmp_path)
        with closing(sqlite3.connect(db_path)) as db:
            after = db.execute("SELECT COUNT(*) FROM collocations").fetchone()[0]
        assert before == after == 5

    def test_second_run_adds_no_new_directions(self, fake_anki_db, tmp_path):
        _run(fake_anki_db, tmp_path)
        db_path = str(tmp_path / "tunatale.db")
        with closing(sqlite3.connect(db_path)) as db:
            before = db.execute("SELECT COUNT(*) FROM collocation_directions").fetchone()[0]
        _run(fake_anki_db, tmp_path)
        with closing(sqlite3.connect(db_path)) as db:
            after = db.execute("SELECT COUNT(*) FROM collocation_directions").fetchone()[0]
        assert before == after == 10

    def test_guid_edited_after_import_falls_back_to_anki_note_id(self, fake_anki_db, tmp_path):
        """Re-import skips via anki_note_id when the stored guid no longer matches.

        Mirrors "DisambigKey cleared or Slovene field edited after import": the TT
        row's guid drifts from what the (unchanged) Anki note recomputes to, so the
        guid lookup misses, but the anki_note_id fallback still finds the row and
        skips it instead of minting a duplicate (import_seed.py:310-318).
        """
        _run(fake_anki_db, tmp_path)
        db_path = str(tmp_path / "tunatale.db")
        with closing(sqlite3.connect(db_path)) as db:
            db.execute("UPDATE collocations SET guid = 'bogus-' || id")
            db.commit()
        result = _run(fake_anki_db, tmp_path)
        assert result["new_parents"] == 0
        assert result["skipped_guid_collisions"] == 5
        with closing(sqlite3.connect(db_path)) as db:
            count = db.execute("SELECT COUNT(*) FROM collocations").fetchone()[0]
        assert count == 5


class TestNoAnkiMutation:
    def test_source_sha256_unchanged(self, fake_anki_db, tmp_path):
        pre = _sha256_file(fake_anki_db)
        _run(fake_anki_db, tmp_path)
        assert _sha256_file(fake_anki_db) == pre

    def test_notes_guid_values_unchanged(self, fake_anki_db, tmp_path):
        with closing(sqlite3.connect(str(fake_anki_db))) as orig_conn:
            orig_guids = {r[0] for r in orig_conn.execute("SELECT guid FROM notes").fetchall()}
        _run(fake_anki_db, tmp_path)
        with closing(sqlite3.connect(str(fake_anki_db))) as post_conn:
            post_guids = {r[0] for r in post_conn.execute("SELECT guid FROM notes").fetchall()}
        assert orig_guids == post_guids

    def test_backup_created_before_import(self, fake_anki_db, tmp_path):
        backup_dir = tmp_path / "bak"
        _run(fake_anki_db, tmp_path, anki_backup_dir=backup_dir)
        backups = list(backup_dir.glob("*.bak_*"))
        assert len(backups) == 1


class TestLemmaPopulation:
    """Step 1 fix: Anki-imported single-word cards get lemma = lowercased text."""

    def test_single_word_card_gets_lemma_on_import(self, tmp_path):
        """Importing a single-word Anki note populates lemma for that card."""
        import sqlite3 as sq3

        from app.srs.database import SRSDatabase
        from tests.conftest import build_minimal_anki_db

        db_path = build_minimal_anki_db(tmp_path)
        # Change note 1001 to "zdravo" so we can assert get_collocation_by_lemma
        conn = sq3.connect(str(db_path))
        conn.execute("UPDATE notes SET flds = ?, sfld = ? WHERE id = 1001", ("zdravo\x1fhello", "zdravo"))
        conn.commit()
        conn.close()

        _run(db_path, tmp_path)
        db = SRSDatabase(str(tmp_path / "tunatale.db"))
        item = db.get_collocation_by_lemma("zdravo")
        assert item is not None
        assert item.syntactic_unit.text == "zdravo"

    def test_b_then_i_pattern_splits_l2_and_gloss(self, tmp_path):
        """Layer 31: import_seed recognises `<b>L2</b><br><i>EN</i>` Front fields
        and splits them: text=L2, translation=EN. Previously these were imported
        as text='L2EN' (concatenated) with translation pulled from the wrong field.
        """
        import sqlite3 as sq3

        from app.srs.database import SRSDatabase
        from tests.conftest import build_minimal_anki_db

        db_path = build_minimal_anki_db(tmp_path)
        conn = sq3.connect(str(db_path))
        conn.execute(
            "UPDATE notes SET flds = ?, sfld = ? WHERE id = 1001",
            ("<b>nič</b><br><i>nothing</i>\x1f[sound:sl_nic.mp3][nətʃ]", "nič"),
        )
        conn.commit()
        conn.close()

        _run(db_path, tmp_path)
        db = SRSDatabase(str(tmp_path / "tunatale.db"))
        item = db.get_collocation("nič")
        assert item is not None, "expected text='nič' (not 'ničnothing')"
        assert item.syntactic_unit.translation == "nothing"


class TestDryRun:
    def test_dry_run_rolls_back_tunatale_writes(self, fake_anki_db, tmp_path):
        _run(fake_anki_db, tmp_path, dry_run=True)
        db_path = str(tmp_path / "tunatale.db")
        with closing(sqlite3.connect(db_path)) as db:
            count = db.execute("SELECT COUNT(*) FROM collocations").fetchone()[0]
        assert count == 0

    def test_dry_run_still_creates_backup(self, fake_anki_db, tmp_path):
        backup_dir = tmp_path / "bak"
        _run(fake_anki_db, tmp_path, dry_run=True, anki_backup_dir=backup_dir)
        assert any(backup_dir.glob("*.bak_*"))


class TestGuidCollisionSkip:
    def test_guid_collision_with_different_text_is_skipped(self, fake_anki_db, tmp_path):
        """If TunaTale already has a row with same GUID but different text, skip + log."""

        from app.common.guid import compute_guid
        from app.srs.database import SRSDatabase

        db_path = str(tmp_path / "tunatale.db")
        # Pre-insert a row with the GUID for "banka" but different text
        guid = compute_guid("banka", "sl")
        db = SRSDatabase(db_path)
        # Insert via raw SQL to force different text with same GUID
        with db._get_conn() as conn:
            conn.execute(
                "INSERT INTO collocations (text, translation, language_code, word_count, unit_difficulty, source, corpus_frequency, guid)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("DIFFERENT_TEXT", "bank", "sl", 1, 1, "corpus", 0, guid),
            )
            conn.execute(
                "INSERT INTO collocation_directions (collocation_id, direction, due_at) VALUES (last_insert_rowid(), 'recognition', strftime('%Y-%m-%dT04:00:00+00:00', 'now'))"
            )
            conn.execute(
                "INSERT INTO collocation_directions (collocation_id, direction, due_at) VALUES ((SELECT id FROM collocations WHERE guid=?), 'production', strftime('%Y-%m-%dT04:00:00+00:00', 'now'))",
                (guid,),
            )
            db._commit(conn)

        import io

        capture = io.StringIO()
        with patch("sys.stdout", capture):
            result = _run(fake_anki_db, tmp_path)

        assert result["skipped_guid_collisions"] >= 1
        # Verify the pre-existing row is untouched
        with closing(sqlite3.connect(db_path)) as db2:
            row = db2.execute("SELECT text FROM collocations WHERE guid = ?", (guid,)).fetchone()
        assert row[0] == "DIFFERENT_TEXT"


class TestMissingCardDirection:
    def test_note_with_only_recognition_card_creates_only_recognition_direction(self, tmp_path):
        """A note with only a recognition card in Anki must produce only a
        recognition direction in TT — no phantom production row.

        Regression: TT used to default-fill the missing direction with a NEW
        DirectionState, which polluted the learning/relearning count for any
        single-template notetype (e.g. phonics on the "Basic" notetype) and
        left orphan rows with `anki_card_id IS NULL` that downstream sync
        could never clean up.
        """
        import sqlite3 as sq3

        from tests.conftest import build_minimal_anki_db

        # Build DB then delete one production card (note 1001, card id=10010+1=10011)
        db_path = build_minimal_anki_db(tmp_path)
        with closing(sq3.connect(str(db_path))) as conn:
            conn.execute("DELETE FROM cards WHERE id = ?", (1001 * 10 + 1,))
            conn.commit()

        _run(db_path, tmp_path)
        with closing(sq3.connect(str(tmp_path / "tunatale.db"))) as tdb:
            tdb.row_factory = sq3.Row
            rows = tdb.execute(
                "SELECT direction FROM collocation_directions"
                " WHERE collocation_id = (SELECT id FROM collocations WHERE text='banka')"
            ).fetchall()
        directions = {r["direction"] for r in rows}
        assert directions == {"recognition"}, f"Expected only recognition, got {directions}"


class TestSettingsDefaults:
    def test_all_settings_defaults_used_when_none_passed(self, fake_anki_db, tmp_path, monkeypatch):
        """When all optional params are None, every setting is read from the settings object."""
        from app.anki import import_seed as mod

        fake_settings = type(
            "S",
            (),
            {
                "anki_deck_name": "0. Slovene",
                "target_language": "sl",
                "anki_collection_path": fake_anki_db,
                "anki_media_path": tmp_path / "no_media",
                "anki_backup_dir": tmp_path / "bak",
                "database_url": f"sqlite:///{tmp_path / 'tt.db'}",
                "media_dir": tmp_path / "media",
                "anki_fallback_log": tmp_path / "fallback.log",
            },
        )()
        monkeypatch.setattr(mod, "settings", fake_settings)
        result = import_seed()  # all None → all from settings
        assert result["new_parents"] == 5

    def test_raises_when_deck_not_found(self, fake_anki_db, tmp_path):
        """import_seed raises RuntimeError when deck is not in collection."""
        with pytest.raises(RuntimeError, match="not found"):
            _run(fake_anki_db, tmp_path, deck_name="Nonexistent Deck")


class TestMediaImport:
    def _build_db_with_sound(self, tmp_path):
        import sqlite3 as sq3

        from tests.conftest import build_minimal_anki_db

        anki_dir = tmp_path / "anki"
        anki_dir.mkdir(exist_ok=True)
        db_path = build_minimal_anki_db(anki_dir)
        conn = sq3.connect(str(db_path))
        conn.execute("UPDATE notes SET flds = ? WHERE id = 1001", ("[sound:sl_banka.mp3]\x1fbank",))
        conn.commit()
        conn.close()
        return db_path

    def test_copies_media_when_anki_media_dir_exists(self, tmp_path):
        """Media files referenced in notes are copied to media_dir."""
        db_path = self._build_db_with_sound(tmp_path)
        anki_media_path = tmp_path / "anki" / "media"
        anki_media_path.mkdir()
        (anki_media_path / "sl_banka.mp3").write_bytes(b"fake audio")
        media_dir = tmp_path / "tunatale_media"
        result = _run(db_path, tmp_path, anki_media_path=anki_media_path, media_dir=media_dir)
        assert result["new_media"] >= 1
        assert (media_dir / "sl_banka.mp3").exists()

    def test_skips_media_file_not_present_in_anki_dir(self, tmp_path):
        """If referenced media file is missing from Anki dir, it is silently skipped."""
        db_path = self._build_db_with_sound(tmp_path)
        anki_media_path = tmp_path / "anki" / "media"
        anki_media_path.mkdir()
        # sl_banka.mp3 is referenced but NOT created → missing file → continue
        result = _run(db_path, tmp_path, anki_media_path=anki_media_path, media_dir=tmp_path / "media")
        assert result["new_media"] == 0

    def test_skips_media_already_imported(self, tmp_path):
        """Second run does not re-copy already-imported media files."""
        db_path = self._build_db_with_sound(tmp_path)
        anki_media_path = tmp_path / "anki" / "media"
        anki_media_path.mkdir()
        (anki_media_path / "sl_banka.mp3").write_bytes(b"fake audio")
        media_dir = tmp_path / "tunatale_media"
        _run(db_path, tmp_path, anki_media_path=anki_media_path, media_dir=media_dir)
        result2 = _run(db_path, tmp_path, anki_media_path=anki_media_path, media_dir=media_dir)
        assert result2["new_media"] == 0

    def test_sha_mismatch_updates_media_and_db(self, tmp_path):
        """When Anki media content changes (SHA mismatch), file is overwritten and DB updated."""
        db_path = self._build_db_with_sound(tmp_path)
        anki_media_path = tmp_path / "anki" / "media"
        anki_media_path.mkdir()
        media_file = anki_media_path / "sl_banka.mp3"
        media_file.write_bytes(b"original audio content")
        media_dir = tmp_path / "tunatale_media"
        # First import
        result1 = _run(db_path, tmp_path, anki_media_path=anki_media_path, media_dir=media_dir)
        assert result1["new_media"] == 1
        # Overwrite Anki media with new content (changes SHA)
        media_file.write_bytes(b"updated audio content changed")
        # Second import should detect SHA mismatch and update
        result2 = _run(db_path, tmp_path, anki_media_path=anki_media_path, media_dir=media_dir)
        assert result2["updated_media"] == 1
        # Verify the file was actually overwritten
        assert (media_dir / "sl_banka.mp3").read_bytes() == b"updated audio content changed"

    def test_inline_data_uri_image_materialized_to_media_dir(self, tmp_path):
        """When an Anki note's Image field is a base64 data: URI (the user pasted
        a rich-text fragment or screenshot inline instead of saving it as a file),
        the bytes are decoded into ``media_dir/inline_<sha[:16]>.<ext>`` and
        recorded as a media row keyed by sha256.

        Reproduces the kratek incident (2026-05-21): 3 of 707 deck notes carried
        inline base64 images; ``list_media_refs`` correctly skipped them (avoiding
        the OSError-too-long crash), but TT had no way to honor them in the UI
        without materializing the bytes.
        """
        import base64
        import sqlite3 as sq3

        from tests.conftest import build_minimal_anki_db

        anki_dir = tmp_path / "anki"
        anki_dir.mkdir(exist_ok=True)
        db_path = build_minimal_anki_db(anki_dir)
        # Tiny valid JPEG header bytes for a recognizable payload.
        payload = b"\xff\xd8\xff\xe0kratek-fake-jpeg-bytes"
        b64 = base64.b64encode(payload).decode("ascii")
        conn = sq3.connect(str(db_path))
        conn.execute(
            "UPDATE notes SET flds = ? WHERE id = 1001",
            (f'banka\x1fbank\x1f\x1f<img src="data:image/jpeg;base64,{b64}">\x1f\x1f\x1f',),
        )
        conn.commit()
        conn.close()

        anki_media_path = tmp_path / "anki" / "media"
        anki_media_path.mkdir()
        media_dir = tmp_path / "tunatale_media"

        result = _run(db_path, tmp_path, anki_media_path=anki_media_path, media_dir=media_dir)
        assert result["new_media"] == 1

        import hashlib

        sha = hashlib.sha256(payload).hexdigest()
        expected_name = f"inline_{sha[:16]}.jpg"
        assert (media_dir / expected_name).read_bytes() == payload

        tt = sq3.connect(str(tmp_path / "tunatale.db"))
        rows = tt.execute("SELECT filename, anki_filename, sha256, bytes FROM media WHERE kind='image'").fetchall()
        tt.close()
        assert rows == [(expected_name, expected_name, sha, len(payload))]

    def test_inline_data_uri_image_is_idempotent_on_rerun(self, tmp_path):
        """Re-running refresh on the same inline image hits the sha256 dedupe path:
        ``unchanged_media`` increments and no new row is added.
        """
        import base64
        import sqlite3 as sq3

        from tests.conftest import build_minimal_anki_db

        anki_dir = tmp_path / "anki"
        anki_dir.mkdir(exist_ok=True)
        db_path = build_minimal_anki_db(anki_dir)
        payload = b"idempotent-payload-bytes"
        b64 = base64.b64encode(payload).decode("ascii")
        conn = sq3.connect(str(db_path))
        conn.execute(
            "UPDATE notes SET flds = ? WHERE id = 1001",
            (f'banka\x1fbank\x1f\x1f<img src="data:image/png;base64,{b64}">\x1f\x1f\x1f',),
        )
        conn.commit()
        conn.close()

        anki_media_path = tmp_path / "anki" / "media"
        anki_media_path.mkdir()
        media_dir = tmp_path / "tunatale_media"

        _run(db_path, tmp_path, anki_media_path=anki_media_path, media_dir=media_dir)
        result2 = _run(db_path, tmp_path, anki_media_path=anki_media_path, media_dir=media_dir)

        assert result2["new_media"] == 0
        assert result2["unchanged_media"] >= 1

        tt = sq3.connect(str(tmp_path / "tunatale.db"))
        n_image_rows = tt.execute("SELECT COUNT(*) FROM media WHERE kind='image'").fetchone()[0]
        tt.close()
        assert n_image_rows == 1

    def test_inline_data_uri_replacement_collapses_old_inline(self, tmp_path):
        """If the user re-pastes a *different* inline image, the new sha256
        bytes get a new row and the old inline row collapses (per-kind cleanup).
        """
        import base64
        import sqlite3 as sq3

        from tests.conftest import build_minimal_anki_db

        anki_dir = tmp_path / "anki"
        anki_dir.mkdir(exist_ok=True)
        db_path = build_minimal_anki_db(anki_dir)

        def _set_inline(payload: bytes) -> None:
            b64 = base64.b64encode(payload).decode("ascii")
            conn = sq3.connect(str(db_path))
            conn.execute(
                "UPDATE notes SET flds = ? WHERE id = 1001",
                (f'banka\x1fbank\x1f\x1f<img src="data:image/jpeg;base64,{b64}">\x1f\x1f\x1f',),
            )
            conn.commit()
            conn.close()

        anki_media_path = tmp_path / "anki" / "media"
        anki_media_path.mkdir()
        media_dir = tmp_path / "tunatale_media"

        _set_inline(b"first-paste")
        _run(db_path, tmp_path, anki_media_path=anki_media_path, media_dir=media_dir)
        _set_inline(b"second-paste-different")
        result2 = _run(db_path, tmp_path, anki_media_path=anki_media_path, media_dir=media_dir)

        assert result2["new_media"] == 1
        assert result2["collapsed_media"] == 1

        tt = sq3.connect(str(tmp_path / "tunatale.db"))
        rows = tt.execute("SELECT filename FROM media WHERE kind='image'").fetchall()
        tt.close()
        assert len(rows) == 1
        assert rows[0][0].startswith("inline_")

    def test_image_field_removed_collapses_prior_file_row(self, tmp_path):
        """The exact kratek failure mode in microcosm: TT has a prior image row
        from when the Anki note had ``<img src="paste-old.jpg">``; the user has
        since replaced that with ``<img src="data:...">``. With Part 1's
        widened cleanup loop, the stale paste-old.jpg row collapses on next
        refresh even though no file ref is currently present.

        Without the fix, the cleanup loop only iterated kinds *touched in this
        pass* — the disappearance of file-based images of kind=image would never
        trigger a delete. TT kept serving the April image for kratek + 2 others.
        """
        import base64
        import sqlite3 as sq3

        from tests.conftest import build_minimal_anki_db

        anki_dir = tmp_path / "anki"
        anki_dir.mkdir(exist_ok=True)
        db_path = build_minimal_anki_db(anki_dir)
        # Step 1: Anki note initially has a file-based image.
        conn = sq3.connect(str(db_path))
        conn.execute(
            "UPDATE notes SET flds = ? WHERE id = 1001",
            ('banka\x1fbank\x1f\x1f<img src="paste-old.jpg">\x1f\x1f\x1f',),
        )
        conn.commit()
        conn.close()

        anki_media_path = tmp_path / "anki" / "media"
        anki_media_path.mkdir()
        (anki_media_path / "paste-old.jpg").write_bytes(b"old image bytes")
        media_dir = tmp_path / "tunatale_media"
        _run(db_path, tmp_path, anki_media_path=anki_media_path, media_dir=media_dir)

        tt_path = tmp_path / "tunatale.db"
        tt = sq3.connect(str(tt_path))
        before = tt.execute("SELECT filename FROM media WHERE kind='image'").fetchall()
        tt.close()
        assert before == [("paste-old.jpg",)]

        # Step 2: user replaces the file image with a data: URI in Anki.
        b64 = base64.b64encode(b"new-inline-payload").decode("ascii")
        conn = sq3.connect(str(db_path))
        conn.execute(
            "UPDATE notes SET flds = ? WHERE id = 1001",
            (f'banka\x1fbank\x1f\x1f<img src="data:image/jpeg;base64,{b64}">\x1f\x1f\x1f',),
        )
        conn.commit()
        conn.close()
        # Note: paste-old.jpg may still exist in collection.media/ (Anki doesn't
        # eagerly delete unreferenced media); the refresh must collapse anyway.

        result = _run(db_path, tmp_path, anki_media_path=anki_media_path, media_dir=media_dir)

        # Old file row is gone; new inline row is in place.
        assert result["collapsed_media"] == 1
        tt = sq3.connect(str(tt_path))
        after = tt.execute("SELECT filename FROM media WHERE kind='image' ORDER BY id").fetchall()
        tt.close()
        assert len(after) == 1
        assert after[0][0].startswith("inline_")
        assert after[0][0] != "paste-old.jpg"

    def test_image_field_emptied_collapses_prior_file_row(self, tmp_path):
        """Pure Part 1 case: image field is removed entirely (no file ref, no
        inline data URI). The prior image row must still collapse — otherwise
        TT keeps serving an image the user has deleted from Anki.
        """
        import sqlite3 as sq3

        from tests.conftest import build_minimal_anki_db

        anki_dir = tmp_path / "anki"
        anki_dir.mkdir(exist_ok=True)
        db_path = build_minimal_anki_db(anki_dir)
        conn = sq3.connect(str(db_path))
        conn.execute(
            "UPDATE notes SET flds = ? WHERE id = 1001",
            ('banka\x1fbank\x1f\x1f<img src="paste-old.jpg">\x1f\x1f\x1f',),
        )
        conn.commit()
        conn.close()

        anki_media_path = tmp_path / "anki" / "media"
        anki_media_path.mkdir()
        (anki_media_path / "paste-old.jpg").write_bytes(b"old image bytes")
        media_dir = tmp_path / "tunatale_media"
        _run(db_path, tmp_path, anki_media_path=anki_media_path, media_dir=media_dir)

        # Strip the image entirely from the Anki note.
        conn = sq3.connect(str(db_path))
        conn.execute(
            "UPDATE notes SET flds = ? WHERE id = 1001",
            ("banka\x1fbank\x1f\x1f\x1f\x1f\x1f",),
        )
        conn.commit()
        conn.close()

        result = _run(db_path, tmp_path, anki_media_path=anki_media_path, media_dir=media_dir)
        assert result["collapsed_media"] == 1

        tt = sq3.connect(str(tmp_path / "tunatale.db"))
        n = tt.execute("SELECT COUNT(*) FROM media WHERE kind='image'").fetchone()[0]
        tt.close()
        assert n == 0

    def test_stale_image_rows_collapsed_to_anki_current(self, tmp_path):
        """When Anki has reverted to an older filename, stale newer-id rows on the
        same collocation should be deleted so get_image_filename returns Anki's current.

        Reproduces the ptica/moška-obleka pattern: TT had two image rows from
        earlier syncs; user reverted Anki to the older filename; import_seed
        used to leave both rows, and TT served the newer-id stale paste.
        """
        import sqlite3 as sq3

        from tests.conftest import build_minimal_anki_db

        anki_dir = tmp_path / "anki"
        anki_dir.mkdir(exist_ok=True)
        db_path = build_minimal_anki_db(anki_dir)
        # Attach an <img> tag to note 1001 ("banka") so import_seed has an image to process.
        conn = sq3.connect(str(db_path))
        conn.execute(
            "UPDATE notes SET flds = ? WHERE id = 1001",
            ('banka\x1fbank\x1f\x1f<img src="img_bank.jpg">\x1f\x1f\x1f',),
        )
        conn.commit()
        conn.close()

        anki_media_path = tmp_path / "anki" / "media"
        anki_media_path.mkdir()
        (anki_media_path / "img_bank.jpg").write_bytes(b"bank image bytes")
        media_dir = tmp_path / "tunatale_media"
        media_dir.mkdir()

        # First import: TT gets one row referencing img_bank.jpg
        _run(db_path, tmp_path, anki_media_path=anki_media_path, media_dir=media_dir)

        # Simulate "user pasted a different image in Anki, ran import_seed, then
        # reverted Anki back to img_bank.jpg": insert a newer-id stale paste row
        # into TT (as if from a prior import) without changing Anki's current state.
        tt_path = tmp_path / "tunatale.db"
        tt = sq3.connect(str(tt_path))
        coll_id = tt.execute("SELECT id FROM collocations WHERE text='banka'").fetchone()[0]
        tt.execute(
            "INSERT INTO media (collocation_id, kind, filename, path, anki_filename, sha256, bytes) "
            "VALUES (?, 'image', 'paste-xxx.jpg', '/tmp/paste.jpg', 'paste-xxx.jpg', 'fakehash', 12345)",
            (coll_id,),
        )
        tt.commit()
        tt.close()

        # Second import: with the fix, the stale paste row should be deleted so
        # get_image_filename returns Anki's current image (img_bank.jpg).
        _run(db_path, tmp_path, anki_media_path=anki_media_path, media_dir=media_dir)

        tt = sq3.connect(str(tt_path))
        rows = tt.execute(
            "SELECT filename FROM media WHERE collocation_id=? AND kind='image' ORDER BY id",
            (coll_id,),
        ).fetchall()
        tt.close()
        # Only one image row should remain, and it should match Anki's current.
        assert rows == [("img_bank.jpg",)]


class TestTranslationStrip:
    def test_translation_html_stripped(self, tmp_path):
        """HTML tags in the translation field are stripped before writing to TunaTale."""
        import sqlite3 as sq3

        from tests.conftest import build_minimal_anki_db

        db_path = build_minimal_anki_db(tmp_path)
        conn = sq3.connect(str(db_path))
        conn.execute("UPDATE notes SET flds = ? WHERE id = 1001", ("banka\x1f<b>bank</b>",))
        conn.commit()
        conn.close()

        _run(db_path, tmp_path)
        tdb = sq3.connect(str(tmp_path / "tunatale.db"))
        row = tdb.execute("SELECT translation FROM collocations WHERE text = 'banka'").fetchone()
        tdb.close()
        assert row[0] == "bank"


class TestSkipsNonVocabNotes:
    """Post-merge, the 0. Slovene deck holds both vocabulary notes (on
    Slovene Vocabulary notetype) and reference/pronunciation Q&A notes
    (left on Basic by merge_dupes because their direction is unknown).

    The Q&A notes have long English questions in field[0] that
    extract_l2_from_fields returns verbatim — feeding that into
    SyntacticUnit triggers its word_count ∈ [1,8] constraint. Filter
    those out instead of crashing or polluting TunaTale with non-vocab."""

    def _update_note_flds(self, db_path, note_id, flds):
        import sqlite3 as sq3

        conn = sq3.connect(str(db_path))
        conn.execute("UPDATE notes SET flds = ? WHERE id = ?", (flds, note_id))
        conn.commit()
        conn.close()

    def test_long_l2_text_note_is_skipped_not_crashed(self, tmp_path):
        from tests.conftest import build_minimal_anki_db

        db_path = build_minimal_anki_db(tmp_path)
        # Note: Field 0 is an English question, Field 1 is the Slovene answer.
        # With the fix, extract_l2_from_fields now correctly returns Field 1 (Slovene answer)
        # instead of Field 0 (English question), so the note gets imported, not skipped.
        self._update_note_flds(
            db_path,
            1001,
            "What are the three possible values of written e in Slovene?\x1fsome answer",
        )
        result = _run(db_path, tmp_path)
        # Note is now imported (not skipped) because we extract the correct L2 field
        assert result["new_parents"] == 5  # 5 notes imported (including the updated one)

    def test_long_l2_text_count_is_reported(self, tmp_path):
        from tests.conftest import build_minimal_anki_db

        db_path = build_minimal_anki_db(tmp_path)
        # With the fix, the note is imported (not skipped) because extract_l2_from_fields
        # now correctly returns the Slovene answer from Field 1
        self._update_note_flds(
            db_path,
            1001,
            "What are the three possible values of written e in Slovene?\x1fsome answer",
        )
        result = _run(db_path, tmp_path)
        # Note is imported, not skipped
        assert result["skipped_non_vocab"] == 0
        assert result["new_parents"] == 5

    def test_empty_l2_text_note_is_skipped(self, tmp_path):
        """Note whose every field yields empty L2 extraction is skipped."""
        from tests.conftest import build_minimal_anki_db

        db_path = build_minimal_anki_db(tmp_path)
        # Both fields empty → extract_l2_from_fields returns "" → word_count=0
        self._update_note_flds(db_path, 1001, "\x1f")
        result = _run(db_path, tmp_path)
        assert result["new_parents"] == 4
        assert result["skipped_non_vocab"] == 1

    def test_eight_word_phrase_is_imported(self, tmp_path):
        """Boundary: an 8-word L2 phrase is valid vocab (not skipped)."""
        from tests.conftest import build_minimal_anki_db

        db_path = build_minimal_anki_db(tmp_path)
        self._update_note_flds(db_path, 1001, "eno dve tri štiri pet šest sedem osem\x1ftrans")
        result = _run(db_path, tmp_path)
        assert result["new_parents"] == 5
        assert result["skipped_non_vocab"] == 0

    def test_long_reference_question_is_imported(self, tmp_path):
        """Reference/Q&A notes with >8-word English questions used to be
        skipped as 'non-vocab'. They're legitimate cards the user wants in
        TT — same notetype as the imported 7-word phonics questions, just
        with a longer prompt. Regression for the missing 'u̯ / w / ʍ glide
        family' phonics note (12 words).

        Production fields reproduce the bug: field 0 wins L2 extraction
        (IPA chars u̯/ʍ outweigh the field-1 stopword-heavy answer), giving
        a 12-word l2_text that the old `1 <= word_count <= 8` filter rejected.
        """
        from tests.conftest import build_minimal_anki_db

        db_path = build_minimal_anki_db(tmp_path)
        # Verbatim production fields from anki_note_id=1774631907182.
        field_0 = "What is the <b>u̯ / w / ʍ</b> glide family in Slovene?"
        field_1 = (
            "All are back rounded glides written as <b>v</b> (or arising from <b>l</b>). "
            "Position determines which:<br><br>"
            "[u̯] — after a vowel (word-final or before consonant)<br>"
            "[w] — word-initial before voiced consonant<br>"
            "[ʍ] — word-initial before voiceless consonant"
        )
        self._update_note_flds(db_path, 1001, f"{field_0}\x1f{field_1}")
        result = _run(db_path, tmp_path)
        assert result["new_parents"] == 5, (
            f"12-word reference question must be imported, not skipped; "
            f"got new_parents={result['new_parents']}, skipped={result.get('skipped_non_vocab')}"
        )
        assert result["skipped_non_vocab"] == 0


class TestLanguageCode:
    def test_default_tags_collocations_with_target_language(self, fake_anki_db, tmp_path):
        """language_code=None defaults to settings.target_language ('sl' in tests)."""
        _run(fake_anki_db, tmp_path)
        with closing(sqlite3.connect(str(tmp_path / "tunatale.db"))) as db:
            codes = {r[0] for r in db.execute("SELECT DISTINCT language_code FROM collocations")}
        assert codes == {"sl"}

    def test_explicit_language_code_tags_and_guids_collocations(self, fake_anki_db, tmp_path):
        """Importing with language_code='no' tags every row 'no' and folds it into the GUID."""
        from app.common.guid import compute_guid

        _run(fake_anki_db, tmp_path, language_code="no")
        with closing(sqlite3.connect(str(tmp_path / "tunatale.db"))) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute("SELECT text, language_code, disambig_key, guid FROM collocations").fetchall()
        assert rows, "expected imported collocations"
        for r in rows:
            assert r["language_code"] == "no"
            assert r["guid"] == compute_guid(r["text"], "no", r["disambig_key"] or "")

    def test_norwegian_notetype_extracts_by_field_name_not_position(self, tmp_path):
        """The Norwegian profile reads L2 from 'Norwegian word' (field idx 1) and the
        gloss from 'English translation' (field idx 4) — proving name-based extraction."""
        from tests.conftest import build_norwegian_anki_db

        db_path = build_norwegian_anki_db(tmp_path)
        import_seed(
            anki_collection_path=db_path,
            anki_backup_dir=tmp_path / "bak",
            anki_media_path=tmp_path / "fake_media",
            deck_name="0. 6000 Most Frequent Norwegian Words [Part 1]",
            language_code="no",
            tunatale_db_path=str(tmp_path / "tunatale_no.db"),
            media_dir=tmp_path / "media",
            fallback_log_path=tmp_path / "fallback.log",
        )
        with closing(sqlite3.connect(str(tmp_path / "tunatale_no.db"))) as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT text, translation, language_code FROM collocations").fetchone()
            dirs = {r[0] for r in db.execute("SELECT direction FROM collocation_directions")}
        assert row["text"] == "være"  # 'Norwegian word' field, not 'Frequency index' (idx 0)
        assert row["translation"] == "to be"  # 'English translation' field, not 'Word class' (idx 2)
        assert row["language_code"] == "no"
        assert dirs == {"recognition"}  # single card → recognition-only, no phantom production

    def test_homographs_with_different_word_class_stay_separate(self, tmp_path):
        """Two notes sharing 'løfte' (noun 'promise' / verb 'lift') must import as
        TWO collocations — Word class is the disambig, so they don't collapse."""
        from tests.conftest import build_norwegian_anki_db

        db_path = build_norwegian_anki_db(tmp_path, with_homographs=True)
        import_seed(
            anki_collection_path=db_path,
            anki_backup_dir=tmp_path / "bak",
            anki_media_path=tmp_path / "fake_media",
            deck_name="0. 6000 Most Frequent Norwegian Words [Part 1]",
            language_code="no",
            tunatale_db_path=str(tmp_path / "tunatale_no.db"),
            media_dir=tmp_path / "media",
            fallback_log_path=tmp_path / "fallback.log",
        )
        with closing(sqlite3.connect(str(tmp_path / "tunatale_no.db"))) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT translation, disambig_key, article FROM collocations WHERE text='løfte' ORDER BY disambig_key"
            ).fetchall()
        assert len(rows) == 2, f"homographs collapsed: {[dict(r) for r in rows]}"
        assert {r["translation"] for r in rows} == {"promise", "lift"}
        assert {r["disambig_key"] for r in rows} == {"noun", "verb"}
        # Article imported from the 'Article' field: 'et' for the noun, blank for the verb.
        assert {(r["disambig_key"], r["article"]) for r in rows} == {("noun", "et"), ("verb", "")}


class TestCLI:
    def test_cli_dry_run_prints_dry_run(self, fake_anki_db, tmp_path, monkeypatch, capsys):
        """_cli() runs without error in dry-run mode."""
        from app.anki import import_seed as mod

        monkeypatch.setattr("sys.argv", ["import_seed", "--dry-run"])
        monkeypatch.setattr(
            mod,
            "import_seed",
            lambda **kw: {
                "new_parents": 3,
                "new_directions": 6,
                "new_media": 0,
                "skipped_guid_collisions": 0,
                "skipped_non_vocab": 0,
            },
        )
        mod._cli()
        out = capsys.readouterr().out
        assert "DRY RUN" in out

    def test_cli_language_resolves_registered_deck(self, monkeypatch):
        """--language no resolves the Norwegian deck from the registry and threads the code."""
        from app.anki import import_seed as mod

        captured = {}

        def fake_import_seed(**kw):
            captured.update(kw)
            return dict.fromkeys(
                ("new_parents", "new_directions", "new_media", "skipped_guid_collisions", "skipped_non_vocab"), 0
            )

        monkeypatch.setattr("sys.argv", ["import_seed", "--language", "no", "--dry-run"])
        monkeypatch.setattr(mod, "import_seed", fake_import_seed)
        mod._cli()
        assert captured["language_code"] == "no"
        assert captured["deck_name"] == "0. 6000 Most Frequent Norwegian Words [Part 1]"


class TestTransactionRollback:
    def test_exception_mid_import_rolls_back_all_writes(self, fake_anki_db, tmp_path):
        """A crash mid-import leaves TunaTale with zero rows written."""
        from app.anki import sqlite_reader

        call_count = [0]
        original_upsert_area = sqlite_reader.fetch_notes_for_deck

        def crash_on_third_note(conn, deck_id):
            notes = original_upsert_area(conn, deck_id)
            # Inject a bad note at position 2 that will cause a later crash
            return notes

        # Simulate crash by patching upsert_by_guid to raise on 3rd call
        from app.srs import database as db_module

        original_upsert = db_module.SRSDatabase.upsert_by_guid

        def crashing_upsert(self, unit, language_code, directions, anki_note_id=None):
            call_count[0] += 1
            if call_count[0] == 3:
                raise RuntimeError("simulated crash")
            return original_upsert(self, unit, language_code, directions, anki_note_id)

        with (
            patch.object(db_module.SRSDatabase, "upsert_by_guid", crashing_upsert),
            pytest.raises(RuntimeError, match="simulated crash"),
        ):
            _run(fake_anki_db, tmp_path)

        with closing(sqlite3.connect(str(tmp_path / "tunatale.db"))) as db:
            count = db.execute("SELECT COUNT(*) FROM collocations").fetchone()[0]
        assert count == 0


class TestRefreshMediaFromConn:
    def test_missing_deck_returns_zero_counts(self):
        """deck not found in the collection → early return, no media work."""
        from pathlib import Path

        from app.anki.import_seed import refresh_media_from_conn
        from app.srs.database import SRSDatabase
        from tests.test_anki_sync_create_new import _make_dual_collection_conn

        conn = _make_dual_collection_conn()
        db = SRSDatabase(":memory:")
        try:
            res = refresh_media_from_conn(
                conn,
                deck_name="No Such Deck",
                anki_media_path=Path("/nonexistent"),
                media_dir=Path("/nonexistent"),
                db=db,
            )
        finally:
            db.close()
        assert res == {"new_media": 0, "updated_media": 0, "unchanged_media": 0, "collapsed_media": 0}

    def test_linked_notes_returns_media_counts(self):
        """refresh_media_from_conn processes linked notes and returns media dict."""
        from pathlib import Path

        from app.anki.import_seed import refresh_media_from_conn
        from app.models.srs_item import Direction
        from app.models.syntactic_unit import SyntacticUnit
        from app.srs.database import SRSDatabase
        from tests.test_anki_sync_create_new import _make_dual_collection_conn

        conn = _make_dual_collection_conn()
        conn.execute(
            "INSERT INTO notes (id, guid, mid, mod, usn, tags, flds, sfld, csum, flags, data) "
            "VALUES (9001, 'guid-1', 1000001, 0, 0, '', 'banka\x1fbank\x1f\x1f\x1f', 'banka', 0, 0, '')"
        )
        conn.execute(
            "INSERT INTO cards (id, nid, did, ord, mod, usn, type, queue, due, ivl, factor, reps, lapses, data) "
            "VALUES (90010, 9001, 12345, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, '')"
        )
        conn.commit()

        db = SRSDatabase(":memory:")
        unit = SyntacticUnit(text="banka", translation="bank", word_count=1, difficulty=1, source="anki")
        db.add_collocation(unit)
        item = db.get_collocation("banka")
        assert item is not None
        db.set_anki_ids(item.guid, 9001, {Direction.RECOGNITION: 90010})
        try:
            res = refresh_media_from_conn(
                conn,
                deck_name="0. Slovene",
                anki_media_path=Path("/nonexistent"),
                media_dir=Path("/nonexistent"),
                db=db,
            )
        finally:
            db.close()
        assert isinstance(res, dict)
        assert res["new_media"] == 0
        assert res["updated_media"] == 0
        assert res["unchanged_media"] == 0
        assert res["collapsed_media"] == 0

    def test_skips_unlinked_notes(self):
        """refresh_media_from_conn skips notes not linked in TT."""
        from pathlib import Path

        from app.anki.import_seed import refresh_media_from_conn
        from app.models.syntactic_unit import SyntacticUnit
        from app.srs.database import SRSDatabase
        from tests.test_anki_sync_create_new import _make_dual_collection_conn

        conn = _make_dual_collection_conn()
        conn.execute(
            "INSERT INTO notes (id, guid, mid, mod, usn, tags, flds, sfld, csum, flags, data) "
            "VALUES (9001, 'guid-1', 1000001, 0, 0, '', 'banka\x1fbank\x1f\x1f\x1f', 'banka', 0, 0, '')"
        )
        conn.execute(
            "INSERT INTO cards (id, nid, did, ord, mod, usn, type, queue, due, ivl, factor, reps, lapses, data) "
            "VALUES (90010, 9001, 12345, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, '')"
        )
        conn.commit()

        db = SRSDatabase(":memory:")
        unit = SyntacticUnit(text="banka", translation="bank", word_count=1, difficulty=1, source="corpus")
        db.add_collocation(unit)
        try:
            res = refresh_media_from_conn(
                conn,
                deck_name="0. Slovene",
                anki_media_path=Path("/nonexistent"),
                media_dir=Path("/nonexistent"),
                db=db,
            )
        finally:
            db.close()
        assert isinstance(res, dict)
        assert res["new_media"] == 0
        assert res["updated_media"] == 0
        assert res["unchanged_media"] == 0
