"""Tests for app.anki.fix_biti_clozes_only data migration."""

from __future__ import annotations

import sqlite3

from app.anki.fix_biti_clozes_only import DUPLICATE_ID, SI_LEMMA_ID, SOURCE_ID, apply_fix, plan_fix
from app.common.guid import compute_guid
from app.srs.database import SRSDatabase
from app.srs.function_words import format_morphology_hint


def _seed_biti_mess(db: SRSDatabase) -> sqlite3.Connection:
    """Seed the pre-fix state by inserting rows directly at fixed ids."""
    conn = db._conn
    # Temporarily disable FK checks so we can insert at fixed ids
    conn.execute("PRAGMA foreign_keys = OFF")
    today = "2026-06-04T04:00:00+00:00"

    guid_858 = compute_guid("biti", "sl", "")
    conn.execute(
        """INSERT INTO collocations (id, text, translation, language_code, word_count, unit_difficulty,
           source, lemma, card_type, disambig_key, source_sentence, grammar, guid, anki_note_id)
           VALUES (?, 'biti', '', 'sl', 1, 1, 'llm', 'biti', 'cloze', '',
           'Zdravo kje {{c1::ste}}', '', ?, 100001)""",
        (SOURCE_ID, guid_858),
    )
    conn.execute(
        "INSERT INTO collocation_directions (collocation_id, direction, stability, fsrs_difficulty, due_at, reps, lapses, state, dirty_fsrs)"
        " VALUES (?, 'production', 1.0, 5.0, ?, 0, 0, 'new', 0)",
        (SOURCE_ID, today),
    )

    guid_868 = compute_guid("ste", "sl", "")
    conn.execute(
        """INSERT INTO collocations (id, text, translation, language_code, word_count, unit_difficulty,
           source, lemma, card_type, disambig_key, source_sentence, grammar, guid, anki_note_id)
           VALUES (?, 'ste', '', 'sl', 1, 1, 'llm', 'biti', 'cloze', '',
           'Zdravo kje {{c1::ste}}', '', ?, NULL)""",
        (DUPLICATE_ID, guid_868),
    )
    conn.execute(
        "INSERT INTO collocation_directions (collocation_id, direction, stability, fsrs_difficulty, due_at, reps, lapses, state, dirty_fsrs)"
        " VALUES (?, 'production', 1.0, 5.0, ?, 0, 0, 'new', 0)",
        (DUPLICATE_ID, today),
    )

    # Seed 866 (si) with leaked translation
    guid_866 = compute_guid("si", "sl", "morph:verb-2sg")
    conn.execute(
        """INSERT INTO collocations (id, text, translation, language_code, word_count, unit_difficulty,
           source, lemma, card_type, disambig_key, source_sentence, grammar, guid)
           VALUES (?, 'si', 'biti, 2nd person singular', 'sl', 1, 1, 'llm', 'biti', 'cloze',
           'morph:verb-2sg', 'Zdravo kje {{c1::si}}', 'biti, 2nd person singular', ?)""",
        (SI_LEMMA_ID, guid_866),
    )

    conn.execute("PRAGMA foreign_keys = ON")
    conn.commit()
    return conn


class TestFixBitiClozesOnly:
    def test_plan_fix_shows_all_rows(self):
        db = SRSDatabase(":memory:")
        conn = _seed_biti_mess(db)
        info = plan_fix(conn)
        assert info["858"] is not None
        assert info["868"] is not None
        assert info["866"] is not None
        assert info["866"]["translation"] == "biti, 2nd person singular"
        assert info["duplicate_directions"] == 1
        assert info["duplicate_revlog"] == 0

    def test_plan_fix_missing_rows(self):
        db = SRSDatabase(":memory:")
        info = plan_fix(db._conn)
        assert info["858"] is None
        assert info["868"] is None

    def test_apply_fix_updates_source_and_deletes_duplicate(self):
        db = SRSDatabase(":memory:")
        conn = _seed_biti_mess(db)
        counts = apply_fix(conn)

        assert counts["source_updated"] == 1
        assert counts["duplicate_deleted"] == 1
        assert counts["si_translation_cleared"] == 1

        # 858 should now be the 2pl conjugation cloze
        row_858 = conn.execute("SELECT * FROM collocations WHERE id = 858").fetchone()
        assert row_858 is not None
        assert row_858["text"] == "ste"
        assert row_858["disambig_key"] == "morph:verb-2pl"
        assert row_858["grammar"] == format_morphology_hint("biti", "verb:2pl")
        assert row_858["guid"] == compute_guid("ste", "sl", "morph:verb-2pl")
        assert row_858["anki_note_id"] == 100001  # preserved

        # 868 should be gone
        row_868 = conn.execute("SELECT * FROM collocations WHERE id = 868").fetchone()
        assert row_868 is None

        # 868's directions should be gone (CASCADE)
        dirs = conn.execute("SELECT COUNT(*) FROM collocation_directions WHERE collocation_id = 868").fetchone()[0]
        assert dirs == 0

        # 866 translation should be cleared
        row_866 = conn.execute("SELECT translation FROM collocations WHERE id = 866").fetchone()
        assert row_866["translation"] == ""

    def test_apply_fix_idempotent(self):
        db = SRSDatabase(":memory:")
        conn = _seed_biti_mess(db)
        apply_fix(conn)

        # Second apply should not error. source_updated may be 1 (UPDATE
        # matched a row even though same values) and duplicate_deleted == 0.
        counts2 = apply_fix(conn)
        assert counts2["duplicate_deleted"] == 0  # already gone
        # 858 still exists — check it's still correct
        row_858 = conn.execute("SELECT * FROM collocations WHERE id = 858").fetchone()
        assert row_858 is not None
        assert row_858["text"] == "ste"

    def test_apply_fix_nothing_to_do(self):
        db = SRSDatabase(":memory:")
        counts = apply_fix(db._conn)
        assert counts["source_updated"] == 0
        assert counts["duplicate_deleted"] == 0
        assert counts["si_translation_cleared"] == 0
