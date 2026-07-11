# Stage 3b empirical measurement — Anki-only-grading experiment

> **Status (2026-07-11).** Historical record. The measurement concluded 2026-05-23 (100% strict match) and the migration it gated has since **shipped and been simplified past this doc**: `event_sync_pull` mode flipped to `new` on 2026-06-02, and the legacy/compare/new flag machinery was later retired from runtime code entirely (live sync takes Anki verbatim, with forward-step replay kept only as a recompute-divergence detector — see `.claude/rules/anki-queue-parity.md` "Soak health check"). One-shot scripts referenced below were deleted or moved to `backend/scripts/anki_archive/`; commands are preserved as written for the historical record and will not run today.

**Audience**: any chat session picking this up. Self-contained; you don't need the prior conversation.

**Goal**: settle whether Stage 3b of the event-sync migration (`~/.claude/plans/ticklish-questing-fountain.md`) is worth committing 2-3 weeks of refactor work to.

## Result (2026-05-23, post Layers 49 + 50 + 51 + 52) — DONE

The experiment ran twice. The 2026-05-22 measurement caught four distinct bugs (Layers 49 + 50 + 51 + 52); the 2026-05-23 re-measurement on the same snapshots confirmed all four fixes. Final headline: **100% strict match (89/89) on stability AND difficulty** — the ≥95% world that the original Stage 3b plan targeted.

| Metric | 2026-05-22 (pre-fixes) | Post-L49 | Post-L50 | Post-L51 | Post-L52 (2026-05-23) |
|---|---|---|---|---|---|
| Strict stability match (±0.01) | — | 17/89 (19.1%) | **89/89 (100%)** | **89/89 (100%)** | **89/89 (100%)** |
| Practical match (±5% s, ±0.1 d) | 78/89 (87.6%) | 78/89 | **89/89 (100%)** | **89/89 (100%)** | **89/89 (100%)** |
| Difficulty bit-exact | 89/89 | 89/89 | 89/89 | 89/89 | 89/89 |
| Single-grade `due_at` exact | — | — | 31/65 (47.7%) | **39/65 (60.0%)** | 39/65 (60.0%) |
| Multi-grade `due_at` bit-exact | — | — | — | 11/40 (27.5%) | **34/40 (85.0%)** |
| All-direction `due_at` within 1h | 0/89 | 42/89 (47%) | 42/89 (47%) | 46/89 (51.7%) | **58/89 (65.2%)** → **82/89** after L53 port |

**Decision**: ≥95% band → the original 1-branch Stage 3b simplification claim HOLDS. Commission Big Pickle on the staged cadence in `~/.claude/plans/ticklish-questing-fountain.md`. The ~−218 LOC `_pull_merge_direction` collapse is the target (vs the refined 3-branch ~−100 LOC fallback the 87.6% world would have required).

**Drill-down history** (kept for reference; all four bugs now fixed):

1. **Difficulty was already solved** (89/89 bit-exact, REVIEW→REVIEW) by an earlier in-flight fix. The `project_fsrs_next_difficulty_diverges` memory was stale for REVIEW→REVIEW; LEARNING→REVIEW and REVIEW→RELEARNING transitions weren't exercised by the day's grades and remain untested.
2. **Stability drift was Layer 50** — not a `_next_stability_recall` formula bug as initially suspected, but the input to it: TT was passing fractional days_elapsed at grade time when Anki uses integer `next_day_at.elapsed_days_since(lrt)` (u64 div by 86400). After the fix, 100% bit-exact. See Layer 50 in `docs/anki-parity-layers.md`.
3. **`due_at` 4-hour quantization was Layer 49** — TT's `schedule()` produced midnight-UTC due_at while `compute_due_at` used 04:00-UTC rollover-anchored due_at. After the fix, 4h quantization is gone.
4. **Single-grade `due_at` off-by-1-day was Layer 51** — two coupled bugs: (a) Anki's `with_review_fuzz(interval, minimum, maximum)` clamps fuzz lower bound to the cascade-derived minimum, but TT was passing `minimum=1` regardless, letting low ChaCha factors drop intervals below the cascade floor; (b) `scheduled_days` was computed via `(due_at - last_review).days` truncation instead of `cards.ivl`-equivalent col_day arithmetic. After the fix, single-grade bit-exact rose 31/65 → 39/65.
5. **Multi-grade `due_at` off-by-1-day was Layer 52** — surfaced via post-L51 multi-grade drill (40 directions, 28 at +1d). Anki's LEARNING/RELEARNING graduation (`learning.rs`/`relearning.rs`) uses simple per-rating fuzz with `minimum=1` — NOT the passing-review cascade Layer 51 wired in. After the fix (new `_graduation_intervals_with_fuzz` helper), multi-grade bit-exact rose 11/40 → 34/40 (85%).

The within-1h ceiling is the **FSRS load balancer** (`loadBalancerEnabled=true` in this collection), **not** `recompute_memory_state` as originally written here — see `docs/anki-parity-layers.md` Layer 53. The balancer relocates each graded interval to a less-loaded day *within* the fuzz range using the whole collection's due histogram.

**Update (2026-05-24): the balancer was ported bit-exact, and the residual re-diagnosed.** Earlier I wrote "a per-card forward-step cannot reproduce the pick" — that turned out to be wrong. The balancer was ported (`app/srs/load_balancer.py` + the RNG primitives in `_anki_rng.py`; commit `7a675c8`, branch `layer53-fsrs-load-balancer`) and proven bit-exact: an oracle sweep matched Anki 24/24, and a decisive per-card real-deck test showed that, **fed the identical histogram, TT's `find_interval` and Anki's balancer pick the same day** — even on a card Anki stored differently. Wiring it into the replay (global-order, with each grade placed at Anki's actual `revlog.ivl`, capturing multi-grade intermediates) raised within-1h **58/89 → 82/89**.

The remaining 7 are **not** a port bug (proven: with the correct histogram, `find_interval` matches Anki). They are the replay's inability to reconstruct Anki's *exact* mid-session histogram from a pre/post snapshot pair — the balancer pick is hypersensitive to ±1 in the per-day counts, and the `(1/interval)³` early-bias amplifies that into a 1–2-day-earlier pick. An earlier pass mis-classified 2 of them as a col-day "anchor" bug; **that was a diagnostic false alarm** — the drill applied `compute_anki_day_index` to a `due_at` built by `review_due_at_for_col_day`, and those two helpers are intentionally non-inverse (off by one). Ground-truthed against the real collection and pinned as **Layer 54** — a latent non-bug with no production impact (`compute_anki_day_index` matches Anki's `today`; `review_due_at_for_col_day` yields the correct date; both production paths agree). See `docs/anki-parity-layers.md` Layer 54 and memory `project_colday_helper_off_by_one`.

**None of this changes Stage 3b**: `sync_pull` reads `cards.due` directly (the "due_at pass-through from Anki" case), so synced cards carry Anki's load-balanced due verbatim. The balancer port is for forward-step validation and future TT-native grading (Phase 2), not the pull path. The `due_at` side-stat ceiling stays below 100% whenever the balancer is on — but it is now a histogram-reconstruction fidelity measure, not a `find_interval` correctness measure (that's settled: bit-exact). Note: unlike Optimize/reschedule, the load balancer fires on **every** grade, so "don't run Optimize during measurement" (below) does not remove it.

The rest of this doc is the procedure used. Useful as a template if Stage 3b's measurement ever needs re-running.

## Design note (2026-05-28): anchor event-sourcing to `card.data`, not the revlog — Anki's revlog is not a pure event log

**The finding.** The Stage 3b compare-soak (event_sync_pull=compare) surfaced 104 shadow **difficulty** divergences (stability bit-exact, difficulty only). Root cause is NOT an FSRS bug and NOT a `recompute_memory_state` event: it's that **Anki's `card.data` is not a pure function of its `revlog`**. A Check Database / forced AnkiWeb download (restore) on 2026-05-21 re-stamped ~2333 revlog rows — original sub-second timestamps were lost, so ids collapsed into sequential runs within a single second (`base+923, +924, +925…`), each cluster sharing one `usn`. Many of those rows are **duplicate re-gradings that Anki never applied to `card.data`** (verified: a card graded 19:26:43 ivl=19 has a 20:23:12 cluster entry ivl=12, and the card keeps ivl=19). TT's event-sourcing replay faithfully applies every revlog row, so it over-applies these orphans. Difficulty exposes it (not stability) because the cluster grades are same-day → elapsed=0 → the recall term zeroes → stability unchanged, while the difficulty update is time-independent. Full root-cause trail: memory `project_stage3b_soak_finding_difficulty_replay`.

**Why this matters for the design.** The premise below (line ~44) assumes divergence happens *only* when Anki ran `recompute_memory_state` between syncs. That's incomplete. Anki **never replays revlog** — it forward-updates `card.data` per grade — so sync-merge / import / restore / Check-Database can leave revlog rows that the live `card.data` does not reflect. Any design that reconstructs memory state by replaying the *full* revlog (the eventual "new" mode) will diverge on every such card, forever, with no per-row signal to distinguish an orphan from a real grade (`dedup_tt_revlog`'s 5s window can't catch clusters minutes/hours from the real grade, and a sequential-id heuristic risks dropping legitimate rapid grades).

**The fix for compare→new: anchor to `card.data`.** When flipping `event_sync_pull` compare→new, do NOT derive state by replaying the full revlog. Instead:
1. **At sync, take Anki's `card.data` as the authoritative baseline** for every synced card (this is exactly what the legacy `_pull_merge_direction` already does, and why legacy never diverges here).
2. **Forward-apply only grades newer than that baseline** — i.e. post-sync TT-native grades. Never re-derive pre-baseline state from revlog.
3. Revlog stays the inter-sync event log for TT-native grading and audit; it is not the source of truth for already-synced state.

This dissolves the entire revlog-impurity class (restores, imports, multi-device merges) rather than chasing per-row heuristics, and it matches the Path-2 escalation in `.claude/rules/anki-queue-parity.md` ("at sync time persist Anki's authoritative state; between syncs serve from snapshot filtered by grades-since-sync"). It also means the compare-mode shadow's job is narrower than first scoped: it validates *forward-step* replay of new grades, not from-scratch reconstruction of historical state.

**Soak interpretation going forward.** The 104 difficulty divergences are a **benign one-off** from the 05-21 restore — NOT a regression and NOT a Stage 3b blocker on their own. See the queue-parity rule's "Already-decided non-issue" entry and `project_stage3b_soak_finding_difficulty_replay` for the exact classifier (difficulty-only + stability-OK = the known cohort; **stability divergence is the real health signal**).

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

**LOC implication.** The original plan's "what collapses" table (in `ticklish-questing-fountain.md`, line ~64) claimed `_pull_merge_direction` shrinks ~218 LOC. Under the refined design, every collapsed branch still has to copy `reps`, `lapses`, `state`, `due_at` from Anki — those `card_rec.reps` / `card_rec.lapses` assignments don't disappear. The shrinkage comes only from collapsing **control flow** (which branch fires, what gets logged), not from removing the copying. Realistic estimate is now closer to −80 LOC than −218. Update the "what collapses" table before commissioning Big Pickle.

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
- Other TT activity (browsing, curriculum generation) is fine — none of it writes to `tt_revlog`.
- `/listen` activity is also fine, but note: `promote_to_learning` writes a `review_kind=4` Manual row into `tt_revlog` when a word is promoted. These rows are filtered out by `rebuild_from_revlog`'s default `exclude_review_kinds=frozenset({4})` (see `backend/app/srs/database.py:2097`), so they don't contaminate replay — but the extended measurement script must inherit that filter. Confirm it's applied to the "new rows since pre" set before passing them into `schedule()`.
- Don't run `sync_pull` yet. Let Anki accumulate the day's grades on its own.
- Don't run "Optimize FSRS parameters" in Anki. Don't change deck-config FSRS weights or `desired_retention`. Don't toggle FSRS on/off. These trigger `recompute_memory_state` and confound the measurement.

**Evening (after grading is done)**:

```bash
# Run sync_pull through TT — pulls the day's Anki-side activity into TT.
# Use whatever your normal sync trigger is (UI button, /api/anki/peer-sync endpoint,
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

**Why these tolerance numbers, in user-visible terms.** FSRS uses stability roughly linearly when computing the next review interval, so a 5% stability error translates to ~5% interval drift. For a 30-day card, that's ±1.5 days; for a 1-day card, ~1 hour. A 0.1 absolute difficulty error (on the 1–10 scale) is sub-step noise that won't move a card into a different difficulty bucket or affect retrieval ordering meaningfully. The strict tolerance (±0.01) is bit-exact territory — what you'd want if Stage 3b's replay were to literally replace Anki's `cards.data` write-back without divergence handling. The practical tolerance is what you'd want if Stage 3b accepts replayed values as "close enough" and only falls back to Anki on cases that meaningfully differ. The strict-vs-practical gap is the lever for tuning how often the divergence path fires.

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
| 50-95% | Real simplification possible but "take-Anki on divergence" is the common path, not rare. Re-frame Stage 3b before committing (see below). |
| <50% | Forward-step `schedule()` doesn't reproduce Anki's per-grade computations consistently. Stage 3b's premise fails. Keep `tt_revlog` as event log only; don't refactor the merge tree. |

**What the 50-95% re-frame concretely looks like.** This is the most likely outcome (Stage 2.5 hit 3% strict-match, and `recompute_memory_state` events won't disappear), so the re-frame deserves a sketch, not a TODO:

- `_pull_merge_direction` keeps **3 branches** (not the original plan's "1 branch"):
  1. **Suspend** (`queue == -1`) — unchanged, ~20 LOC.
  2. **Bury** (`queue ∈ {-2, -3}`) — unchanged, ~30 LOC.
  3. **FSRS state with Anki precedence** (~60 LOC, not the 30 LOC the original plan claimed for the ≥95% world):
     - Ingest new revlog rows since `last_synced_at` (already happens at the top of sync_pull).
     - Apply via `schedule()` forward-step from stored state → `derived`.
     - Read Anki's `cards.data` → `anki_state`.
     - If `derived` matches `anki_state` within strict tolerance → write `derived` (the cheap happy path).
     - Else → write `anki_state` (take-Anki), emit `recompute_divergence` log event.
     - In both sub-cases, copy `reps`, `lapses`, `state`, `due_at`, `anki_card_mod`, `bury_kind` from Anki's record (pass-through fields).
- The divergence-log event is now a **frequent diagnostic stream**, not a rare flag. Plan for it: structured log with one line per direction per sync (card id, stored s, derived s, anki s), rotated daily. Probably ends up as ~50 KB/day; ignore unless investigating a specific divergence.
- **What still gets deleted under the re-frame**: `_anki_step_ahead` (subsumed by always-take-Anki for `left`), the 6 separate FSRS-state branches (collapsed into one), the timestamp-tie-break sub-branch (`dirty_fsrs && anki_last > local_last` — subsumed because we always defer to Anki when sides disagree). Estimate: −80 to −110 LOC on `_pull_merge_direction`, not −218.
- **What does NOT get deleted under the re-frame**: `_resolve_prior_state`, `_resolve_introduced_at`, `_derive_revlog_shape` — these all read fields that still need to exist for `count_new_introduced_today` and sync_push. Schema drop of `prior_*` columns is **deferred from Stage 3b to Stage 5** in this regime.

If the actual measurement comes in at 70-90%, this is the shape Big Pickle implements. The original plan's −415 LOC headline is the ≥95% world; the 50-95% world is a more modest but still worthwhile −100 to −150 LOC plus the structural win of "one mental model for FSRS state: Anki is authoritative when revlog and stored state diverge."

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

### `introduced_at` under the refined design

The original plan's *"First revlog row IS `introduced_at`; derive on read"* was a ≥95%-world claim that assumed `prior_state` could be dropped. Under the refined (pass-through) design, `prior_state` stays on the row for the foreseeable future — `count_new_introduced_today` still filters by `introduced_at IS NOT NULL` and that column is stamped by `_resolve_introduced_at` from `MIN(revlog.id)` at sync time (Layer 26 — see `app/anki/sync.py:1036`).

Pick **one** of these paths in the implementation chat; don't leave it for the migration:

- **Keep `introduced_at` column, stamp from `_resolve_introduced_at` at sync.** Status quo. No code change. `introduced_at` is a one-shot timestamp written once per direction's intro arc.
- **Derive `introduced_at` on read from `tt_revlog.MIN(id)` where `review_kind != 4`.** Drop the column. Slightly slower at read time but eliminates the stamping step. Cleaner if Stage 5 also runs and `prior_state` goes with it.

Default to the first (status quo) unless the measurement opens a ≥95%-world path that also justifies dropping `prior_state`. The second only makes sense as part of a unified column-drop migration, not Stage 3b in isolation.

### Anki-side parsing reference

Anki stores FSRS state as JSON in `cards.data`. The relevant fields:

- `s` (float): stability
- `d` (float): difficulty (1-10 scale)
- `dr` (float): desired_retention (typically 0.9)
- `lrt` (int, sometimes absent): last review time in epoch ms

Example: `{"s":15.6911,"d":3.225,"dr":0.9,"lrt":1779380173195,"decay":0.5}`.

When `cards.data` is `'{}'` or lacks `s`/`d`, the card has no FSRS state — Anki uses SM-2 fallback. These directions can be skipped in the measurement (filter `WHERE c.data LIKE '%"s":%'`).

When parsing, use `json.loads(card.data)` and pull the keys. The script `backend/app/anki/diagnose_replay_match.py` is the existing pattern for doing this — read it for the right error handling.

**Open the Anki snapshots read-only, not via `safe_open`.** The snapshots in `/tmp` are static copies made after `PRAGMA wal_checkpoint(TRUNCATE)`; they're not the live `collection.anki2`. Use `sqlite3.connect("file:/tmp/anki_pre_anki_only.db?mode=ro", uri=True)` — same pattern as `diagnose_replay_match.py` and `replay_fsrs_from_revlog.py`. **Do not** call `safe_open(...)` against the snapshots: `safe_open` is for the live file and does lock-probe + SHA256 backup + integrity-check work that's wrong for a static read-only snapshot path.

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
