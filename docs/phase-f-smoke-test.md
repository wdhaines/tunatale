# Phase F — Function-word cloze smoke test

End-to-end manual test using Lesson 1 (Day 1) of the `arrival-in-ljubljana-5f8c0f52`
curriculum, "Arrival in Ljubljana." This lesson exercises Phase F's path from
`/listen` → cloze classification → TT-row creation → Anki sync → grade round-trip.

## What this verifies

1. `/listen` lemmatizes each NATURAL_SPEED token, looks each lemma up against
   `SLOVENE_FUNCTION_WORDS` (`backend/app/srs/function_words.py`).
   Function-word lemmas create **cloze** rows (`card_type='cloze'`) when
   the cloze flag is on. Non-function-word (content) lemmas create **vocab**
   rows (`card_type='vocab'`) regardless of the cloze flag — unknown
   lesson vocabulary is auto-surfaced into Anki.
2. `add_collocation` skips the recognition direction for cloze items (Layer 35:
   cloze = production-only).
3. `add_collocation` is idempotent against the schema's `UNIQUE(text,
   disambig_key)` — pre-existing vocab rows for function words are *not*
   overwritten to cloze; they stay as their original card type.
4. `sync_create_new` branches by `card_type` and writes via `create_cloze_note`
   to Anki's built-in Cloze notetype.
5. `make_cloze_text` wraps the surface form with `{{c1::word}}` in
   `source_sentence`, case-insensitive match, case-preserving output.
6. The DB-backed feature flag survives across requests and persists in
   `anki_state_cache`.
7. Round-trip: grading a cloze card in Anki and syncing back marks the TT row
   clean.
8. `/listen` creates cloze rows with `state='new'` (no auto-grade on creation)
   and creates vocab rows for unknown content words with `state='new'`.
   Existing vocab rows with recognition in LEARNING/RELEARNING/REVIEW states
   receive a Good grade on the recognition direction only (once per day for
   REVIEW). Key phrases (multi-word collocations) follow the same eligibility
   rules as single-word content cards — created if missing, graded with Good
   when eligible, and skipped otherwise.

## Function-word coverage in this lesson

`SLOVENE_FUNCTION_WORDS` (curated 2026-05-12) contains 21 entries. Lesson 1's
NATURAL_SPEED dialogue triggers some of these as lemmas. Behavior depends on
whether a standalone single-word vocab row already exists in TT. **Content
words are now auto-added by `/listen` as vocab rows** — every unknown lemma
in the lesson gets a TT row, and the next sync pushes it to Anki. The user
can mark unwanted words "known" or "ignored" via the WordSpan click.

| Lemma | Pre-existing standalone row? | Expected /listen outcome |
|---|---|---|
| `kje` | no | new cloze row |
| `je`  | no | new cloze row |
| `v`   | no | new cloze row |
| `kako`| no | new cloze row |
| `si`  | no | new cloze row |
| `to`  | no | new cloze row |
| `vam` | no | new cloze row |
| `še`  | no | new cloze row |
| `pa`  | no | new cloze row |
| `se`  | no | new cloze row |
| `ja`  | yes (vocab, has image) | unchanged — stays vocab |
| `sem` | yes (cloze, from prior session) | unchanged — stays cloze |
| `da`, `mi`, `ti` | yes (vocab, source='anki') | unchanged — stay vocab |

The "no standalone row" lemmas don't have a single-word Anki note in the
user's deck; they only appear inside multi-word collocations or are skipped by
the import. /listen sees them for the first time as bare lemmas and creates
fresh cloze cards.

**Note on `seveda` and `brez`:** these are NOT in `SLOVENE_FUNCTION_WORDS` (see
`function_words.py:14-38`), so `/listen` creates them as `card_type='vocab'`
even though they look function-y. To convert them, add the lemmas to that set
and rerun. Earlier drafts of this doc claimed they would be cloze — they
aren't.

## What this does NOT verify

- Forvo / Pixabay media — cloze cards don't need media; the sentence is the
  prompt.
- Online (AnkiConnect) sync — Phase F uses the offline writer only.

## Setup

```bash
./start-dev.sh   # backend :8000, frontend :5173
```

Anki must be **closed** before any sync step. The auto-backup envelope
(`safe_open`) will refuse to write if Anki is running.

## 1. Confirm the flag is on

Open `http://localhost:5173/admin/srs`. Under **Feature flags**, confirm
**Function-word cloze cards (Phase F)** is checked. If not, check it.

Verify via DB:
```bash
sqlite3 /Users/wdhaines/CascadeProjects/tunatale/backend/tunatale.db \
  "SELECT value FROM anki_state_cache WHERE key='enable_cloze_cards';"
# Expect: true
```

## 2. Pre-state snapshot

Snapshot which cloze rows already exist and which lemmas have standalone vocab
rows that /listen should leave alone:

```bash
sqlite3 /Users/wdhaines/CascadeProjects/tunatale/backend/tunatale.db <<'EOF'
SELECT text, card_type, source_sentence, anki_note_id, datetime(created_at)
FROM collocations
WHERE card_type = 'cloze'
ORDER BY created_at;
EOF
```

Two cloze rows are expected to pre-exist (created in the 2026-05-12 cleanup):

| text  | source_sentence              | anki_note_id  |
|-------|------------------------------|---------------|
| `sem` | `Zdravo Ana, jaz sem Janez.` | 1778681103396 |
| `vsak`| `Odprto je vsak dan`         | 1778681103400 |

If you see additional cloze rows, you've already run /listen — proceed to
Step 4 to inspect what's there.

Check which function-word lemmas have pre-existing standalone vocab rows
(these should NOT change after /listen):

```bash
sqlite3 /Users/wdhaines/CascadeProjects/tunatale/backend/tunatale.db <<'EOF'
SELECT text, card_type, source, anki_note_id
FROM collocations
WHERE text IN ('je','v','sem','kako','si','to','da','na','tam','ni',
               'vam','z','mi','še','pa','ti','po','kaj','se','kje','ja')
ORDER BY text;
EOF
```

Expect rows for **`da`, `mi`, `ti`, `ja`** as `card_type='vocab'`,
`source='anki'`, and **`sem`** as `card_type='cloze'` (from prior session).
The other lemmas have no standalone row — they'll be created as cloze by
/listen.

Note the current `col.usn` (you'll re-check it after sync):
```bash
sqlite3 ~/Library/Application\ Support/Anki2/Will/collection.anki2 \
  "SELECT 'col.usn=' || usn FROM col;"
```

## 3. Listen to Lesson 1

Navigate to the lesson page for **Day 1 — "Arrival in Ljubljana"** in the
`arrival-in-ljubljana-5f8c0f52` curriculum. Click **Mark as Listened**.

**Gating note:** `/listen` with the cloze flag on creates function-word
clozes AND content-word vocab rows. With the flag off, function words are
skipped but content words still create vocab rows. Key phrases are always
registered if missing.

The NATURAL_SPEED dialogue contains many function-word phrases. A sample of
what `make_cloze_text` should produce:

| Phrase                              | Function word | Expected cloze front           |
|-------------------------------------|---------------|--------------------------------|
| "Zdravo, kje ste?"                  | `kje`         | `Zdravo, {{c1::kje}} ste?`     |
| "Kako si?"                          | `kako`        | `{{c1::Kako}} si?`             |
| "Kako si?"                          | `si`          | `Kako {{c1::si}}?`             |
| "To je dobro. Center je zanimiv."   | `je`          | `To {{c1::je}} dobro. ...`     |
| "Dobrodošli v Ljubljani!"           | `v`           | `Dobrodošli {{c1::v}} ...`     |
| "Pa gremo skupaj, da vam jo pokazem."| `pa`         | `{{c1::Pa}} gremo skupaj, ...` |
| "Ja, še nisem videl."               | `še`          | `Ja, {{c1::še}} nisem videl.`  |

`make_cloze_text` is case-insensitive in matching but case-preserving in
output: lowercase `pa` in the lesson stays lowercase, uppercase `Kako` stays
uppercase.

## 4. Verify TT cloze rows

```bash
sqlite3 /Users/wdhaines/CascadeProjects/tunatale/backend/tunatale.db <<'EOF'
SELECT text, card_type, source_sentence, anki_note_id, datetime(created_at)
FROM collocations
WHERE card_type = 'cloze'
ORDER BY created_at DESC;
EOF
```

**Expect ~10 cloze rows + ~N vocab rows** (one vocab per unknown content-word lemma):

- **~8 new cloze rows** from this /listen call (one per function-word lemma
  that didn't have a pre-existing standalone row): `kje`, `je`, `v`, `kako`,
  `si`, `to`, `vam`, `še`, `pa`, `se`. All have:
  - `card_type = 'cloze'`
  - `state = 'new'` (not auto-graded; `reps=0`, `introduced_at IS NULL`)
  - `source_sentence` populated with the containing NATURAL_SPEED phrase
  - `anki_note_id IS NULL` (not yet synced)
  - Recent `created_at`
- **~N new vocab rows** for content words in the lesson (proper nouns,
  conjugated verbs, adjectives, etc.): `banka`, `center`, `ljubljani`, etc.
  All have:
  - `card_type = 'vocab'`
  - `state = 'new'` (not graded)
  - `anki_note_id IS NULL`
  - Both recognition + production directions present
- **2 pre-existing cloze rows** carried over from the 2026-05-12 cleanup:
  `sem`, `vsak`, both with `anki_note_id` set.

Exact count varies based on lesson content — the principle is: **every unknown
lemma in the lesson gets a TT row (cloze for function words, vocab for content
words).** Pre-existing rows are untouched unless their recognition direction
is in LEARNING/RELEARNING/REVIEW (eligible for a once-per-day Good grade).

## 5. Verify single-direction creation

Cloze items skip the recognition direction (Layer 35):

```bash
sqlite3 /Users/wdhaines/CascadeProjects/tunatale/backend/tunatale.db <<'EOF'
SELECT c.text, d.direction, d.state, d.dirty_fsrs
FROM collocations c
JOIN collocation_directions d ON d.collocation_id = c.id
WHERE c.card_type = 'cloze'
ORDER BY c.text, d.direction;
EOF
```

**Expect**: one row per cloze collocation. Every row has
`direction = 'production'`. **No `recognition` rows.** State is `new` for
unsynced rows; pre-existing rows may have other states.

## 6. Verify pre-existing vocab rows were NOT converted

The four function-word lemmas with standalone Anki-sourced vocab rows
(`da`, `mi`, `ti`, `ja`) must still be `card_type='vocab'` after /listen:

```bash
sqlite3 /Users/wdhaines/CascadeProjects/tunatale/backend/tunatale.db <<'EOF'
SELECT text, card_type, source
FROM collocations
WHERE text IN ('da','mi','ti','ja');
EOF
```

**Expect**: each row has `card_type = 'vocab'` and `source = 'anki'`. This
pins the idempotency rule: `add_collocation` finds an existing row via the
`(text, language_code, disambig_key)` fallback and does NOT upgrade vocab to
cloze.

## 7. Sync to Anki

Trigger sync via the admin **Sync** button on `/admin/srs`, or:

```bash
cd /Users/wdhaines/CascadeProjects/tunatale/backend && uv run python -m app.anki.sync
```

Watch the output. Specifically look for:
- `create_new` report showing ~10 items created (the new cloze rows).
- No `ValueError("Cloze notetype not found in collection")` — Anki's built-in
  Cloze notetype should be present.

After sync, re-query TT — `anki_note_id` should now be populated on all
cloze rows:

```bash
sqlite3 /Users/wdhaines/CascadeProjects/tunatale/backend/tunatale.db \
  "SELECT text, anki_note_id FROM collocations WHERE card_type = 'cloze' AND anki_note_id IS NULL;"
# Expect: zero rows (all have anki_note_id after sync)
```

## 8. Inspect the cloze cards in Anki

Open Anki. In Browse, filter:

```
tag:tunatale tag:cloze
```

You should see roughly 12 notes (10 fresh + 2 pre-existing). Sample a few:

| Note text                          | Card front (production)   | Card back                |
|------------------------------------|---------------------------|--------------------------|
| `Zdravo, {{c1::kje}} ste?`         | `Zdravo, [...] ste?`      | `Zdravo, kje ste?`       |
| `{{c1::Kako}} si?`                 | `[...] si?`               | `Kako si?`               |
| `Kako {{c1::si}}?`                 | `Kako [...]?`             | `Kako si?`               |
| `To {{c1::je}} dobro. Center je zanimiv.` | `To [...] dobro. ...` | `To je dobro. ...`   |
| `Odprto je {{c1::vsak}} dan`       | `Odprto je [...] dan`     | `Odprto je vsak dan`     |

Notetype should be **Cloze** (not Slovene Vocabulary). Each note generates
**one** card (no recognition+production pair). Tags should be
`tunatale cloze`.

## 9. Verify TT review renders the production-direction cloze

Open `http://localhost:5173/review`. The next new card should be one of the
cloze items. Verify:

- **Front (prompt)**: shows the sentence with the target word replaced by
  `[...]` — e.g. `Zdravo, [...] ste?`. The target word must NOT appear.
- **Click Show**: back shows the full sentence with the answer highlighted
  inline (e.g. `Zdravo, <mark>kje</mark> ste?`) plus translation/note.
- **Audio** (if `audio_url` is plumbed through): plays on reveal.

This pins the production-direction cloze rendering work (DrillCard.svelte's
cloze branch). The card must NOT show on the recognition direction — cloze
rows don't have one.

## 10. Grade a card and verify round-trip

Pick one cloze card (e.g., `kje`) and grade it **Good** in Anki.

Close Anki. Run sync again:

```bash
cd /Users/wdhaines/CascadeProjects/tunatale/backend && uv run python -m app.anki.sync
```

Verify the grade flowed back to TT:

```bash
sqlite3 /Users/wdhaines/CascadeProjects/tunatale/backend/tunatale.db <<'EOF'
SELECT c.text, d.direction, d.state, d.reps, d.dirty_fsrs,
       datetime(d.last_review) as last_review
FROM collocations c
JOIN collocation_directions d ON d.collocation_id = c.id
WHERE c.card_type = 'cloze' AND c.text = 'kje';
EOF
```

**Expect**:
- `direction = 'production'`
- `state = 'learning'` (typical for a first Good grade) or `'review'`
- `reps >= 1`
- `dirty_fsrs = 0` (sync_pull cleared it)
- `last_review` is recent

That's the full round-trip: TT auto-add → cloze sentence generated → synced
to Anki as a Cloze note → graded in Anki → synced back to TT, production
direction updated.

## 11. Toggle-off regression check

Back in `/admin/srs`, **uncheck** the cloze flag.

Click **Mark as Listened** on any other lesson (e.g., Day 2 — "Asking for
Directions to a Hotel"). Day 2 contains its own function words but with the
flag off, function words are skipped. Content words still create vocab rows:

```bash
sqlite3 /Users/wdhaines/CascadeProjects/tunatale/backend/tunatale.db \
  "SELECT card_type, COUNT(*) FROM collocations GROUP BY card_type;"
# Expect: cloze count unchanged from Step 4; vocab count increased by the
# number of content-word lemmas in Day 2's NATURAL_SPEED section
```

Pins the "cloze-only gated behind flag; content-word creation is always on"
guarantee.

Re-enable the flag before continuing.

## 12. Post-sync diagnostic

Per `.claude/rules/anki-sync.md`:

```bash
sqlite3 "file:$HOME/Library/Application%20Support/Anki2/Will/collection.anki2?mode=ro" \
  "SELECT 'col.usn=' || usn FROM col;
   SELECT 'cards_gt_col=' || SUM(CASE WHEN usn > (SELECT usn FROM col) THEN 1 ELSE 0 END) FROM cards;
   SELECT 'notes_gt_col=' || SUM(CASE WHEN usn > (SELECT usn FROM col) THEN 1 ELSE 0 END) FROM notes;
   SELECT 'revlog_gt_col=' || SUM(CASE WHEN usn > (SELECT usn FROM col) THEN 1 ELSE 0 END) FROM revlog;"
```

All three `*_gt_col` numbers should be **0** after a normal incremental sync.
If any are non-zero, run `normalize_usns` per the sync rule.

## Common failures and what they mean

| Symptom | Likely cause |
|---|---|
| Step 4 returns ≤ 2 cloze rows (only the pre-existing ones) | Feature flag is off (check `/admin/srs`), OR /listen wasn't actually clicked |
| Step 4 shows 50+ cloze rows | Function-word list expanded since this doc was written — verify against `function_words.py:14-38` |
| Step 4 shows duplicate cloze rows for the same lemma | Idempotency check is too aggressive — Phase F's loop should not skip *new* lemmas just because another cloze was created in the same /listen call, but `add_collocation` should de-dup the same lemma across calls |
| Step 5 shows `recognition` direction rows for cloze items | `add_collocation`'s `card_type` branch isn't firing — check `database.py:289-292` |
| Step 6 shows `card_type='cloze'` for `da`/`mi`/`ti`/`ja` | The `(text, language_code, disambig_key)` fallback in `add_collocation` regressed; a vocab row was overwritten to cloze. See `database.py:251-280` |
| Step 7's sync raises `ValueError("Cloze notetype not found in collection")` | User's Anki collection is missing the built-in Cloze notetype (rare; would need to be restored from Anki's Tools → Manage Note Types → Add → Cloze) |
| Step 7 raises `IntegrityError: UNIQUE constraint failed: collocations.text, collocations.disambig_key` | The `(text, lang, disambig)` fallback regressed — `add_collocation` is back to relying solely on `ON CONFLICT(guid)` |
| Step 8: cards target Slovene Vocabulary notetype instead of Cloze | `sync_create_new`'s `card_type` branch is taking the vocab path for cloze items |
| Step 8: front shows the whole sentence with NO blank | `make_cloze_text` failed to wrap — check the surface form casing or regex boundary |
| Step 9: front shows the bare lemma, not the sentence | `_item_to_dict` is missing `card_type` / `source_sentence`, OR DrillCard.svelte's cloze branch isn't firing |
| Step 9: word labeled "unknown" in the lesson page even after creation | Broken `lemma` column on the cloze row. Run `uv run python -m app.anki.repair_cloze_lemmas` |
| Step 10: `dirty_fsrs` still 1 after grade-and-sync | Round-trip didn't clear via `sync_pull`; check the pull path's clear-on-pull logic |
| Step 12: `cards_gt_col > 0` after sync | Some write in this session bypassed the `usn=-1` envelope; investigate which mutation; restore from the safe_open backup if needed |
| Step 4: cloze rows show `state='learning'` (not `'new'`) | The auto-grade-on-creation path regressed — `/listen` is calling `schedule()` during creation. Cloze rows must stay `state='new'` with `reps=0` |
| Step 4: vocab rows for content words not created | The unknown-word branch isn't firing — check the lemma loop in `mark_lesson_listened` doesn't skip content-word lemmas |
| Step 4: `registered` count lower than expected | Key phrases may not be counted in `registered`, or content-word creation isn't incrementing the counter |

## Backup recovery

If the sync or any grading step produces unexpected state, the most recent
`safe_open` backup is at:

```
~/.tunatale/anki-backups/collection.anki2.bak_<latest-timestamp>
```

The deeper floor is the known-good backup from 2026-04-24:

```
~/.tunatale/anki-backups/collection.anki2.bak_20260424_132004_KNOWN_GOOD_post_S3
```

## Cross-references

- `enchanted-floating-crescent.md` — Phase F design and rationale.
- `docs/fluent-forever.md` — Wyner's cloze-card prescription and how Phase F
  implements it.
- `backend/app/srs/function_words.py` — the curated 21-entry function-word
  list. **Source of truth** for which lemmas Phase F routes to cloze.
- `backend/app/anki/sync.py` — `create_cloze_note` and the `card_type` branch
  in `sync_create_new`.
- `backend/app/anki/repair_cloze_lemmas.py` — TT-only repair for cloze rows
  whose `lemma` column got corrupted by an earlier sync pass.
- `.claude/rules/anki-sync.md` — USN protocol and `safe_open` envelope.
- `.claude/rules/anki-queue-parity.md` — Layer 35 (`bury_kind`) and Layer 36
  (`counts.all_zero` auto-bump), both of which interact with the cloze
  production-direction queue.
