# Stage 3b empirical measurement — Anki-only-grading experiment

**Audience**: any chat session picking this up. Self-contained; you don't need the prior conversation.

**Goal**: settle whether Stage 3b of the event-sync migration (`~/.claude/plans/ticklish-questing-fountain.md`) is worth committing 2-3 weeks of refactor work to.

## What Stage 3b is

The TT↔Anki sync layer's `_pull_merge_direction` in `backend/app/anki/sync.py` is a 9-branch merge tree (~318 LOC) that reconciles TT's stored FSRS state with what Anki sent over. The event-sync migration's Stage 3b proposes to collapse 6 of those 9 branches into one: "ingest new revlog rows from Anki since `last_synced_at`; apply them forward via `schedule(stored_state, row)`; compare result with Anki's `cards.data`; if matched, write the replayed value; if mismatched, take Anki's value and record a divergence event."

The claim that justifies the refactor: **for typical sync_pull traffic, forward-step `schedule()` applied to TT's stored state reproduces Anki's `cards.data` at high enough match rate that the merge tree's complex branching is unnecessary noise.** Divergence happens only when Anki ran `recompute_memory_state` between syncs (deck-config edit, "Optimize FSRS parameters", FSRS toggle, etc.).

If the match rate is high (≥95%), Stage 3b ships. If it's borderline (50-95%), the refactor still pays back but the take-Anki-on-divergence branch is more than a rare diagnostic. If it's low (<50%), Stage 3b doesn't simplify enough to justify the work.

## Why today's measurement (2026-05-21) didn't answer the question

The measurement script at `backend/app/anki/measure_stage3b_premise.py` was run against two TT DB snapshots straddling a real production sync (`/tmp/tt_post_dedup.db` → `/tmp/tt_post_sync.db`). It found 0/45 directions matched bit-exact.

That number is misleading because **the snapshot interval included parallel grading in both Anki and TT**. The post-sync TT state reflects merge-tree reconciliation work that conflates three different things:

1. TT's own offline `schedule()` calls (from TT-side grades).
2. Anki's `cards.data` evolution (from Anki-side grades).
3. The merge tree's choices about which side to trust.

Looking at the actual divergences:
- **`reps` was off by exactly 1 on every direction.** TT's `pre.reps` (e.g., 12) was already one less than `pre.tt_revlog` valid-row count (13). Anki's `cards.reps` doesn't count tt_revlog rows 1-for-1 — there's accounting consolidation we don't reproduce. This is structural, not a measurement bug.
- **Stability divergences were tiny.** 0.4-4% relative error. Within practical tolerance.
- **`due_at` had zero divergences.** Forward-step `schedule()` reproduces Anki's interval+fuzz exactly.

The reps issue suggests an architectural correction: in Stage 3b's design, `reps` and `lapses` should be pass-through-from-Anki fields (alongside `anki_card_id`, `anki_card_mod`, `bury_kind`, `due_at`). Replay derives `stability`, `difficulty`, `state`, `last_review`, `left`. Everything else comes from Anki's `cards.data` via the metadata-refresh branch.

But that's a design refinement; it doesn't move the central question. **We still need to know what match rate looks like under clean isolation.**

## The clean experiment — grade only in Anki

Stage 3b is about **sync_pull**: reading Anki's revlog and updating TT. If you grade only in Anki, sync_pull has work to do; nothing else complicates the picture.

The contrasting experiment (grade only in TT) tests offline-replay consistency with online `schedule()` — a debugging baseline, but doesn't test Stage 3b's premise because sync_pull would have no new Anki rows to apply.

### Procedure

**Morning (before any grading)**:

```bash
cp ~/CascadeProjects/tunatale/backend/tunatale.db /tmp/tt_pre_anki_only.db
cp ~/Library/Application\ Support/Anki2/Will/collection.anki2 /tmp/anki_pre_anki_only.db
```

This freezes the pre-experiment state for both DBs.

**Throughout the day**:

- Do all your spaced-repetition grading in **Anki** (the desktop app, AnkiWeb, AnkiDroid, AnkiMobile — any client).
- Do **not** open TT's `/review` page or grade through any TT endpoint.
- Other TT activity (listening, browsing, curriculum generation) is fine — none of it writes to `tt_revlog` for graded cards.
- Don't run `sync_pull` yet. Let Anki accumulate the day's grades on its own.
- Don't run "Optimize FSRS parameters" in Anki. Don't change deck-config FSRS weights or `desired_retention`. Don't toggle FSRS on/off. These trigger `recompute_memory_state` and confound the measurement.

**Evening (after grading is done)**:

```bash
# Run sync_pull through TT — pulls the day's Anki-side activity into TT.
# Use whatever your normal sync trigger is (UI button, /api/anki/sync endpoint,
# or test.sh's sync path — confirm which works in your current setup).
```

After sync_pull finishes, snapshot both DBs again:

```bash
cp ~/CascadeProjects/tunatale/backend/tunatale.db /tmp/tt_post_anki_only.db
cp ~/Library/Application\ Support/Anki2/Will/collection.anki2 /tmp/anki_post_anki_only.db
```

You now have a clean four-snapshot set. Time to measure.

### Running the measurement

The existing script (`backend/app/anki/measure_stage3b_premise.py`) compares two **TT** snapshots — but the right comparison is `schedule(pre.tt_stored, new_rows_in_post)` vs `post.anki.cards.data` (Anki's stored values, not TT's post-merge stored). The current script doesn't do this. It needs a small extension.

**Extension required** (one-day-shape task for the picking-up chat):

1. Add `--anki-pre` and `--anki-post` arguments to `measure_stage3b_premise.py`.
2. For each direction with new tt_revlog rows in `post.tt_revlog` (since `pre.tt_revlog`'s max id for that direction):
   - Build pre_state from `pre.tt_collocation_directions`.
   - Apply new rows via `schedule()` to get `derived`.
   - **New**: read Anki's post `cards.data` for the matched card. Parse `s`, `d`, `dr`, `lrt` from the JSON.
   - Compare `derived` with Anki's parsed `cards.data` (not TT's post-stored).
3. Bucket into:
   - **MATCH**: `derived.stability ≈ anki.s` within ±0.01, `derived.difficulty ≈ anki.d` within ±0.01.
   - **PRACTICAL_MATCH**: relaxed tolerance (5% relative stability, 0.1 absolute difficulty).
   - **DIVERGE**: stability mismatch outside practical tolerance.
4. Skip `reps`, `lapses`, `state`, `due_at` in the comparison — those are pass-through fields under the refined Stage 3b design.

The new invocation will look like:

```bash
cd backend && uv run python -m app.anki.measure_stage3b_premise \
    --pre /tmp/tt_pre_anki_only.db \
    --post /tmp/tt_post_anki_only.db \
    --anki-pre /tmp/anki_pre_anki_only.db \
    --anki-post /tmp/anki_post_anki_only.db
```

### Reading the results

The script's existing decision gate logic still applies. The match rate against Anki's `cards.data` (not TT's post-merge stored) is the Stage 3b decision-maker:

| Match rate | Verdict |
|---|---|
| ≥95% | Stage 3b's simplification claim holds. Commission Big Pickle on the staged cadence in `ticklish-questing-fountain.md`. |
| 50-95% | Real simplification possible but "take-Anki on divergence" is the common path, not rare. Re-frame Stage 3b as "merge with Anki precedence on FSRS state" and re-estimate before committing. |
| <50% | Forward-step `schedule()` doesn't reproduce Anki's per-grade computations consistently. Stage 3b's premise fails. Keep `tt_revlog` as event log only; don't refactor the merge tree. |

Also worth reporting:
- **Mean and distribution of stability divergence** when paths diverge. If most non-MATCH cases are <5% stability difference, the practical impact is small even if the strict match rate is low.
- **`due_at` match rate.** If forward-step reproduces fuzzed intervals exactly, that's a strong signal that the FSRS forward step is reliable.
- **Direction count with N new rows**. If most directions have N=1 or 2 new rows in a day's grading, the measurement reflects realistic sync intervals. If some have N=10+, those are heavy-use cards and worth spot-checking separately.

## Background context the next chat will need

### Where the plan lives

- **Active plan**: `~/.claude/plans/ticklish-questing-fountain.md` — the staged migration (Stages 0-5) plus the Stage 3b reframe and decision gates. The top section ("Status") is current as of 2026-05-21; the bottom half is the original (pre-reframe) plan, marked superseded where it conflicts.
- **Simplify plan** (done, kept for context): `~/.claude/plans/you-ve-written-a-ton-happy-yeti.md` — the Phase 1-3 refactor + oracle harness that preceded the event-sync work.

### Where the code lives

| File | Purpose |
|---|---|
| `backend/app/srs/database.py:2039` | `rebuild_from_revlog(...)` — walks `tt_revlog` through `schedule()` from NEW state. Stage 3b will extend this with `starting_state` and `since_id` parameters. |
| `backend/app/anki/replay_fsrs_from_revlog.py` | One-shot classifier that buckets every direction into MATCH/REPAIR/SKIP_*. Diagnostic tool; production behavior unchanged. |
| `backend/app/anki/diagnose_replay_match.py` | Picks 10 representative directions and prints stored vs replayed field-by-field. For drilling into why specific cases diverge. |
| `backend/app/anki/dedup_tt_revlog.py` | Removes content-duplicate `tt_revlog` rows (Anki copies of TT-grade events that landed at slightly different millisecond timestamps). One-shot; already run against production (237 dupes removed). |
| `backend/app/anki/measure_stage3b_premise.py` | **The script to extend.** Currently does TT-pre vs TT-post comparison; needs Anki-snapshot integration per "Extension required" above. |
| `backend/app/anki/sync.py:1376` | `_pull_merge_direction` — the 9-branch merge tree Stage 3b targets. |
| `backend/app/anki/sync.py:1712` | `_ingest_anki_revlog_for_card` — already writes to `tt_revlog` on every sync_pull, including the `has_revision_near` dedup guard. Stage 3b's "new rows since last sync" come from here. |

### Key facts about the current data state (2026-05-21)

- 1243 directions with `reps > 0`, all Anki-linked. 14302 `tt_revlog` rows.
- 26 bogus rows with `id < 1_000_000_000_000` from a pre-Stage-0 PK bug (TT's `time_ms` was used as PK instead of wall-clock ms-since-epoch). They sit near 1970-01-01 in `id` ordering. Filter them out in any measurement with `WHERE id >= 1000000000000`. Cleanup deferred.
- The two TT snapshots in `/tmp` (`tt_post_dedup.db` from 15:30, `tt_post_sync.db` from 16:26) reflect mixed-grading + multiple syncs. Don't use them for the Stage 3b measurement — use the fresh Anki-only snapshots from tomorrow.

### Anki-side parsing reference

Anki stores FSRS state as JSON in `cards.data`. The relevant fields:

- `s` (float): stability
- `d` (float): difficulty (1-10 scale)
- `dr` (float): desired_retention (typically 0.9)
- `lrt` (int, sometimes absent): last review time in epoch ms

Example: `{"s":15.6911,"d":3.225,"dr":0.9,"lrt":1779380173195,"decay":0.5}`.

When `cards.data` is `'{}'` or lacks `s`/`d`, the card has no FSRS state — Anki uses SM-2 fallback. These directions can be skipped in the measurement (filter `WHERE c.data LIKE '%"s":%'`).

When parsing, use `json.loads(card.data)` and pull the keys. The script `backend/app/anki/diagnose_replay_match.py` is the existing pattern for doing this — read it for the right error handling.

### Things to avoid during the experiment

- **No `recompute_memory_state` triggers in Anki.** Specifically: don't run "Tools → FSRS" optimize, don't change deck-config FSRS weights or `desired_retention`, don't disable+re-enable FSRS, don't bulk-reschedule cards.
- **No TT-side grading.** If you accidentally grade a card in TT, that direction will have both an Anki-side and a TT-side revlog row, and the measurement loses the isolation.
- **No `dedup_tt_revlog` runs.** The dedup script ran once already (`9dff44d` / commit history); running it again is fine but won't change anything (sliding-window dedup found 0 additional matches). Don't run it mid-experiment.
- **One sync_pull at the end.** Multiple syncs interspersed with grading would split the "new rows" across multiple ingest batches. Easier to reason about with one sync at the end.

### What success looks like

- Match rate against Anki's `cards.data` ≥ 95% on stability and difficulty (within ±0.01 absolute or ±5% relative — the script can report both).
- `due_at` ≈ identical (forward-step reproduces fuzz).
- Divergent cases have an obvious explanation (e.g., the card crossed an FSRS toggle at some point, or the user happened to optimize during the day despite the instruction not to).

### What failure looks like

- Match rate <50%: forward-step `schedule()` doesn't reproduce Anki's per-grade output. Either TT's port of FSRS has subtle drift from Anki's, or Anki does something between grades that we're not modeling (some other recompute path). Stage 3b doesn't ship; keep tt_revlog as event log.
- Match rate 50-95%: read the divergent cases. If they cluster around specific card properties (large `s`, very small `s`, `lrt` absent, etc.), the cause is mechanical and might be fixable. If they're random, Stage 3b's "take Anki on divergence" path is the common case, and the simplification claim weakens.

## Quick checklist for the picking-up chat

- [ ] User has done the Anki-only-grading day and produced 4 snapshots in `/tmp`.
- [ ] Extend `measure_stage3b_premise.py` to take `--anki-pre` and `--anki-post`, parse `cards.data` JSON, compare against `derived` from forward-step replay.
- [ ] Run it. Capture the match rate.
- [ ] Update the Status table in `~/.claude/plans/ticklish-questing-fountain.md` with the result.
- [ ] If MATCH ≥95%: kick off Stage 3b implementation per the staged cadence in the plan.
- [ ] If MATCH 50-95%: ask the user how to proceed; the simplification claim weakens but the work might still be worthwhile. Re-frame the plan and re-estimate.
- [ ] If MATCH <50%: mark Stage 3b NOT VIABLE in the plan. Recommend the user stop here and accept the wins from Stages 0-2.5.

## Related documentation

- `.claude/rules/anki-queue-parity.md` — divergence playbook + pre-Layer checklist. Read before any sync/merge changes.
- `.claude/rules/anki-sync.md` — USN protocol, safety envelope, schema migrations.
- `.claude/rules/anki-oracle-harness.md` — when to write harness tests vs unit tests.
- `.claude/rules/testing.md` — coverage discipline including pragma rule.
- `docs/anki-parity-layers.md` — full Layer history (1-48).
