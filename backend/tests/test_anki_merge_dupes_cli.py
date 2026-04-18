"""CLI-surface tests for ``app.anki.merge_dupes``.

Covers ``--dry-run``, ``--yes``, the AnkiWeb ``syncKey`` preflight, and the
defaults-from-settings wiring. Correctness of the apply/plan path is already
exercised in ``test_anki_merge_dupes_apply.py``.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.anki.merge_dupes import merge_dupes


def _set_col_conf(db_path: Path, conf: dict) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("UPDATE col SET conf=?", (json.dumps(conf),))
        conn.commit()
    finally:
        conn.close()


def _sha256(path: Path) -> str:
    from app.anki.safety import _sha256_file

    return _sha256_file(path)


class TestDryRun:
    def test_dry_run_writes_nothing(self, fake_anki_db_slovene_pairs, tmp_path):
        pre_sha = _sha256(fake_anki_db_slovene_pairs)
        result = merge_dupes(
            deck_name="0. Slovene",
            anki_collection_path=fake_anki_db_slovene_pairs,
            anki_backup_dir=tmp_path / "bak",
            dry_run=True,
            yes=False,
        )
        post_sha = _sha256(fake_anki_db_slovene_pairs)
        assert pre_sha == post_sha, "dry-run must not modify the source"
        # Plan numbers surface even when nothing is written.
        assert result["planned_notes_migrated"] > 0
        assert result["notes_migrated"] == 0

    def test_dry_run_still_creates_backup(self, fake_anki_db_slovene_pairs, tmp_path):
        backup_dir = tmp_path / "bak"
        merge_dupes(
            deck_name="0. Slovene",
            anki_collection_path=fake_anki_db_slovene_pairs,
            anki_backup_dir=backup_dir,
            dry_run=True,
            yes=False,
        )
        assert backup_dir.exists()
        assert len(list(backup_dir.glob("collection.anki2.bak_*"))) == 1

    def test_dry_run_summary_printed(self, fake_anki_db_slovene_pairs, tmp_path, capsys):
        merge_dupes(
            deck_name="0. Slovene",
            anki_collection_path=fake_anki_db_slovene_pairs,
            anki_backup_dir=tmp_path / "bak",
            dry_run=True,
            yes=False,
        )
        out = capsys.readouterr().out
        # Human-readable summary must show pairs/singletons/homonym counts
        assert "paired" in out.lower() or "pairs" in out.lower()
        assert "homonym" in out.lower() or "barva" in out.lower()


class TestYesFlag:
    def test_real_run_without_yes_and_with_sync_key_aborts(self, fake_anki_db_slovene_pairs, tmp_path, monkeypatch):
        _set_col_conf(fake_anki_db_slovene_pairs, {"syncKey": "abc"})
        pre_sha = _sha256(fake_anki_db_slovene_pairs)

        def _no_input(_prompt=""):
            raise AssertionError("the CLI must not prompt — --yes is required up-front")

        monkeypatch.setattr("builtins.input", _no_input)
        result = merge_dupes(
            deck_name="0. Slovene",
            anki_collection_path=fake_anki_db_slovene_pairs,
            anki_backup_dir=tmp_path / "bak",
            dry_run=False,
            yes=False,
        )
        assert result.get("aborted") is True
        assert _sha256(fake_anki_db_slovene_pairs) == pre_sha

    def test_real_run_with_yes_commits(self, fake_anki_db_slovene_pairs, tmp_path):
        _set_col_conf(fake_anki_db_slovene_pairs, {"syncKey": "abc"})
        result = merge_dupes(
            deck_name="0. Slovene",
            anki_collection_path=fake_anki_db_slovene_pairs,
            anki_backup_dir=tmp_path / "bak",
            dry_run=False,
            yes=True,
        )
        assert result.get("aborted") is not True
        assert result["notes_deleted"] > 0

    def test_real_run_no_sync_key_does_not_require_yes(self, fake_anki_db_slovene_pairs, tmp_path):
        """With no AnkiWeb link, --yes is not strictly required but the command still applies."""
        result = merge_dupes(
            deck_name="0. Slovene",
            anki_collection_path=fake_anki_db_slovene_pairs,
            anki_backup_dir=tmp_path / "bak",
            dry_run=False,
            yes=False,
        )
        assert result.get("aborted") is not True
        assert result["notes_deleted"] > 0


class TestDefaults:
    def test_defaults_from_settings_when_kwargs_none(self, fake_anki_db_slovene_pairs, tmp_path, monkeypatch):
        from app.anki import merge_dupes as mod

        fake_settings = type(
            "S",
            (),
            {
                "anki_deck_name": "0. Slovene",
                "anki_collection_path": fake_anki_db_slovene_pairs,
                "anki_backup_dir": tmp_path / "bak",
            },
        )()
        monkeypatch.setattr(mod, "settings", fake_settings)

        result = merge_dupes(dry_run=True, yes=False)
        assert result["planned_notes_migrated"] > 0


class TestDeckNotFound:
    def test_raises_when_deck_missing(self, fake_anki_db_slovene_pairs, tmp_path):
        with pytest.raises(RuntimeError, match="[Dd]eck"):
            merge_dupes(
                deck_name="Nonexistent",
                anki_collection_path=fake_anki_db_slovene_pairs,
                anki_backup_dir=tmp_path / "bak",
                dry_run=True,
                yes=False,
            )


class TestCLIEntrypoint:
    def test_cli_invokes_merge_dupes(self, fake_anki_db_slovene_pairs, tmp_path, monkeypatch):
        from app.anki import merge_dupes as mod

        monkeypatch.setattr("sys.argv", ["merge_dupes", "--dry-run", "--yes"])

        captured: dict[str, object] = {}

        def fake_merge(**kwargs):
            captured.update(kwargs)
            return {
                "notes_migrated": 0,
                "cards_reparented": 0,
                "notes_deleted": 0,
                "planned_notes_migrated": 7,
                "planned_cards_reparented": 5,
                "planned_notes_deleted": 5,
                "aborted": False,
            }

        monkeypatch.setattr(mod, "merge_dupes", fake_merge)
        mod._cli()

        assert captured["dry_run"] is True
        assert captured["yes"] is True
        assert captured["deck_name"] is None
