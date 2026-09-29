"""``col.mod`` is written in MILLISECONDS by every TT write path (tunatale-6zoc).

Anki stores ``col.mod`` as ``TimestampMillis`` (rslib ``SyncMeta.modified``,
read straight from ``col.mod`` by ``Collection::sync_meta``). Two TT writers —
``OfflineWriter._bump_col`` and ``apply_repositioning`` — passed the SECONDS
timestamp they use for ``cards.mod``/``notes.mod`` (which ARE seconds in Anki),
leaving a 10-digit ``col.mod`` beside a 13-digit ``col.ls``. Observed on the live
laptop ``tt_collection`` on 2026-09-28 after a sync with no push leg.

What that breaks, from rslib ``sync/collection/meta.rs::compared_to_remote``:
``local_is_newer = local.modified > remote.modified`` is always FALSE for a
seconds value, so the client never sends its collection config or creation stamp
and the server treats its own config as the newer one; and
``status.rs::collection_changed_since_sync`` (``mod > ls``) reads "no local
changes" with dirty rows waiting.

The oracle: after any TT write, ``col.mod`` is a 13-digit ms value, strictly
greater than both its previous value and ``col.ls`` — so it can never equal the
server's ``modified`` (which ``ls`` mirrors after a sync) and Anki cannot answer
NoChanges over rows TT just dirtied.
"""

from __future__ import annotations

import sqlite3
import time

from app.plugins.anki_sync import sync_writer
from app.plugins.anki_sync.sync_writer import OfflineWriter

# Measured on the live tt_collection, 2026-09-28: the seconds value a TT write
# left behind, and the ms last-sync stamp it sat beside.
LEGACY_SECONDS_MOD = 1_790_648_707
LAST_SYNC_MS = 1_790_648_706_749


def _col(mod: int, ls: int, usn: int = 4321) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE col (id INTEGER PRIMARY KEY, mod INTEGER, usn INTEGER, ls INTEGER)")
    conn.execute("INSERT INTO col VALUES (1, ?, ?, ?)", (mod, usn, ls))
    return conn


def _col_row(conn: sqlite3.Connection) -> sqlite3.Row:
    return conn.execute("SELECT mod, usn, ls FROM col").fetchone()


def _is_ms(value: int) -> bool:
    return len(str(value)) == 13


class TestBumpColMod:
    def test_a_legacy_seconds_value_becomes_now_in_milliseconds(self) -> None:
        conn = _col(LEGACY_SECONDS_MOD, LAST_SYNC_MS)
        before_ms = int(time.time() * 1000)

        sync_writer.bump_col_mod(conn)

        mod = _col_row(conn)["mod"]
        assert _is_ms(mod), f"col.mod={mod} is not a 13-digit ms value"
        assert mod >= before_ms
        assert mod <= int(time.time() * 1000)

    def test_it_stays_above_a_last_sync_stamp_from_a_clock_that_runs_ahead(self) -> None:
        """After a sync, mod == ls == the SERVER's modified. A server clock a minute
        ahead of this machine would make a plain now_ms smaller than both — and a
        value that happens to equal ls would read as NoChanges."""
        ahead = int(time.time() * 1000) + 60_000
        conn = _col(ahead, ahead)

        sync_writer.bump_col_mod(conn)

        row = _col_row(conn)
        assert row["mod"] > row["ls"]
        assert row["mod"] == ahead + 1

    def test_consecutive_bumps_strictly_increase(self) -> None:
        conn = _col(LEGACY_SECONDS_MOD, LAST_SYNC_MS)
        seen = []
        for _ in range(5):
            sync_writer.bump_col_mod(conn)
            seen.append(_col_row(conn)["mod"])
        assert seen == sorted(seen)
        assert len(set(seen)) == len(seen), f"a bump did not advance col.mod: {seen}"

    def test_never_touches_col_usn(self) -> None:
        """col.usn is the sync ANCHOR, not a dirty flag (Layer 61)."""
        conn = _col(LEGACY_SECONDS_MOD, LAST_SYNC_MS, usn=4321)
        sync_writer.bump_col_mod(conn)
        assert _col_row(conn)["usn"] == 4321


class TestTheWritePathsUseIt:
    """The writer that used to pass seconds, through its real entry point against a
    collection-shaped fixture. ``apply_repositioning``, the other one, is pinned in
    ``test_anki_reposition_production_cards.py`` beside its own fixture."""

    def test_offline_writer_leaves_col_mod_in_milliseconds(self, fake_anki_db) -> None:
        conn = sqlite3.connect(fake_anki_db)
        conn.row_factory = sqlite3.Row
        conn.execute("UPDATE col SET mod = ?, ls = ?", (LEGACY_SECONDS_MOD, LAST_SYNC_MS))
        cid = conn.execute("SELECT id FROM cards LIMIT 1").fetchone()["id"]

        OfflineWriter(conn).update_card_memory_state(cid, stability=2.5, difficulty=6.1, last_review_secs=222)

        row = conn.execute("SELECT mod, ls FROM col").fetchone()
        assert _is_ms(row["mod"]), f"col.mod={row['mod']} is not a 13-digit ms value"
        assert row["mod"] > row["ls"]
        # The card row keeps SECONDS — that is Anki's unit for cards.mod.
        card_mod = conn.execute("SELECT mod FROM cards WHERE id = ?", (cid,)).fetchone()["mod"]
        assert len(str(card_mod)) == 10
