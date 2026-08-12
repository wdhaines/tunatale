# Deployment

Operational runbook for running TunaTale somewhere other than the author's
laptop. Built incrementally alongside the `Deploy` epic; sections appear as the
corresponding work lands.

Provisioning, auth, and cutover are not written yet. What follows is the part
that already applies to the laptop today.

## Backups and restore

### Why this section exists before the deployment does

A backup that has never been restored is not a backup. This project has wiped
its curricula twice — 2026-06-30 and 2026-07-13, both from an E2E run pointed at
the real DB by a `DATABASE_URLS` casing bug — which is why the restore path is
treated as the risky half and was drilled before any off-box storage existed.

### What is irreplaceable

| Path | Size | Why it cannot be regenerated |
|---|---|---|
| `backend/tunatale_no.db` | 18 MB | curricula, lessons, FSRS state not mirrored in Anki |
| `backend/tunatale_sl.db` | 2.9 MB | same, Slovene |
| `backend/media` | 332 MB | Forvo/Pixabay fetches; upstream is not guaranteed to still serve them |
| `backend/output` | 68 MB | rendered lesson audio |

"Regenerable" does not hold up as a reason to skip the trees: audio rendering
depends on `edge-tts`, an unofficial endpoint that breaks at home and is
filtered from datacenter IPs, and text regeneration is bounded by Groq's free
tier (200K tokens, 1K requests/day). Rebuilding months of content is weeks of
budget at best.

Total payload is ~420 MB against a 10 GB free tier — 25× headroom, so there is
nothing to ration and no selection decision to make.

### The existing rolling snapshots are not off-box backups

`app/storage/db_backup.py::rotate_db_backups` snapshots each content DB once per
calendar day into `~/.tunatale/db-backups`, keeping `db_backup_keep_days` (5).
It runs at app startup and never raises, so a backup problem cannot block boot.

**These live on the same disk as the DBs they protect.** They defend against
application bugs and stray test runs — the failure mode that actually happened
twice — and not at all against losing the machine. They are the *input* to the
drill below, not a substitute for off-box storage.

### Snapshots are self-contained — ignore the sidecars

`rotate_db_backups` writes each snapshot with SQLite's online-backup API
(`Connection.backup`), which materialises everything into the `.db` file. The
`-wal` / `-shm` files that appear next to snapshots are **inspection
artifacts**: they are created the moment anything opens a snapshot, including a
read-only `sqlite3` query.

Verified 2026-08-12: a snapshot `.db` copied alone, with no sidecars, produced
row-for-row identical counts to the same snapshot read in place with its
sidecars present. Separately, opening a snapshot during the drill created a
zero-byte `-wal` beside it while leaving the `.db` byte-identical.

**Do not chase the WAL when restoring.** Copy the `.db`. A restore procedure
built around "remember the sidecars" would be protecting against nothing and
would obscure the real failure modes.

### Running the drill

```bash
cd backend
uv run python scripts/restore_drill.py --scratch /tmp/restore-drill
```

Read-only with respect to every source; everything lands under `--scratch`,
which is wiped per run. Exit code is non-zero on any failure, so it can be a
cron or CI gate. It checks:

- newest snapshot per DB stem, restored `.db`-only, timed
- `PRAGMA integrity_check` and `foreign_key_check`
- row counts per table
- every `media` row's recorded `sha256` against the restored tree — cryptographic
  verification, not a file count
- every `audio_files` entry full-decoded with `ffmpeg -f null`, so the check is
  playability rather than "the header parses", plus a check that the caption
  timeline does not run past the audio

Point `--snapshot-dir` at a restic/rclone-restored tree to run the identical
drill against an off-box backup. That is the intended Phase 2 use.

The app-boot step is printed at the end rather than automated, because it needs
a free port and a comparison against the running server.

### Drill results — 2026-08-12, local snapshots

Performed against `~/.tunatale/db-backups` snapshots dated 2026-08-12, restored
into a scratch directory, on the author's Mac (Darwin 25.5.0, APFS SSD).

| Step | Wall clock |
|---|---|
| Restore both DBs (21 MB) | 0.01 s |
| Restore `media` + `output` trees (400 MB) | 3.25 s |
| Verify 6001 media checksums | 0.23 s |
| Full-decode 48 audio files (7.4 h of audio) | 38.97 s |
| **Total** | **43.34 s** |

**RTO for the data layer is under a minute** once the bytes are local. The
provisioning runbook should state the download time from the chosen remote as
the dominant term, not the restore itself — at 420 MB that is bandwidth-bound.

Verified on the restored copy:

- `integrity_check` — `ok` on both DBs
- 6001/6001 Norwegian media files matched their recorded sha256; 0 missing, 0
  mismatched
- 48/48 lesson audio files decoded end to end across 6 lessons, 0 decode errors,
  0 caption overruns
- Booted the app against the restored DBs on port 8099 and compared against the
  live server: `/api/srs/stats` `{"total":3014,"due_today":94}` and
  `/api/srs/queue-stats` `{"new":3,"learning":0,"review":95,...}` were **identical**
  on both, and the curriculum list matched

Row counts that came back (Norwegian): 3014 collocations, 3033 directions, 24184
`tt_revlog`, 6001 media, 6 lessons, 1 curriculum — 37253 rows across 17 tables.
Slovene: 734 collocations, 17115 `tt_revlog`, 1379 media, 20636 rows total.

### Known gaps found by the drill

These are pre-existing conditions the drill surfaced, confirmed present in the
live data by the same query. None is a backup fault — a faithful restore
reproduces the source warts and all — but each is worth fixing.

1. **The restored media tree cannot be served.** `MEDIA_DIR` is ignored by the
   serving route: `api/srs.py::serve_media` reads a module-level
   `_MEDIA_DIR = Path(__file__).parent.parent.parent / "media"`, and
   `app.state.audio_dir` is hardcoded to `_BACKEND_DIR / "output/audio"` in
   `main.py`. Demonstrated by setting `MEDIA_DIR` to the restored tree, altering
   a file there, and confirming the API still served the original bytes from
   `backend/media`. Meanwhile `settings.media_dir` *is* read on the import side
   (`media/importer.py`, `plugins/anki_sync/import_seed.py`), so the two halves
   can silently diverge — imports land in one directory while serving reads
   another. They coincide today only because the default `./media` resolves to
   the same place under the dev CWD. Tracked by the container-safe mutable paths
   work.

2. **41 foreign-key orphans in `tunatale_no.db`** — 25 `tt_revlog` rows
   referencing absent `collocation_directions`, 16 `media` rows referencing
   absent `collocations`. `PRAGMA foreign_keys` is `0` (SQLite's default), so
   nothing enforces these at write time. Identical row-for-row in live and
   restored, so pre-existing.

3. **One dangling media reference in `tunatale_sl.db`** — row 723
   (collocation 373, `skodelica`) points at `img_cup.jpg`, which is not on disk;
   `img_cup_a7577283.jpg` is. A hash-suffixed rename left this row behind. It is
   the only one of 1379 Slovene media rows that fails checksum verification.

### Not done yet

Off-box storage (Phase 2) — creating the bucket, choosing where the encryption
key lives, pointing a scheduled job at it, and re-running the drill above
against a remote restore. The drill is deliberately storage-agnostic so that
step changes only `--snapshot-dir`.

The encryption key must not live only on the machine being backed up.
