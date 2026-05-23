# Anki disaster recovery

If TT corrupts your `collection.anki2`, this is the recovery sequence. The safety envelope writes a timestamped backup every time TT mutates Anki, so you should never be more than one mutation behind a known-good state.

The point of this doc is to make the recovery procedure cold-readable when you're stressed and Anki won't open. **Read it before you need it.**

## Where backups live

`backend/app/anki/safety.py::safe_open(..., mode="rw")` writes a backup to `settings.anki_backup_dir` before every mutation. Default: `~/.tunatale/anki-backups/`. Filenames look like:

```
collection.anki2.bak_20260424_132004
collection.anki2.bak_20260424_132004_KNOWN_GOOD_post_S3
collection.anki2.bak_20260516_204512
```

The `_KNOWN_GOOD_` suffix is a manual rename — when a backup represents a verified-good state, the operator appends a note. Use this for any backup you've spot-checked. The bare timestamp backups are produced automatically.

Every backup is validated by `_validate_backup` at write time: it must pass `PRAGMA integrity_check` and have a row count matching the source's `notes` table. A failed validation deletes the backup and raises — so files in the backup dir are SQLite-valid as of their creation.

## When to suspect corruption

- Anki refuses to open the collection.
- Anki opens but the deck list is empty or shows the wrong card counts.
- `PRAGMA integrity_check` on `collection.anki2` returns anything other than `ok`.
- TT's `sync_pull` reports a sudden mass of `skipped_unknown_guid` errors.
- The TT/Anki badge gap is hundreds of cards rather than the usual ±10 (PART 18.x territory). Mid-double-digit drift is parity, not corruption.

## The recovery sequence

### Step 0 — Stop everything

Close Anki. Quit any TT process. Don't run sync. Don't run anything against `collection.anki2`. **If Anki is open, the WAL is mutating the file and you cannot safely copy it.**

```bash
pgrep -f Anki && killall Anki
pgrep -f uvicorn && killall uvicorn
```

### Step 1 — Snapshot the current corrupted state

Don't delete the broken file yet. Copy it somewhere clearly named so you can diff against it later:

```bash
cp "$HOME/Library/Application Support/Anki2/Will/collection.anki2" \
   "/tmp/collection.anki2.corrupted_$(date +%Y%m%d_%H%M%S)"
```

Also copy `-shm` and `-wal` sidecars if they exist — Anki's WAL has uncommitted state worth inspecting later.

### Step 2 — Identify the most recent known-good backup

```bash
ls -lt ~/.tunatale/anki-backups/ | head -20
```

Pick the most recent timestamp **before** the corrupting event. If you don't know when the corruption happened, work backward: validate each candidate before restoring.

To verify a backup is intact without restoring it:

```bash
BACKUP=~/.tunatale/anki-backups/collection.anki2.bak_<timestamp>
sqlite3 "$BACKUP" "PRAGMA integrity_check;"               # must return: ok
sqlite3 "$BACKUP" "SELECT COUNT(*) FROM notes;"            # spot-check row count
sqlite3 "$BACKUP" "SELECT usn FROM col;"                   # remember this value
sqlite3 "$BACKUP" "SELECT mod, scm FROM col;"              # collection mtime + schema timestamp
```

If `integrity_check` returns anything other than `ok`, that backup is also broken. Try the next one.

### Step 3 — Restore

```bash
# Move the corrupted file aside (don't delete — you may want it for forensics)
mv "$HOME/Library/Application Support/Anki2/Will/collection.anki2" \
   "$HOME/Library/Application Support/Anki2/Will/collection.anki2.broken_$(date +%Y%m%d_%H%M%S)"

# Restore the backup
cp ~/.tunatale/anki-backups/collection.anki2.bak_<timestamp> \
   "$HOME/Library/Application Support/Anki2/Will/collection.anki2"

# Clean up the WAL sidecars so SQLite doesn't try to replay them
rm -f "$HOME/Library/Application Support/Anki2/Will/collection.anki2-shm"
rm -f "$HOME/Library/Application Support/Anki2/Will/collection.anki2-wal"
```

### Step 4 — Open Anki and verify

Open Anki. Check the deck list shows the expected card counts. Open a couple of cards. If anything looks wrong, return to step 2 with the next-older backup.

### Step 5 — Sync to AnkiWeb

You restored a snapshot from your local TT backup, not from AnkiWeb. AnkiWeb's copy is whatever it last received from any device. The two disagree.

**If the restored backup is newer than AnkiWeb's state**: File → Sync → choose "Upload to AnkiWeb" when prompted. This makes your restored state authoritative.

**If AnkiWeb's state is newer than your restored backup** (e.g., you graded on a different device since the backup was taken): File → Sync → choose "Download from AnkiWeb." You'll lose any TT mutations the backup carried that hadn't synced yet.

The conflict-resolution prompt is unavoidable after a restore. Choose deliberately based on which side has the data you actually want.

### Step 6 — Normalize USNs and re-sync TT

After any forced upload/download, run the post-schema-bump USN normalization (per `.claude/rules/anki-sync.md`):

```bash
cd backend
uv run python -m app.anki.normalize_usns
```

Verify with the read-only diagnostic at the bottom of `.claude/rules/anki-sync.md`:

```bash
sqlite3 "file:$HOME/Library/Application%20Support/Anki2/Will/collection.anki2?mode=ro" \
  "SELECT 'col.usn=' || usn FROM col;
   SELECT 'cards_gt_col=' || SUM(CASE WHEN usn > (SELECT usn FROM col) THEN 1 ELSE 0 END) FROM cards;
   SELECT 'notes_gt_col=' || SUM(CASE WHEN usn > (SELECT usn FROM col) THEN 1 ELSE 0 END) FROM notes;
   SELECT 'revlog_gt_col=' || SUM(CASE WHEN usn > (SELECT usn FROM col) THEN 1 ELSE 0 END) FROM revlog;"
```

All `*_gt_col` values should be 0. If they aren't, `normalize_usns` failed and you should investigate before any further sync.

Now run TT sync from the UI's Sync button. It should re-link your TT directions to the restored Anki cards. If TT's DB references `anki_card_id`s that no longer exist (because the restored backup predates some TT-created cards), expect `skipped_unknown_guid` warnings and possibly orphan rows — those are recoverable; see "After-recovery cleanup" below.

## When the backup is from a known-good moment

For checkpoints you've manually labelled (`*_KNOWN_GOOD_*`), the recovery is the same but you can skip the spot-check in step 2 — that was done at label time. Steve's-good-backup-from-April pattern: rename important backups at the moment you trust them.

To create a labeled checkpoint:

```bash
LATEST=$(ls -t ~/.tunatale/anki-backups/collection.anki2.bak_2* | head -1)
mv "$LATEST" "${LATEST}_KNOWN_GOOD_post_<your-label>"
```

## After-recovery cleanup

If the restored state is older than some TT-created cards/notes, you'll have TT rows referencing Anki IDs that no longer exist. Symptoms:

- Sync reports `skipped_unknown_guid` for several cards.
- TT shows phantom rows in `/admin/srs` that don't appear in Anki.
- The dedup script (`backend/app/anki/dedup_tt_revlog.py`) reports orphans.

Resolution:

```bash
# 1. Sync once to let TT discover which IDs are gone
# (the Sync button in the UI runs sync_push → sync_pull)

# 2. Run the orphan-recovery procedure
cd backend
uv run python -m app.anki.merge_dupes --dry-run        # surface duplicate Anki notes
uv run python -m app.anki.repair_nested_homonyms       # if homonym pairs ended up split
```

The sync code's orphan-recovery path (`9cf4782` — see PART 21.2 of walkthrough.md) is supposed to either re-link by guid or stage for cleanup automatically. If you have orphans surviving a sync, that's a sync-code bug, not a recovery-procedure problem.

## Belt-and-braces: when you don't trust any local backup

If every TT backup is suspect (e.g., the corruption happened weeks ago and propagated through every snapshot), AnkiWeb is your last fallback. AnkiWeb keeps one server-side state per profile, mutated by whichever device last synced. If your phone hasn't synced in two weeks, you may be able to:

1. Disable network on your phone.
2. Open Anki on the phone — its local state is from before the corruption.
3. Force-upload from the phone (File → Sync → Upload).
4. Sync down to desktop from AnkiWeb.

This is not a TT-specific procedure — it's general Anki disaster recovery — but it's the right move when local backups are exhausted.

## What NOT to do

- **Don't restore while Anki is open.** WAL replay will corrupt the restored file too.
- **Don't `rm -rf ~/.tunatale/anki-backups/`** even after a successful recovery. Multiple backups protect against multiple-step regressions.
- **Don't skip step 5 (forced sync).** If you restore locally without telling AnkiWeb, your next sync from any device will be a USN-protocol failure — see `.claude/rules/anki-sync.md` "The USN desync trap."
- **Don't `--no-verify` past a hook failure.** If `./test.sh` fails after a recovery, that's a signal the restored state has a real schema issue worth investigating.

## Cross-references

- `.claude/rules/anki-sync.md` — USN protocol, safety envelope, schema-bump workflow.
- `backend/app/anki/safety.py` — `safe_open`, backup validation, post-write audit.
- `backend/app/anki/normalize_usns.py` — the post-restore USN normalizer.
- `walkthrough.md` PART 12.2 — Safety Envelope deep-dive.
- `walkthrough.md` PART 21.2 — orphan recovery in sync_push.
