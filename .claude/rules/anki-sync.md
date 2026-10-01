---
paths:
  - "backend/app/plugins/anki_sync/**"
  - "backend/app/api/anki.py"
  - "backend/scripts/anki_archive/**"
  - "backend/tests/test_anki_*.py"
  - "backend/tests/test_e2e_listen_to_sync.py"
---

# Anki Sync Protocol

*The always-loaded hard invariants live in `anki-safety-core.md`; this file is the
full protocol.*

Any tool under `backend/app/plugins/anki_sync/` that writes to `collection.anki2`
must preserve AnkiWeb sync consistency. Skip this and the user's next sync
re-uploads hundreds of cards every time.

## The USN desync trap

Anki tracks sync state via `col.usn` plus a per-row `usn`:
- `row.usn = -1`: dirty, push on next sync
- `row.usn > col.usn`: Anki thinks "newer than the server knows" → push
- `row.usn <= col.usn`: clean

**Forced full uploads preserve local row USNs but reset `col.usn`.** After a full
upload, any row whose `usn` exceeds the new `col.usn` is seen as dirty forever,
so every later incremental sync re-uploads it. Anki has no self-repair.

## 4-step workflow for schema-changing migrations

A migration "bumps schema" when it modifies `col.scm` — e.g., adding a notetype
field or a field config. That forces AnkiWeb to demand a full upload.

1. **Run the migration.** It must also bump `notetypes.mtime_secs`, set
   `notetypes.usn = -1`, and update `col.scm`. Skipping any of these triggers
   Anki's "Check Database" on next open, which does the bumps itself and surprises
   the user.
2. **Tell the user: open Anki → File → Sync → Upload to AnkiWeb.** This is
   unavoidable after any `col.scm` change.
3. **After Anki closes, run `uv run python -m app.plugins.anki_sync.normalize_usns`.**
   It resets `cards.usn`, `notes.usn` and `revlog.usn` where they exceed
   `col.usn` back to `col.usn`. No content changes — it only aligns bookkeeping.
4. **Re-anchor TT's own sync mirror:**

   ```bash
   cd backend && uv run python -m app.plugins.anki_sync.sync_orchestrator --bootstrap
   ```

   Steps 1–3 only reconcile Anki ↔ AnkiWeb. TT keeps a **separate** collection at
   `~/.tunatale/tt_collection.anki2` (`settings.tt_collection_path`), a different
   file from the desktop collection. After the full upload that mirror is behind
   the server, so the next peer-sync aborts on the pull leg with:

       AnkiWeb requires a one-way FULL_SYNC (required=2) on the pull leg

   That abort is correct behaviour — peer-sync refuses rather than clobber.
   `--bootstrap` is download-only (`bootstrap_collection()` issues
   `create_collection` + `full_download` against `tt_collection_path`, never
   touching `anki_collection_path` and never pushing), so it cannot send a
   half-migrated collection anywhere. Every schema migration needs this step.

Data-only migrations (e.g., rewriting `notes.guid` with `notes.usn=-1`) stay
within incremental sync and do not need steps 2–4.

## Required writes for every mutation

When writing to Anki tables, always:
- **`notes`/`cards` mutation**: set `usn = -1` and `mod = now_ts` on every touched
  row. Without `usn=-1`, Anki's integrity check re-detects the change on next
  open and bumps `col.scm` itself, forcing a full sync.
- **`col` mutation**: `UPDATE col SET mod = ?` after any batch write. **Do not set
  `col.usn = -1`** (Layer 61). `col.usn` is the sync *anchor* — the server's last
  USN — not a per-row dirty flag. The content rows you touch
  (`cards`/`notes`/`revlog`/`decks`) each carry their own `usn = -1`, which is
  what actually pushes; bumping `col.mod` tells Anki the collection changed.
  Setting `col.usn` to `-1` is invisible on one device, but once another device
  (e.g. the phone) advances the server's USN, AnkiWeb cannot reconcile the
  desktop's `usn=-1` incrementally and **demands a full sync** (see `_bump_col` in
  `app/plugins/anki_sync/sync_writer.py`). The one-shot migration scripts that
  still write `col.usn=-1` are out of scope: they bump `col.scm` and force a
  one-way sync anyway.
- **Schema change (fields, notetypes)**: bump `notetypes.mtime_secs`, set
  `notetypes.usn = -1`, and `UPDATE col SET scm = ?` — all three, not just one.

## Deletes — the `graves` table

To delete notes or cards while keeping AnkiWeb in sync, write a grave row instead
of a bare `DELETE`. Anki's sync layer uses `graves` to tell the server what was
removed.

- `graves` columns: `oid INTEGER NOT NULL, type INTEGER NOT NULL, usn INTEGER NOT NULL`,
  `PRIMARY KEY (oid, type)`.
- `type` constants (from `rslib/src/storage/graves/mod.rs:13-19`): `0 = Card`,
  `1 = Note`, `2 = Deck`.
- One grave per card AND one grave per note (Anki's `remove_notes_inner` does
  this in `rslib/src/notes/mod.rs:502-515`). A note with two cards gets two
  `type=0` rows (the cids) and one `type=1` row (the nid).
- `usn=-1` on every new grave row (client-side; the server rewrites it during
  sync).
- Bump `col.mod` only. Don't set `col.usn=-1` (Layer 61, as above — the grave rows
  already carry `usn=-1`, which is what pushes), and don't touch `col.scm`:
  deletes are data-only, with no full upload. Because deletes leave `scm` alone,
  the one-shot-migration carve-out does not cover them. A delete that sets
  `col.usn=-1` leaves the collection silently armed: the next time a phone session
  advances the server's USN, the desktop sync demands a full download (with `scm`
  re-stamped mid-sync and 0 dirty rows). Pinned by
  `tests/test_anki_grave_ignored_lemma_cards.py::test_preserves_col_usn`.

Canonical pattern (mirror `scripts/anki_archive/delete_phonology_demos.py`):

```python
_GRAVE_KIND_CARD, _GRAVE_KIND_NOTE = 0, 1
card_ids = [r[0] for r in conn.execute("SELECT id FROM cards WHERE nid=?", (nid,))]
for cid in card_ids:
    conn.execute("INSERT OR REPLACE INTO graves (oid, type, usn) VALUES (?, ?, -1)", (cid, _GRAVE_KIND_CARD))
    conn.execute("DELETE FROM cards WHERE id=?", (cid,))
conn.execute("INSERT OR REPLACE INTO graves (oid, type, usn) VALUES (?, ?, -1)", (nid, _GRAVE_KIND_NOTE))
conn.execute("DELETE FROM notes WHERE id=?", (nid,))
conn.execute("UPDATE col SET mod=?", (int(time.time() * 1000),))  # NOT usn=-1 — Layer 61
```

Verify after writing: each deleted nid has exactly one `type=1` grave, and each
deleted cid exactly one `type=0` grave.

### Reading graves on pull — honoring Anki-side deletes (Layer 68)

The above is the write side (TT-originated deletes). On the read side,
`detect_and_reset_orphans` (`app/plugins/anki_sync/sync.py`) must distinguish an
**intentional Anki delete** from a wipe before deciding to recover a missing card.
It reads `OfflineReader.get_grave_note_ids()` (`graves WHERE type=1`): a note
grave means hard-delete the TT collocation (`db.delete_collocations_for_graves`);
a note missing *without* a grave means resurrect (reset pointers and re-mint, the
force-full-download net). **Don't "simplify" `detect_and_reset_orphans` to recover
every missing card** — that reintroduces the resurrection loop, where deleted
cards keep coming back. This is note-level only; a bare card grave on a
still-live note keeps the recovery path.

## Diagnostic (safe while Anki is open — read-only)

```bash
sqlite3 "file:$HOME/Library/Application%20Support/Anki2/Will/collection.anki2?mode=ro" \
  "SELECT 'col.usn=' || usn FROM col;
   SELECT 'cards_gt_col=' || SUM(CASE WHEN usn > (SELECT usn FROM col) THEN 1 ELSE 0 END) FROM cards;
   SELECT 'notes_gt_col=' || SUM(CASE WHEN usn > (SELECT usn FROM col) THEN 1 ELSE 0 END) FROM notes;
   SELECT 'revlog_gt_col=' || SUM(CASE WHEN usn > (SELECT usn FROM col) THEN 1 ELSE 0 END) FROM revlog;"
```

Any `*_gt_col > 0` means step 3 (`normalize_usns`) is pending.

## Safety envelope — always use it

Never call `sqlite3.connect` on `collection.anki2` directly. Use
`app.plugins.anki_sync.safety.safe_open(..., mode="rw"|"ro")`. It handles:
- Lock probe (aborts if Anki is running)
- SHA256 backup to `~/.tunatale/anki-backups/`
- Backup validation (integrity_check + row-count match)
- Post-write audit via `ctx.audit_changes`

## When building a new Anki migration

- Add it under `backend/app/plugins/anki_sync/`, following the shape of an
  existing migration there, such as `migrate_number_clozes.py` or
  `add_image_field.py`.
- Tests go under `backend/tests/test_anki_<name>.py` — build minimal in-memory
  DBs, never a real `collection.anki2`.
- If the migration bumps `col.scm`, the module docstring must point to this file.
- TDD red-green always (see `.claude/rules/tdd.md`).

## One sync sequence — never fork the phase list (the b0a4b8a class)

There is exactly **one** definition of "the steps a sync runs": `run_full_sync`
in `app/plugins/anki_sync/sync.py`. `sync.py` is the runner and re-export facade:
the `AnkiSync` engine is in `sync_engine.py`, collection I/O in
`sync_reader.py`/`sync_writer.py`, leaf helpers in `sync_common.py`; import and
patch through `app.plugins.anki_sync.sync`. It runs `warn_if_guid_collisions →
warn_if_recognition_only_deck → detect_and_reset_orphans → sync_create_new →
sync_push → sync_pull → promote_production_cards → (every refresh_* deck-config
sync + watch_fsrs_preset + Anki→TT media refresh + soak heartbeat)`. That list is
a reading aid, not the contract: the order is whatever `sync.py::run_full_sync`
does, and `tests/test_anki_sync_main.py::TestRunFullSync` pins it.

`warn_if_guid_collisions` is a report-only tripwire (it runs on dry-runs too). It
warns `GUID_COLLISION` when two Anki notes share the same `(text, POS)` and so
collapse to one TT guid, leaving one collocation with two candidate cards that
can alternate between them. It is keyed on text **plus disambig**, because POS
homonyms (`løfte` noun/verb) are legitimate and must not trip it. It pairs with
`RELINK_TRACE` in `db_sync.set_anki_ids`, which catches the alternation itself.

The single sync path funnels through `main()`:

- **`POST /api/anki/peer-sync`** (`app/api/anki.py`, the only HTTP sync endpoint)
  → `peer_sync` → `main` (`sync_orchestrator.py` → `sync.py:main`). This is the
  path the UI Sync button uses. It threads the LLM/image `media_fn` and the media
  dir through, and the active language via `_tt_settings(language_code)`
  (per-language db, deck and target_language).

`main()` is the internal reconcile driver that `peer_sync` calls, not a
standalone command. There is no sync CLI: peer-sync is the sole sync path,
single-language per request, so syncing two languages takes two UI syncs. The
automatic force-fsrs write path in `sync_push` (recovered / `KNOWN` /
`fsrs_force_next` cards) remains. Do not reintroduce a second HTTP sync path.

The only legitimate per-caller differences are `media_fn`/`media_dir` and the
`_tt_settings` language. Everything else lives in `run_full_sync`.

**Don't inline a sync phase into one entry point.** A second entry point that
runs a *different subset* of phases is the b0a4b8a regression. When the Sync
button was repointed to `peer_sync`, the peer reconcile ran only push and pull,
silently dropping **`sync_create_new`** (TT-added cards never reached Anki) and
**every `refresh_*`** (Anki-side FSRS-param, desired-retention, daily-cap and
load-balancer changes never reached TT). Unit tests and the 100% coverage gate
missed it: each function was green in isolation, and the orchestrator tests
patched `main`, so nothing crossed the seam.

A new sync phase goes in `run_full_sync`, not in a call site. Three independent
nets pin this, all run in CI:

1. **`tests/test_anki_sync_main.py::TestRunFullSync`** — the contract test. It
   asserts the full ordered phase set (including every `refresh_*` by name and the
   media-refresh phase) against a mocked sync object, and it is the only
   sanctioned place to pin the phase list.
2. **`tests/test_anki_sync_orchestrator.py::TestSociableSync`** — the b0a4b8a
   guard. The real `peer_sync` internals run against a real on-disk
   `SyntheticCollection` with only the `_run_driver` subprocess faked; an unlinked
   TT collocation must come out linked with a real `notes` row, so dropping a
   phase from `run_full_sync` turns it red.
3. **`test_anki_peer_sync_selfhost.py`** (`--run-peer-sync`, auto-started server)
   — full round-trips against a real sync server, including
   `test_media_round_trip_parity` (both media directions).

If you add a phase and only `TestRunFullSync` needs updating, you did it right. If
you find yourself editing an entry point's body, stop and move the change into
`run_full_sync`.

## When building a new UI that adds cards

Any UI that originates cards in TT (the `/listen` lesson flow, a future
LingQ-style unknown-word marker, manual add forms, and so on) must write its rows
in the shape `sync_create_new` expects, or sync will skip or mis-link them.

The contract:

- **Use `db.upsert_by_guid()` or `db.add_collocation()`.** Never write to
  `collocations` / `collocation_directions` with raw SQL. Those helpers compute
  the guid, set the schema invariants (including `due_date` ↔ `anki_due`
  consistency), and handle re-insert idempotency.
- **Set `card_type` correctly on the `SyntacticUnit`**: `"vocab"` (creates both
  `recognition` and `production` directions) or `"cloze"` (creates `production`
  only, routed through `OfflineWriter.create_cloze_note` against Anki's built-in
  Cloze notetype).
- **Leave `anki_note_id` and `anki_card_id` as `None`.** `sync_create_new` mints
  the Anki note via `OfflineWriter.create_note`, reads back the per-`ord` card ids,
  and writes them via `db.set_anki_ids` on success. A UI that pre-populates them
  will either link to the wrong Anki row or skip the create-new path entirely.
- **State must be `SRSState.NEW`.** `dirty_fsrs` stays 0, `last_review` stays
  NULL, and `introduced_at` stays NULL until the first grade. The card is *added*,
  not *graded* — those are different events.
- **Same-day appearance is automatic.** `get_review_queue` (`app/api/srs.py`)
  tail-appends NEW-state latecomers to the frozen `session_main_queue`;
  REVIEW-state latecomers are dropped, mirroring Anki excluding graduations from
  today's flow. If you need a card to land mid-session, it must be NEW.

Canonical reference: the listen route (`app/api/srs.py::mark_lesson_listened`) and
its tests in `tests/test_api_listen.py::TestListenClozeIntegration`. New UIs
should follow the same shape end to end (`SyntacticUnit` → `upsert_by_guid` →
wait for the next sync → linked).

Tests for a new card-adding UI must cover:
- A round-trip through `sync_create_new` (use `_make_collection_conn()` for vocab
  or `_make_cloze_collection_conn()` for cloze, both in
  `test_anki_sync_create_new.py`).
- Idempotency: re-running the UI on the same input creates no duplicate
  collocations and no duplicate Anki notes.
- The card surfaces in `/review-queue` on the same day without a sync (the
  NEW-state tail-append).
