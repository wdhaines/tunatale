# Anki Sync Protocol

Any tool under `backend/app/anki/` that writes to `collection.anki2` must preserve AnkiWeb sync consistency. Skip this and the user's next sync re-uploads hundreds of cards every time.

## The USN desync trap

Anki tracks sync state via `col.usn` + per-row `usn`. Rules:
- `row.usn = -1`: dirty, push on next sync
- `row.usn > col.usn`: Anki thinks "newer than server knows" → push
- `row.usn <= col.usn`: clean

**Forced full uploads preserve local row USNs but reset `col.usn`.** After a full upload, any row whose `usn > 0` (or whatever the server set `col.usn` to) is perpetually seen as dirty. Result: every subsequent incremental sync re-uploads those rows forever. Anki has no self-repair.

## 3-step workflow for schema-changing migrations

A migration "bumps schema" when it modifies `col.scm` — e.g., adding a notetype field, adding a field config. This forces AnkiWeb to demand a full upload.

1. **Run the migration.** It must also bump `notetypes.mtime_secs`, set `notetypes.usn = -1`, and update `col.scm`. Skipping any of these triggers Anki's "Check Database" on next open, which does the bumps itself and surprises the user.
2. **Tell the user: open Anki → File → Sync → Upload to AnkiWeb.** This is unavoidable after any `col.scm` change.
3. **After Anki closes, run `uv run python -m app.anki.normalize_usns`.** Resets `cards.usn`, `notes.usn`, `revlog.usn` where they're `> col.usn` back to `col.usn`. No content change — just aligns bookkeeping.

Data-only migrations (e.g., `backfill_guids` — rewrites `notes.guid`, sets `notes.usn=-1`) stay within incremental-sync territory and do NOT need steps 2–3.

## Required writes for every mutation

When writing to Anki tables, always:
- **`notes`/`cards` mutation**: set `usn = -1` and `mod = now_ts` on every touched row. Without `usn=-1`, Anki's integrity check re-detects the change on next open and bumps `col.scm` itself, forcing a full sync.
- **`col` mutation**: `UPDATE col SET mod = ?` after any batch write. **Do NOT set `col.usn = -1`** (Layer 61). `col.usn` is the sync *anchor* — the server's last USN — not a per-row dirty flag. The content rows you touch (`cards`/`notes`/`revlog`/`decks`) each carry their own `usn = -1`, which is what actually pushes; bumping `col.mod` tells Anki the collection changed. Clobbering `col.usn` to `-1` is invisible single-device, but the moment another device (e.g. the phone) advances the server's USN, AnkiWeb can't reconcile the desktop's `usn=-1` incrementally and **demands a full sync** (reproduced 2026-05-29 — see `_bump_col` in `app/anki/sync.py`). The one-shot migration scripts that still write `col.usn=-1` are out of scope: they bump `col.scm` and intentionally force a one-way sync anyway.
- **Schema change (fields, notetypes)**: bump `notetypes.mtime_secs`, set `notetypes.usn = -1`, and `UPDATE col SET scm = ?` (all three, not just one).

## Deletes — the `graves` table

To delete notes/cards while keeping AnkiWeb in sync, write a grave row instead of a bare `DELETE`. Anki's sync layer uses `graves` to tell the server what was removed.

- `graves` columns: `oid INTEGER NOT NULL, type INTEGER NOT NULL, usn INTEGER NOT NULL`, `PRIMARY KEY (oid, type)`.
- `type` constants (from `rslib/src/storage/graves/mod.rs:13-19`): `0 = Card`, `1 = Note`, `2 = Deck`.
- One grave per card AND one grave per note (Anki's `remove_notes_inner` does this in `rslib/src/notes/mod.rs:502-515`). For a note with two cards: 2 grave rows of `type=0` (the cids) + 1 grave row of `type=1` (the nid).
- `usn=-1` on every new grave row (client-side; the server rewrites it during sync).
- Bump `col.mod` and set `col.usn=-1`. **Don't** touch `col.scm` — deletes are data-only, no full upload.

Canonical pattern (mirror `app/anki/delete_phonology_demos.py` or `cleanup_function_word_notes.py`):

```python
_GRAVE_KIND_CARD, _GRAVE_KIND_NOTE = 0, 1
card_ids = [r[0] for r in conn.execute("SELECT id FROM cards WHERE nid=?", (nid,))]
for cid in card_ids:
    conn.execute("INSERT OR REPLACE INTO graves (oid, type, usn) VALUES (?, ?, -1)", (cid, _GRAVE_KIND_CARD))
    conn.execute("DELETE FROM cards WHERE id=?", (cid,))
conn.execute("INSERT OR REPLACE INTO graves (oid, type, usn) VALUES (?, ?, -1)", (nid, _GRAVE_KIND_NOTE))
conn.execute("DELETE FROM notes WHERE id=?", (nid,))
conn.execute("UPDATE col SET mod=?, usn=-1", (int(time.time() * 1000),))
```

Verify post-write: each deleted nid has exactly one type=1 grave; each deleted cid has exactly one type=0 grave.

## Diagnostic (safe while Anki is open — read-only)

```bash
sqlite3 "file:$HOME/Library/Application%20Support/Anki2/Will/collection.anki2?mode=ro" \
  "SELECT 'col.usn=' || usn FROM col;
   SELECT 'cards_gt_col=' || SUM(CASE WHEN usn > (SELECT usn FROM col) THEN 1 ELSE 0 END) FROM cards;
   SELECT 'notes_gt_col=' || SUM(CASE WHEN usn > (SELECT usn FROM col) THEN 1 ELSE 0 END) FROM notes;
   SELECT 'revlog_gt_col=' || SUM(CASE WHEN usn > (SELECT usn FROM col) THEN 1 ELSE 0 END) FROM revlog;"
```

Any `*_gt_col > 0` means step 3 (normalize_usns) is pending.

## Safety envelope — always use it

Never call `sqlite3.connect` on `collection.anki2` directly. Use `app.anki.safety.safe_open(..., mode="rw"|"ro")`. It handles:
- Lock probe (aborts if Anki is running)
- SHA256 backup to `~/.tunatale/anki-backups/`
- Backup validation (integrity_check + row-count match)
- Post-write audit via `ctx.audit_changes`

## When building a new Anki migration

- Add it under `backend/app/anki/`, following the shape of `backfill_guids.py` or `migrate_homonyms.py`.
- Tests under `backend/tests/test_anki_<name>.py` — build minimal in-memory DBs, no real `collection.anki2`.
- If the migration bumps `col.scm`, the module docstring MUST point to this file.
- TDD red-green always (see `.claude/rules/tdd.md`).

## When building a new UI that adds cards

Any UI that originates cards in TT (the `/listen` lesson flow, a future LingQ-style unknown-word marker, manual add forms, etc.) must drop its rows into the same shape `sync_create_new` expects, or sync will skip them / mis-link them.

The contract:

- **Use `db.upsert_by_guid()` or `db.add_collocation()`.** Never write to `collocations` / `collocation_directions` with raw SQL. Those helpers compute the guid, set the schema invariants (including `due_date` ↔ `anki_due` consistency after the 2026-05 fix), and handle re-insert idempotency.
- **Set `card_type` correctly on the `SyntacticUnit`**: `"vocab"` (creates both `recognition` + `production` directions) or `"cloze"` (creates `production` only, routes through `OfflineWriter.create_cloze_note` against Anki's built-in Cloze notetype — see Phase F notes in root `CLAUDE.md`).
- **Leave `anki_note_id` and `anki_card_id` as `None`.** `sync_create_new` mints the Anki note via `OfflineWriter.create_note`, reads back the per-`ord` card ids, and writes them via `db.set_anki_ids` on success. A UI that pre-populates these will either link to the wrong Anki row or skip the create-new path entirely.
- **State must be `SRSState.NEW`.** `dirty_fsrs` stays 0; `last_review` stays NULL; `introduced_at` stays NULL until the first grade. The card is *added*, not *graded* — those are different events.
- **Want the card to appear on the same day?** It does, automatically: `get_review_queue` tail-appends NEW-state latecomers to the frozen `session_main_queue` (`app/api/srs.py` near line 1138). REVIEW-state latecomers are dropped (mirrors Anki excluding graduations from today's flow). Don't fight this — if you need a card to land mid-session, it must be NEW.

Canonical reference: `app/api/srs.py::listen` and its tests in `tests/test_api.py::TestListenClozeIntegration`. New UIs should follow the same shape end-to-end (`SyntacticUnit` → `upsert_by_guid` → wait for next sync → linked).

Tests for a new card-adding UI must cover:
- Round-trip through `sync_create_new` (use `_make_dual_collection_conn()` for vocab or `_make_cloze_collection_conn()` for cloze, both in `test_anki_sync_create_new.py`).
- Re-running the UI on the same input is idempotent (no duplicate collocations, no duplicate Anki notes).
- The card surfaces in `/review-queue` on the same day without requiring a sync (NEW-state tail-append).
