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

3. ~~**One dangling media reference in `tunatale_sl.db`**~~ — **FIXED
   2026-08-12.** Row 723 (collocation 373, `skodelica`) pointed at
   `img_cup.jpg`, which was absent from `backend/media`, and it was the only one
   of 1379 Slovene media rows failing checksum verification.

   **The original diagnosis here was wrong and is worth recording, because the
   wrong one implied a much worse fix.** It read the co-located
   `img_cup_a7577283.jpg` as the same image after a hash-suffixed rename, which
   would have made the repair "repoint the row at the new name" — silently
   swapping the image on a card, and orphaning the `<img src="img_cup.jpg">`
   that `sync_engine.py` had already written into the Anki note.

   The suffix is not a rename tag. `cards/media/vocab_media.py` builds
   `{stem}_{sha256(data)[:8]}.{ext}`, so it is a *content* hash — and
   `a7577283…` is that file's own digest, not `8cf43f1d…`, the digest row 723
   recorded. They are two different pictures of a mug, both fetched under
   different naming schemes.

   The actual state was simpler and entirely recoverable: **`img_cup.jpg` still
   existed in Anki's `collection.media`, byte-identical to the recorded
   sha256.** TT's media tree had lost a file Anki still held. The fix was to
   copy it back — no database write, no Anki write, no hash change, nothing to
   re-sync. Verified: 1379/1379 Slovene media rows now match, live and restored.

   The generalisable lesson: **when a media row and a media file disagree, check
   Anki's media folder before concluding anything is lost.** It is a second
   copy of every file TT has ever pushed, and it is not covered by any of the
   reasoning about what is "regenerable".

   (`img_cup_a7577283.jpg` remains in `backend/media` with no `media` row
   pointing at it. Harmless, and left alone — an orphan file costs 26 KB, while
   deleting a file some Anki note might reference costs an image.)

## Off-box backups — restic into Backblaze B2

`scripts/backup_offbox.py` is the second line of defence: the rolling snapshots
above protect against application bugs, this protects against losing the
machine. It is a thin driver around [restic](https://restic.net), chosen over an
`rclone` mirror because restic's repository is content-addressed and versioned —
a mirror propagates a corruption or a deletion to the remote on its next run,
which is the failure this project has already had twice.

### What it ships, and why not the rolling snapshots

Sources are a **fresh** snapshot of each configured language DB, plus
`backend/media` and `backend/output`.

It deliberately does **not** back up `~/.tunatale/db-backups`. Those are written
at most once per calendar day, earliest-wins — a rule that exists so an
afternoon wipe cannot clobber the morning's good copy, and that is exactly wrong
for an off-box job: it would upload this morning's database tonight and report
success. `stage_db_snapshots` takes its own consistent snapshot at backup time
into `~/.tunatale/offbox-staging`, and restic's own snapshot history supplies
the time depth the rolling directory was providing.

Staging is swept on every run, so it is fenced twice against the one-typo
disaster of pointing `--staging` at `backend/` or at the rolling snapshot
directory: an ownership marker (`.tt-offbox-staging`) that a non-empty
unmarked directory will not get, and a name filter that only ever considers
`{stem}.{YYYY-MM-DD}.db`.

### Secrets

Three Keychain items, read at run time; nothing lives in the repo or in `.env`:

| Service | Account | Value |
|---|---|---|
| `tunatale-restic` | `repo-password` | restic repository passphrase |
| `tunatale-b2` | `key-id` | B2 `applicationKeyId` |
| `tunatale-b2` | `app-key` | B2 `applicationKey` |

```bash
openssl rand -base64 32 | pbcopy          # then paste into the password manager FIRST
security add-generic-password -s tunatale-restic -a repo-password -w
security add-generic-password -s tunatale-b2     -a key-id        -w
security add-generic-password -s tunatale-b2     -a app-key       -w
```

The bare `-w` makes `security` prompt, so no secret reaches shell history. A
missing item is reported with the exact `add-generic-password` line that fixes
it — an unattended job's stderr is the only user interface it has.

⚠️ **The passphrase must not live only on the machine being backed up.** The
Keychain copy exists so the job can run unattended; the durable copy lives in
the password manager. A restic repository whose passphrase is lost is
indistinguishable from no backup at all — there is no recovery path, by design.

The B2 application key needs `listBuckets`, `listFiles`, `readFiles`,
`writeFiles` and `deleteFiles`. A key missing `deleteFiles` works fine until
`forget --prune` runs weeks later. Set the bucket's lifecycle rule to **keep
only the last version**: restic never rewrites a file in place, so B2 versioning
buys nothing and quietly accumulates against the 10 GB free tier.

### Running it

```bash
cd backend
export TT_B2_BUCKET=<bucket>                       # or pass --bucket

uv run python scripts/backup_offbox.py init        # once, creates the repository
uv run python scripts/backup_offbox.py backup
uv run python scripts/backup_offbox.py snapshots
uv run python scripts/backup_offbox.py check       # repository integrity
uv run python scripts/backup_offbox.py restore --target ~/restore
```

`backup` validates every source **before** staging and before anything reaches
restic. Restic treats a vanished path as a warning and exits 3 having backed up
the rest; a silent partial backup is the failure this whole section exists to
prevent, so a missing tree refuses the run outright. Retention afterwards is
`--keep-daily 7 --keep-weekly 4 --keep-monthly 6 --prune`, and a `forget`
failure still fails the job even though the upload succeeded.

Every failure prints a `BACKUP FAILED` banner and exits non-zero.

### Verifying a remote backup

The restore drill is storage-agnostic on purpose — point it at what restic gave
back:

```bash
uv run python scripts/backup_offbox.py restore --target /tmp/b2-restore
uv run python scripts/restore_drill.py \
  --snapshot-dir /tmp/b2-restore/Users/<you>/.tunatale/offbox-staging \
  --media-src    /tmp/b2-restore/<repo>/backend/media \
  --output-src   /tmp/b2-restore/<repo>/backend/output \
  --scratch      /tmp/restore-drill
```

restic restores into the target under each source's absolute path, which is why
the three flags carry that prefix.

### Drill results — 2026-08-12, restored from B2

Performed against `b2:tunatale-backups:tunatale`, the first real upload. Same
machine, same drill script, same day as the local-snapshot drill above — so the
two tables are directly comparable.

| Step | Wall clock | Detail |
|---|---|---|
| `backup` (upload) | 32.7 s | 7576 files, 403.8 MiB read, **266.8 MiB stored** after compression |
| `check` (repository) | 2 s | `no errors were found` |
| `restore` (download) | 32.8 s | 7586 files/dirs, 403.8 MiB, 28 s of it transfer |
| Drill on the restored tree | 44.4 s | DBs 0.01 s, trees 3.00 s, 6001 checksums 0.29 s, 48 full decodes 40.23 s |
| **Total, bucket → verified** | **≈ 77 s** | |

**The download did not dominate.** Phase 1 predicted the transfer would be the
RTO's main term; measured, it is 28 s against 40 s of audio decoding. At 403 MiB
this is a sub-two-minute recovery on a home connection — the number the
provisioning runbook should state, with the caveat that it is bandwidth-bound
and a datacenter restore will differ in whichever direction that box's link does.

Compression is worth noting for capacity planning: 403.8 MiB of sources occupy
266.8 MiB in the repository, so the 10 GB free tier holds roughly 38 full copies
before deduplication is even counted. Retention is not close to binding.

Verified on the restored copy:

- `integrity_check` — `ok` on both DBs
- row counts **identical to the local drill**: Norwegian 37253 rows across 17
  tables (3014 collocations, 3033 directions, 24184 `tt_revlog`, 6001 media, 6
  lessons, 1 curriculum); Slovene 20636 rows across 17 tables
- 6001/6001 Norwegian media files matched their recorded sha256
- 48/48 lesson audio files decoded end to end across 6 lessons (7.4 h), 0 decode
  errors, 0 caption overruns
- Booted the app on port 8099 against the restored DB. `/api/srs/stats`
  `{"total":3014,"due_today":94}`, `/api/srs/queue-stats`
  `{"new":3,"learning":0,"review":95,...}` and the curriculum list
  (`reading-sn-mannen-022a5dc3`, created `2026-08-02 01:22:20`) were **identical**
  to the live server queried the same minute

The first run of this drill exited **non-zero** on one line — Slovene media
`1378/1379 matched, 1 missing (img_cup.jpg)`, i.e. Known gap #3 above, faithfully
reproduced from the live tree. That file was recovered from Anki's
`collection.media` (see gap #3 for why that was the right fix and the plausible
wrong one), and the drill was re-run end to end against a fresh backup and a
fresh restore:

| Step | Wall clock | Detail |
|---|---|---|
| `backup` #2 (incremental) | **7.5 s** | one new 45 KB file; everything else deduplicated |
| `restore` | 29.4 s | 7587 files/dirs, 403.9 MiB, 26 s of it transfer |
| Drill | 42.5 s | DBs 0.02 s, trees 2.65 s, checksums 0.22 s, 48 decodes 38.83 s |

`=== DRILL PASSED ===`, exit 0, with Slovene media at **1379/1379 matched, 0
missing, 0 mismatched** and every other check unchanged.

Two things that run tells you beyond the fix itself. The incremental backup cost
**7.5 s against 32.7 s** for the initial one, so a daily job is cheap and the
free tier is nowhere near binding. And because the drill now exits 0 on this
data, it is usable as a monitor's pass/fail signal — a scheduled job can gate on
it directly, with no allowance list of accepted pre-existing failures.

The `[note] 41 pre-existing FK orphan row(s)` line is deliberately a note and not
a failure (gap #2); it does not affect the exit code.

### Not done yet

Scheduling. The job runs on demand today; a `launchd` agent with loud failure
reporting is deferred to the monitoring work, and until it lands **nothing runs
this for you**. That is the single largest remaining hole: a backup you have to
remember is one you will stop taking.

Also unproven off this machine: the restore has only ever been performed on the
Mac that made the backup. Restoring onto a *different* box — the actual disaster
scenario — additionally requires the passphrase from the password manager, which
is precisely the step this arrangement is designed around and the one that has
not been exercised.

## Schema rollback

### Image rollback is not schema rollback

"Roll back to a previous SHA in one command" is true of the image and false of
the data. Starting a build runs `app/srs/migrations.py::migrate` against the
existing volume, so a deploy that advances the schema and is then rolled back
leaves a **newer schema under older code** — a different and worse failure than
the one being rolled back from. The schema only ever moves forward: `migrate`'s
loop is `while version < CURRENT_VERSION`, and there are no down-migrations.

Two mechanisms close that gap.

**1. A pre-migration snapshot.** Before the first pending migration runs,
`migrate` copies the database to
`{migration_backup_dir}/{stem}.pre-v{N}.db`, where `N` is the version being
left behind — so the filename says which build can still open it. Written via
the same online-backup API as the rolling snapshots (`db_backup.py::_snapshot`),
so the file is self-contained; a restore copies the `.db` alone and ignores any
`-wal`/`-shm` beside it.

Three properties are deliberate and each has a test in
`backend/tests/test_pre_migration_backup.py`:

- **It never rotates.** `migration_backup_dir` defaults to
  `~/.tunatale/pre-migration-backups`, separate from the rolling
  `~/.tunatale/db-backups`, which keeps only `db_backup_keep_days` (5). You
  find out you need a pre-migration snapshot long after that window. Pruning is
  additionally scoped to date-shaped filenames, so co-locating the two
  directories would still be safe.
- **The first snapshot of a version wins.** A migration that fails half-way
  leaves the DB mutated; the retry re-enters at the same version and must not
  copy the damaged file over the good one.
- **A failure to snapshot aborts the migration.** This is the opposite of
  `rotate_db_backups`, which swallows every error so a backup hiccup cannot
  block startup. Here, migrating without a snapshot would destroy the only copy
  of the pre-migration state — the precise outcome the mechanism exists to
  prevent. Since it only fires when a migration is actually pending, the
  availability cost is bounded to deploys that change the schema.

**2. A refusal to run older code against a newer DB.** `migrate` raises
`SchemaTooNewError` when `PRAGMA user_version` exceeds the build's
`CURRENT_VERSION`, naming both versions and the snapshot that would fix it.
Refusing to start beats a silent mixed-state boot.

Check it *before* swapping, so a bad rollback declines instead of crash-looping:

```bash
cd backend && uv run python scripts/check_schema_compat.py
# exit 0 = safe to start; 1 = a DB is ahead of this build; 2 = a named DB is missing
```

The script imports the same comparison the runtime guard uses rather than
reimplementing it, and reads `PRAGMA user_version` straight off the files — it
needs nothing running.

### Which migrations are reversible

**Reversible**, below, means: after the migration has run, an older build (one
whose `CURRENT_VERSION` is the pre-migration number) can be pointed back at the
*same file* — after resetting `PRAGMA user_version` — without losing data it
knew about. It does **not** mean a down-migration exists; none does.

Three classes:

- **Additive** — new column, table, or index only. The older build ignores it.
  Reversible.
- **Backfill** — writes rows, but only where the target was `NULL` or empty. No
  pre-existing value is destroyed, so nothing is lost; it is not *mechanically*
  undoable, because nothing records which rows were blank.
- **Destructive** — drops, deletes, or overwrites pre-existing values, or
  rebuilds a table. **Not reversible: the pre-migration snapshot IS the
  rollback.**

26 of the 42 are additive, 3 are backfills, and 13 are destructive.

| From → to | Class | What it does |
|---|---|---|
| v0 → v1 | Additive | `collocations.lemma` + index |
| v1 → v2 | **Destructive** | Splits FSRS state into `collocation_directions`; rebuilds `collocations`; drops `_collocations_v1`; synthesizes a production direction per row |
| v2 → v3 | **Destructive** | Rewrites `collocations.text` (strips the `(suffix)` into a new `disambig_key`) and recomputes every guid. The original `text` values do not survive |
| v3 → v4 | **Destructive** | Rebuilds `collocation_directions` / `media` / `collocation_tags` to repair FK targets. Every row is copied — no data loss — but the pre-repair tables are dropped |
| v4 → v5 | Additive | `last_rating` |
| v5 → v6 | Additive | `anki_due` |
| v6 → v7 | Additive | `grammar`, `note` |
| v7 → v8 | Additive | `source_sentence`, `source_lesson_id`, `source_line_index` |
| v8 → v9 | **Destructive** | `DROP TABLE pending_revlog` (+ index). Unused at the time, but the drop is unconditional |
| v9 → v10 | Additive | `last_review_time_ms` |
| v10 → v11 | Additive | `left`, `due_at` |
| v11 → v12 | **Destructive** | Nulls `last_review` on `state='new' AND reps=0` rows. The prior timestamps are gone |
| v12 → v13 | Additive | `prior_state`, `prior_left`, `prior_stability` |
| v13 → v14 | Additive | `anki_card_mod` |
| v14 → v15 | Backfill | `lemma = LOWER(text)` for single-word rows, `WHERE lemma IS NULL` |
| v15 → v16 | **Destructive** | Deletes phantom direction rows (the `_build_directions` auto-fill residue) |
| v16 → v17 | Additive | Index on `collocations.created_at` |
| v17 → v18 | Additive | `introduced_at` + index |
| v18 → v19 | Additive | `card_type` |
| v19 → v20 | Additive | `bury_kind`; its backfill writes only into that new column |
| v20 → v21 | Additive | `sentence_translation` |
| v21 → v22 | **Destructive** | Rebuilds `media` to drop the `kind` CHECK. All rows copied; old table dropped |
| v22 → v23 | **Destructive** | Appends `audio` to `collocations.dirty_fields` on matching rows — including non-empty ones |
| v23 → v24 | Backfill | Fills `due_at` from `due_date`, `WHERE due_at IS NULL`. `due_date` still exists at this version |
| v24 → v25 | **Destructive** | Drops `collocation_directions.due_date`. The clearest one-way door in the chain |
| v25 → v26 | Additive | `tt_revlog` table |
| v26 → v27 | Additive | `stability_replayed`, `fsrs_difficulty_replayed` |
| v27 → v28 | Additive | `lemma_key` |
| v28 → v29 | Backfill | Fills `grammar` for inflection clozes, `WHERE grammar IS NULL OR grammar = ''` |
| v29 → v30 | Additive | `ignored_lemmas` table |
| v30 → v31 | Additive | `known_prior_state`, `known_prior_stability`, `known_prior_due_at`, `fsrs_force_next` |
| v31 → v32 | **Destructive** | Drops `stability_replayed` / `fsrs_difficulty_replayed` |
| v32 → v33 | Additive | `article` |
| v33 → v34 | Additive | `extras` |
| v34 → v35 | **Destructive** | Rebuilds `collocation_directions` to add CHECK domains, and nulls out-of-domain `prior_state` / `bury_kind` |
| v35 → v36 | **Destructive** | Overwrites `word_count` to 1 for comma-separated spelling-variant fronts |
| v36 → v37 | Additive | `media.mtime_ns` + index on `collocations.anki_note_id` |
| v37 → v38 | Additive | `lesson_listens` table |
| v38 → v39 | Additive | `lesson_reviews` table |
| v39 → v40 | Additive | `tt_revlog.budget_neutral` |
| v40 → v41 | Additive | `pending_listen_grades` table |
| v41 → v42 | **Destructive** | Rebuilds `pending_listen_grades` to widen the UNIQUE key to `(lesson_id, collocation_id, direction)` |

`test_pre_migration_backup.py::TestReversibilityIsDocumented` fails if a new
migration lands without a row here, so the table cannot silently fall behind
`CURRENT_VERSION`.

### Restoring

```bash
# 1. Refuse-check the build you are rolling back to.
cd backend && uv run python scripts/check_schema_compat.py

# 2. If it refuses, restore the snapshot it names. Stop the app first.
cp ~/.tunatale/pre-migration-backups/tunatale_sl.pre-v41.db backend/tunatale_sl.db

# 3. Re-check: it should now report ok (or "pending", if you rolled forward again).
uv run python scripts/check_schema_compat.py
```

Copy the `.db` alone — see *Snapshots are self-contained* above.

**What this costs.** Restoring a pre-migration snapshot discards every write
made since that migration ran. For a destructive migration there is no better
option; for an additive one you do not need the snapshot at all — reset
`PRAGMA user_version` to the older build's number and the only thing lost is
whatever the new column held. Reach for the snapshot when the table above says
**Destructive**.
