# Phase F — Function-word cloze smoke test

End-to-end manual test using Lesson 1 (Day 1) of the `arrival-in-ljubljana-5f8c0f52` curriculum, "Arrival in Ljubljana." This lesson contains four function words that should produce cloze cards on /listen — `kje`, `ja`, `seveda`, `brez` — which were hard-deleted from both TT and Anki on 2026-05-12 so Phase F can mint fresh cloze versions without GUID conflicts.

## What this verifies

1. Phase F's `/listen` integration detects function words against `SLOVENE_FUNCTION_WORDS` and routes them to `card_type='cloze'`.
2. `add_collocation` skips the production direction for cloze items (recognition only).
3. `sync_create_new` branches by `card_type` and writes via `create_cloze_note` to Anki's built-in Cloze notetype.
4. `make_cloze_text` correctly wraps the surface form with `{{c1::word}}` in `source_sentence`.
5. The DB-backed feature flag survives across requests and persists in `anki_state_cache`.
6. Round-trip: grading a cloze card in Anki and syncing back marks the TT row clean.

## What this does NOT verify

- The 18 other curated function words (`je`, `v`, `sem`, `kako`, `si`, `to`, `da`, `na`, `tam`, `ni`, `vam`, `z`, `mi`, `še`, `pa`, `ti`, `po`, `kaj`) — they're still imported as `source='anki'` vocab rows, so the GUID conflict will prevent Phase F from firing. Step 6 explicitly checks that NO cloze rows appear for them.
- Forvo / Pixabay media — cloze cards don't need media; the sentence is the prompt.

## Setup

```bash
./start-dev.sh   # backend :8000, frontend :5173
```

Anki must be **closed** before any sync step. The auto-backup envelope (`safe_open`) will refuse to write if Anki is running.

## 1. Confirm the flag is on

Open `http://localhost:5173/admin/srs`. Under **Feature flags**, confirm **Function-word cloze cards (Phase F)** is checked. If not, check it.

Verify via DB:
```bash
sqlite3 /Users/wdhaines/CascadeProjects/tunatale/backend/tunatale.db \
  "SELECT value FROM anki_state_cache WHERE key='enable_cloze_cards';"
# Expect: true
```

## 2. Pre-state snapshot

Confirm no Phase F cloze rows exist yet (the four target words were deleted on 2026-05-12):

```bash
sqlite3 /Users/wdhaines/CascadeProjects/tunatale/backend/tunatale.db <<'EOF'
SELECT text, card_type, source_sentence, anki_note_id
FROM collocations
WHERE card_type = 'cloze';
EOF
# Expect: zero rows
```

Confirm the four target words are absent (they were deleted, so no rows):

```bash
sqlite3 /Users/wdhaines/CascadeProjects/tunatale/backend/tunatale.db <<'EOF'
SELECT text FROM collocations WHERE text IN ('kje', 'ja', 'seveda', 'brez');
EOF
# Expect: zero rows
```

Note the current `col.usn` (you'll re-check it after sync):
```bash
sqlite3 ~/Library/Application\ Support/Anki2/Will/collection.anki2 \
  "SELECT 'col.usn=' || usn FROM col;"
```

## 3. Listen to Lesson 1

Navigate to the lesson page for **Day 1 — "Arrival in Ljubljana"** in the `arrival-in-ljubljana-5f8c0f52` curriculum. Click **Mark as Listened**.

The NATURAL_SPEED dialogue contains these phrases (among others) that should each generate a cloze:

| Phrase | Function word | Expected cloze front |
|---|---|---|
| "Zdravo, kje ste?" | `kje` | `Zdravo, {{c1::kje}} ste?` |
| "Ja, še nisem videl." | `ja` | `{{c1::Ja}}, še nisem videl.` |
| "Seveda, hvala." | `seveda` | `{{c1::Seveda}}, hvala.` |
| "Brez problema, rada." | `brez` | `{{c1::Brez}} problema, rada.` |

(`make_cloze_text` is case-insensitive in matching but case-preserving in output, so `Ja` keeps its capital `J`.)

The same lesson also contains `kje` and `Kje` in three other phrases ("Kje boste ostali?", "Hvala, Ana. Kje greš?"). Phase A's idempotency means only **one** cloze row should be created per lemma — the first encountered phrase wins.

## 4. Verify TT cloze rows

```bash
sqlite3 /Users/wdhaines/CascadeProjects/tunatale/backend/tunatale.db <<'EOF'
SELECT text, card_type, source_sentence, anki_note_id, datetime(created_at) as created
FROM collocations
WHERE card_type = 'cloze'
ORDER BY created_at DESC;
EOF
```

**Expect exactly 4 rows**, one per target word, each with:
- `card_type = 'cloze'`
- `source_sentence` populated with the containing NATURAL_SPEED phrase (e.g., `Zdravo, kje ste?`)
- `anki_note_id IS NULL` (not yet synced)
- Recent `created_at`

## 5. Verify single-direction creation

Cloze items must skip the production direction (matches `d306311`'s no-phantom-direction rule for single-template notetypes):

```bash
sqlite3 /Users/wdhaines/CascadeProjects/tunatale/backend/tunatale.db <<'EOF'
SELECT c.text, d.direction, d.state, d.dirty_fsrs
FROM collocations c
JOIN collocation_directions d ON d.collocation_id = c.id
WHERE c.card_type = 'cloze'
ORDER BY c.text, d.direction;
EOF
```

**Expect**: one row per cloze collocation (4 rows total). Every row has `direction = 'recognition'`. **No `production` rows.** State is `new`.

## 6. Verify NO clozes for blocked function words

The other 18 curated function words appear in Lesson 1 too (`je` appears many times, `v` in "V hotelu v centru mesta", `kako` and `si` in "Kako si?", etc.), but they're already imported as `source='anki'` vocab rows. The GUID conflict in `add_collocation` should silently skip them:

```bash
sqlite3 /Users/wdhaines/CascadeProjects/tunatale/backend/tunatale.db <<'EOF'
SELECT text, card_type, source
FROM collocations
WHERE text IN ('je', 'v', 'sem', 'kako', 'si', 'to', 'da', 'na', 'vam', 'še', 'pa', 'ti', 'se');
EOF
```

**Expect**: each row has `card_type = 'vocab'` and `source = 'anki'`. **None of these should have `card_type = 'cloze'`.** That's the Phase F gotcha working as designed (and the reason we hard-deleted `kje`/`ja`/`seveda`/`brez` to unblock them).

## 7. Sync to Anki

Trigger sync via the admin **Sync** button on `/admin/srs`, or:

```bash
cd /Users/wdhaines/CascadeProjects/tunatale/backend && uv run python -m app.anki.sync
```

Watch the output. Specifically look for:
- `create_new` report showing 4 items created (the 4 cloze rows).
- No `ValueError("Cloze notetype not found in collection")` — Anki's built-in Cloze notetype should be present.
- The pending `ime` translation fix from earlier also pushes in this sync (was staged with `dirty_fields='translation'`).

After sync, re-query TT — `anki_note_id` should now be populated on all 4 cloze rows:

```bash
sqlite3 /Users/wdhaines/CascadeProjects/tunatale/backend/tunatale.db \
  "SELECT text, anki_note_id FROM collocations WHERE card_type = 'cloze';"
# Expect: 4 rows, all with non-null anki_note_id
```

## 8. Inspect the cloze cards in Anki

Open Anki. In Browse, filter:

```
tag:tunatale tag:cloze
```

You should see exactly 4 notes, one per target word. Sample each:

| Note text | Card front (recognition) | Card back |
|---|---|---|
| `Zdravo, {{c1::kje}} ste?` | `Zdravo, [...] ste?` | `Zdravo, kje ste?` |
| `{{c1::Ja}}, še nisem videl.` | `[...], še nisem videl.` | `Ja, še nisem videl.` |
| `{{c1::Seveda}}, hvala.` | `[...], hvala.` | `Seveda, hvala.` |
| `{{c1::Brez}} problema, rada.` | `[...] problema, rada.` | `Brez problema, rada.` |

Notetype should be **Cloze** (not Slovene Vocabulary). Each note generates **one** card (no recognition+production pair). Tags should be `tunatale cloze`.

Also verify the `ime` fix landed: Browse → find sidro's neighbor `ime`. The English field should now read `name`, not `time`.

## 9. Grade a card and verify round-trip

Pick one cloze card (e.g., `kje`) and grade it **Good** in Anki.

Close Anki. Run sync again:

```bash
cd /Users/wdhaines/CascadeProjects/tunatale/backend && uv run python -m app.anki.sync
```

Verify the grade flowed back to TT:

```bash
sqlite3 /Users/wdhaines/CascadeProjects/tunatale/backend/tunatale.db <<'EOF'
SELECT c.text, d.state, d.reps, d.dirty_fsrs, datetime(d.last_review) as last_review
FROM collocations c
JOIN collocation_directions d ON d.collocation_id = c.id
WHERE c.card_type = 'cloze' AND c.text = 'kje';
EOF
```

**Expect**:
- `state = 'learning'` (typical for a first Good grade) or `'review'`
- `reps >= 1`
- `dirty_fsrs = 0` (sync_pull cleared it)
- `last_review` is recent

That's the full round-trip: TT auto-add → cloze sentence generated → synced to Anki as a Cloze note → graded in Anki → synced back to TT.

## 10. Toggle-off regression check

Back in `/admin/srs`, **uncheck** the cloze flag.

Click **Mark as Listened** on any other lesson (e.g., Day 2 — "Asking for Directions to a Hotel"). Day 2 contains its own function words (`kje`, `na`, `je`, etc.) but with the flag off, no new cloze rows should be created:

```bash
sqlite3 /Users/wdhaines/CascadeProjects/tunatale/backend/tunatale.db \
  "SELECT COUNT(*) FROM collocations WHERE card_type='cloze';"
# Expect: still 4 (no new clozes added while flag off)
```

This pins the "DB flag read per request, not at startup" guarantee from Phase F Step 7.

Re-enable the flag before continuing.

## 11. Post-sync diagnostic

Per `.claude/rules/anki-sync.md`:

```bash
sqlite3 "file:$HOME/Library/Application%20Support/Anki2/Will/collection.anki2?mode=ro" \
  "SELECT 'col.usn=' || usn FROM col;
   SELECT 'cards_gt_col=' || SUM(CASE WHEN usn > (SELECT usn FROM col) THEN 1 ELSE 0 END) FROM cards;
   SELECT 'notes_gt_col=' || SUM(CASE WHEN usn > (SELECT usn FROM col) THEN 1 ELSE 0 END) FROM notes;
   SELECT 'revlog_gt_col=' || SUM(CASE WHEN usn > (SELECT usn FROM col) THEN 1 ELSE 0 END) FROM revlog;"
```

All three `*_gt_col` numbers should be **0** after a normal incremental sync. If any are non-zero, run `normalize_usns` per the sync rule.

## Common failures and what they mean

| Symptom | Likely cause |
|---|---|
| Step 4 returns zero cloze rows | Feature flag is off (check `/admin/srs`), OR /listen wasn't actually clicked, OR the dialogue-lemma loop isn't reading the DB flag at request time |
| Step 4 returns rows for `kje` only (not the other 3) | Idempotency check is too aggressive — Phase F's loop should not skip *new* lemmas just because another cloze was created in the same /listen call |
| Step 5 shows `production` direction rows for cloze items | `add_collocation`'s `card_type` branch isn't firing — check `database.py:237`-area |
| Step 6 shows `card_type='cloze'` for `je`/`v`/etc. | GUID conflict path is somehow not firing — would mean Phase F is creating duplicate rows |
| Step 7's sync raises `ValueError("Cloze notetype not found in collection")` | User's Anki collection is missing the built-in Cloze notetype (rare; would need to be restored from Anki's Tools → Manage Note Types → Add → Cloze) |
| Step 8: cards target Slovene Vocabulary notetype instead of Cloze | `sync_create_new`'s `card_type` branch is taking the vocab path for cloze items |
| Step 8: front shows the whole sentence with NO blank | `make_cloze_text` failed to wrap — check the surface form casing or regex boundary |
| Step 9: `dirty_fsrs` still 1 after grade-and-sync | Round-trip didn't clear via `sync_pull`; check the pull path's clear-on-pull logic |
| Step 11: `cards_gt_col > 0` after sync | Some write in this session bypassed the `usn=-1` envelope; investigate which mutation; restore from the safe_open backup if needed |

## Backup recovery

If the sync or any grading step produces unexpected state, the most recent `safe_open` backup is at:

```
~/.tunatale/anki-backups/collection.anki2.bak_20260512_152125
```

That's the pre-deletion snapshot from before the four ghost function words were removed. Restoring it puts the `source='anki'` vocab rows for `kje`/`ja`/`seveda`/`brez` back in Anki — but TT's local DB would no longer match, so you'd need to re-run `import_seed` to align.

The deeper floor is the known-good backup from 2026-04-24:

```
~/.tunatale/anki-backups/collection.anki2.bak_20260424_132004_KNOWN_GOOD_post_S3
```

## Cross-references

- `enchanted-floating-crescent.md` — Phase F design and rationale.
- `docs/fluent-forever.md` — Wyner's cloze-card prescription and how Phase F implements it.
- `backend/app/srs/function_words.py` — the 22-entry curated function-word list.
- `backend/app/anki/sync.py` — `create_cloze_note` and the `card_type` branch in `sync_create_new`.
- `.claude/rules/anki-sync.md` — USN protocol and `safe_open` envelope.
