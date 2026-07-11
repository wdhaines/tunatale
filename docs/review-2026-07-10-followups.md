Update docs/walkthrough.md to match the current codebase, then sweep the rest
  of docs/ for stale content. Verify every claim against the code before keeping
  or writing it — the docs lie, the code doesn't.

  The walkthrough likely predates several 2026-06/07 structural changes; verify
  each and update where it shows the old world:
  - sync module split (2026-06-11): app/anki/sync.py is a runner + re-export
    facade; engine in sync_engine.py, I/O in sync_reader.py/sync_writer.py,
    helpers in sync_common.py.
  - peer-sync is the ONLY sync path (legacy /api/anki/sync + /status deleted
    2026-06-10; the python -m app.anki.sync CLI + --all-languages removed
    2026-06-30).
  - god-module split (2026-07-04/05): app/srs/database.py is a ~60-line facade
    over db_* mixins; queue engine extracted to app/srs/queue_engine.py.
  - language-plugin hardening (2026-07-08): app/languages.py registry +
    LanguageContext, enforced by scripts/check_language_literals.py; sqlite_writer.py
    DELETED — any doc mention of it as a live module is stale.
  - direction field registry (2026-07-08): app/srs/direction_fields.py + v35 SQL
    CHECK drive _DIR_COLUMNS/_direction_differs.
  - Norwegian breakdown plugin (2026-07-07..10): app/generation/norwegian_breakdown.py
    (compound segmentation, s-overlap, closed-class stems), dispatched via
    section_builder; docs/bp-brief-segmenter-homographs-overlap.md has the design.
  - lesson player rework (2026-07-09/10): per-section cue manifests
    (render_service.derive_section_cues), new slow_translated section, phase +
    enunciation/English track model in LessonPlayer, legacy lessons gated via
    trackMode; the transcript "Slow" text toggle is gone.
  - daily-caps parity now spans Layers 75-77 (caps limit the served queue, new
    intros charge the review budget, review budget caps served new cards).

  One concrete stale spot already confirmed: .claude/rules/testing.md's frontend
  coverage-gate baseline says "46 drops on 21 files" — current reality is ~128
  drops on 47-48 files (growth, not compiler drift); update the baseline note the
  way that section itself prescribes.

  Hard limits: docs/anki-parity-layers.md is an append-only history — never
  rewrite entries. The .claude/rules/*.md files are load-bearing operational
  docs — fix factually stale statements in place but do not restructure or
  condense them; the Anki mirror is the product, "fewer Layers" is not a goal,
  and Path 2 stays rejected. Don't touch ~/.claude memory files.

  Deliverables: (1) the doc updates, committed after a full unpiped ./test.sh
  run (confirm "All checks passed" + the ruff file count; docs-only changes
  still go through the gate); (2) a new docs/refactor-suggestions-2026-07.md
  listing fixes/refactors/cleanups you noticed along the way — ASSESSMENT ONLY,
  ranked, with file:line evidence and a one-line why per item; do not implement
  them. Check docs/review-2026-07-10-followups.md and docs/ui-review-backlog.md
  first so you don't duplicate known items. Use cheap subagents (sonnet/haiku)
  for the bulk read-and-verify passes; write the final docs yourself.

  Use showboat to update the documentation as appropriate, especially @docs/walkthrough.md.

  Also can you use your knowledge of all the documentation to appropriately compact memory? I’d like to not have to load 66k tokens every session!

# Review follow-ups — 2026-07-10 branch review (norwegian-breakdown)

Findings from the 2026-07-10 review of commits since 7/7 that were **deliberately
deferred** (non-trivial work, or requiring human linguistic judgment). Each entry
is written as a prescriptive brief so a delegated agent can execute it. Fixed in
the same review (not listed below): Layer 77 (served queue ignored the review
limit's cap on new cards — `docs/anki-parity-layers.md`), legacy-lesson player
degradation (`LessonPlayer.svelte` `trackMode` gate + `findPlayableCue` full-track
fallback), language-literal checker case-sensitivity bypass, `.gitignore` DB-backup
pattern gap.

**Status updates:**
- **#1 DONE** (commits `8438629`, `975e44f`): closed-class stoplist + anchor-rank
  scoring, then initial-only homographs (hver/selv/vår), s-overlap compounds
  (busstasjon → buss|stasjon spoken), and forstand-whole via guard-exempt
  prepositions — see `docs/bp-brief-segmenter-homographs-overlap.md`. All 9
  Norwegian lessons re-rendered 2026-07-10.
- **#2 DONE** (2026-07-10): v35 migration normalizes case-variant/out-of-domain
  `prior_state`/`bury_kind` before the CHECK-constrained INSERT, with WARNING
  counts and an idempotency + normalization test.

## 1. Norwegian compound segmenter splits ordinary words (HIGH) — DONE

**Problem.** `_is_lexicalized_whole` (`backend/app/generation/norwegian_breakdown.py:186-198`)
only keeps a word whole when it out-ranks *all* its parts. That inverts when the
coincidental parts are hyper-frequent closed-class words, and `_segment_surface`
prefers the *deepest* split (`norwegian_breakdown.py:177`). Reproducible today:

```
segment_compound("sommer")     -> ['som', 'mer']        # summer; som=rank 7, mer=56, sommer=817
segment_compound("morsom")     -> ['mor', 'som']        # funny
segment_compound("togstasjon") -> ['tog', 'stas', 'jon'] # should be tog|stasjon; "jon" is a NAME in the wordlist
segment_compound("busstasjon") -> ['buss', 'tas', 'jon']
```

The golden tests pass because they pin exactly the Day-1 words verified by ear;
the algorithm does not generalize past that sample.

**Fix shape (two independent parts):**
- **(a) Closed-class stem exclusion.** Add a curated stoplist of words that can
  never be a *free compound stem* (pronouns/conjunctions/degree adverbs: `som`,
  `mer`, `men`, `den`, `det`, `han`, `hun`, `seg`, …). **CRITICAL constraint:
  prepositions/particles MUST stay eligible** — `etter`, `inn`, `ut`, `over`,
  `under`, `til`, `mot`, `for`, `om` are productive compound first-elements
  (`etterforskning` = `etter`+`forskning` is the flagship golden). A blanket
  "function word" test (e.g. reusing `is_function_word`) will break it.
- **(b) Prefer fewer parts on rank ties.** The plan originally specified "prefer
  fewer parts"; the implementation maximizes part count. `tog|stasjon` (2) should
  beat `tog|stas|jon` (3). Check every pinned golden before flipping the
  preference — `etterforskningsteamet`'s pinned split must survive. Consider a
  scoring rule (e.g. prefer the split whose *worst-ranked* part is most frequent,
  tie-break on fewer parts) instead of a bare count flip.

**Ownership.** The stoplist contents and any new/changed golden splits are
**human-confirmed decisions** (plan carve-out: the linguistic oracle is not
delegable). Agent builds machinery + tests; user confirms via
`uv run python -m app.generation.breakdown_preview sommer morsom togstasjon busstasjon flyplassen etterforskningsteamet forskning politiet` and by ear.

**Tests.** Pin the four repro cases above (corrected values) as new goldens; keep
every existing golden green; regression-pin that `etter…` still splits.

## 2. v35 migration hard-fails on CHECK-violating legacy rows (MED) — DONE

**Problem.** `migrate_v34_to_v35` (`backend/app/srs/migrations.py:977-1062`) does a
blind `INSERT INTO _cd_v35 SELECT …` into a CHECK-constrained table. Any row with
a case-variant `prior_state` (e.g. `'REVIEW'` — the exact shape the commit fixed
in test fixtures) or out-of-domain `bury_kind` raises `IntegrityError` and blocks
app startup. Only mitigation was a one-time manual "pre-checked clean" against the
two live DBs; any stale copy/backup/re-import can still hard-fail.

**Fix shape.** Add a normalization pass *inside* the migration before the INSERT:
lowercase `prior_state` where it case-insensitively matches a legal value, NULL it
otherwise (log count); map out-of-domain `bury_kind` to `NULL` (log). Test with an
in-memory v34 DB seeded with `'REVIEW'`, `'New'`, and a junk `bury_kind` — migration
succeeds, values normalized, WARNING logged.

## 3. `syllabify.py` dispatch table exempted instead of routed (MED-LOW) — DONE (8519051)

**Problem.** `backend/app/generation/syllabify.py:202-203` holds a bare
`{"sl": …, "no": …}` code→function dict, allowlisted wholesale in
`tests/language_literals_allowlist.txt` — the exact pattern the registry was built
to replace (commit `378df0e` added registry accessors for three sibling cases in
the same change).

**Fix shape.** Add a `syllabifier` facet to `LanguageConfig`/`app/languages.py`
(`get_syllabifier(code)` mirroring `get_lemmatizer_type`), point
`build_word_breakdown`/callers through it, delete the local dict, remove the
`syllabify.py` allowlist entry (allowlist should keep only true phonotactic data,
if the onset-cluster constants stay). Registry test in `test_languages.py`.

## 4. Review-cap residuals (LOW, parity — orchestrator supervision required)

- **(a) `new_cards_ignore_review_limit` never synced. — DONE (2026-07-10).** TT
  hardcoded the default (off) in badge + queue. Fixed: storage location resolved
  EMPIRICALLY against the 26.05 binary — a collection-level config-table bool,
  key `newCardsIgnoreReviewLimit` (NOT a deck_config proto field; the deck-options
  UI edits it but Anki persists it at collection scope). Oracle-pinned
  (`test_parity_daily_caps.py::test_anki_new_cards_ignore_review_limit_flips_new_cap`:
  saturated reviews + flag OFF → new=0, flag ON → new=new-cap). Read by
  `refresh_new_cards_ignore_review_limit` in `run_full_sync` (added to the ONE
  phase list + `TestRunFullSync`), cached in `anki_state_cache`, resolved by
  `resolve_new_cards_ignore_review_limit(db)` (default False), and threaded into
  `effective_review_budget` (keyword arg), the `/queue-stats` badge new-cap, and
  the Layer 77 served-queue new-slice cap. Layer 76/77 addenda + rule 12 amended.
- **(b) Interday learning charges the review limit — DONE (2026-07-10, Layer 79).** Anki
  gathers interday-learning (queue=3) as `LimitKind::Review`
  (`gathering.rs:35-61`); rule 12's "learning exempt" is true only for intraday
  (queue=1). Oracle-pinned FIRST as required
  (`test_parity_daily_caps.py::test_anki_interday_learning_charges_review_limit`:
  cap 3 + 2 interday learning + 5 due reviews + 4 new → Anki `learning=2,
  review=1, new=0`), then mirrored: `count_interday_learning_due(today)` charged
  inside `effective_review_budget` (both flag branches — the flag only lifts the
  new couplings), threaded through the badge and the served queue. Residual: when
  interday count > budget Anki gathers only budget-many (learning count shrinks);
  TT doesn't cap the learning queue — documented in the Layer 79 entry.
  (Numbering: 78 went to the same-day lastIvl/review_kind revlog fix.)

## 5. Player prefetch downloads everything (LOW) — DONE (2026-07-10)

`LessonPlayer.svelte` onMount prefetches the full concatenated track **plus all 5
section tracks** unconditionally; the plan specified active + likely-next (~2).
Fix: prefetch the resolved current section + the enunciation-cycle neighbor;
skip the legacy full track when `trackMode` (it's immediately src-swapped away).
Mobile-data cost only — nothing is broken.

## 6. Small hygiene (LOW) — DONE (2026-07-10, items 1-2; backfill skipped as optional)

- **DONE.** `backend/app/srs/transcript.py` / `db_lemma_cache.py` "row vanished between
  queries" `pragma: no cover` — restructured to ONE query:
  `get_variant_candidates_with_items` scans and hydrates in a single SELECT
  (`SELECT *` + `_row_to_item`), the caller's vanished-row branch and its pragma
  are deleted outright (testing.md option (b)), and the now-dead
  `get_variant_candidate_rows` removed. (First pass kept a two-SELECT
  same-connection shape with a "genuinely unreachable" pragma — overstated:
  autocommit connections give each SELECT its own read snapshot, so the window
  was narrowed, not closed. The single-query merge removes the branch instead.)
- **DONE.** `check_language_literals.py` known remaining bypass: string concatenation
  (`"n" + "o"`) — two Constant nodes, neither matches. Documented as a known
  limitation in the script docstring (an AST const-folding pass is possible but
  likely overkill; the case-variant bypass was fixed 2026-07-10).
- Legacy lessons could alternatively get a **backfill script** deriving per-section
  cues from the stored full manifest (`derive_section_cues` already exists and is
  pure) — would light up the phase UI on old lessons without re-rendering. The
  player-side gate (shipped) makes this optional polish, not a bug fix.

## Process notes (for the humans)

- Six of nine commits in the player stream (2026-07-09) self-report **partial**
  gates ("frontend gate green", "backend suite") instead of the mandatory full
  `./test.sh`; none paste a CI link. The three shipped-broken incidents in that
  stream (dead play, missing A7, vanished ▶) were all seam bugs that the partial
  runs structurally could not catch. Enforce the "Delivering" convention on
  delegated work.
- The `audioWithCues`-style fixture trap: component fixtures modeled the legacy
  shape, so the phase-model tests exercised exactly the state the feature doesn't
  support. When adding an API field, update the fixtures to the new shape AND keep
  one explicitly-named legacy fixture with degradation assertions (done 2026-07-10).

## 7. sync_push collapses intermediate TT grades out of Anki's revlog (MED-LOW, parity — BP brief, orchestrator review required before commit) — DONE (2026-07-10, Layer 80)

**Problem (observed live 2026-07-10, ~16:00).** TT review badge 45 vs Anki 46 after
the 15:59 sync. TT counted 5 review answers today; Anki's rebuilt studied-today
counter saw 4. Card 1483 was graded twice between syncs (13:49 Again — a countable
lapse — then a 15:52 relearn-step press), but `sync_push` writes **one Anki revlog
row per dirty direction reflecting only the latest state** (`_derive_revlog_shape`),
so the intermediate lapse never reached Anki's revlog. Consequences: Anki's
`review_today` reconstruction (`count_reviews_today_for_deck`) under-counts,
`reps`/`lapses` bump once per push instead of once per grade, and Anki-side FSRS
Optimize sees an incomplete review history. Bounded by grades-per-card-per-sync-gap;
badge half self-clears at rollover — the revlog history loss does not.

**Enabler.** Since Layer 78, TT-native `tt_revlog` rows carry Anki-faithful values
(`review_kind` from the pre-answer state; `last_interval` days-positive ≥ 1 for
review footing) — so the rows can be pushed verbatim instead of reconstructed.

**Fix shape (decisions pre-made — do not re-litigate):**
- In the push path, per pushed direction, candidate rows =
  `tt_revlog` rows for that direction with `id > (SELECT MAX(id) FROM revlog WHERE cid = ?)`
  in the Anki collection (no separate watermark; monotone ids). This naturally
  excludes rows already echoed (Layer 74 lands TT pushes at grade-time ids),
  ingested Anki rows (they keep Anki's original id), and pre-Layer-78 malformed
  history (already superseded by a pushed collapsed row at the latest grade id).
- Insert each candidate at **its own `tt_revlog.id`** (extends Layer 74's
  `preferred_id` semantics per row; keep the `_revlog_id_exists` bump-on-collision
  guard), mapping `ease=button_chosen, ivl=interval, lastIvl=last_interval,
  time=taken_millis, type=review_kind, usn=-1` — i.e. generalize
  `OfflineWriter.write_revlog_entry`; do NOT invent a second insert path.
- `reps`/`lapses`: bump by rows inserted / by inserted rows with
  `type=1 AND next state = relearning` (today: `button_chosen=1` on `type=1`),
  replacing the current single increment.
- **Do NOT touch**: the cards-table state writes, `sync_pull` ingest/`held_ids`,
  `col.usn` (anki-sync.md Layer 61), `_derive_revlog_shape`'s remaining users.
- **Accepted edge (document, don't solve)**: a phone grade newer than an unpushed
  TT grade on the SAME card makes MAX(id) skip the older TT row — same loss as
  today's collapse; rule 6 state convergence still applies.

**Guardrail tests (all must exist; sabotage-drill each):**
1. Unit: two TT grades between syncs (lapse then relearn step) → push writes BOTH
   revlog rows at their exact grade-time ids with `tt_revlog`-verbatim values.
2. Outcome (`TestSociableSync` style): after push,
   `count_reviews_today_for_deck == count_reviews_completed_today` for the same day.
3. Idempotency: re-running push inserts nothing new (MAX(id) + id-exists guards).
4. Round-trip (`--run-peer-sync`, MANDATORY run): push → pull does not re-ingest
   the per-grade rows as duplicates (Layer 74 echo suppression now per row), and
   `reviews_today` does not inflate (the Layer 74 signature).

**Definition of done**: full `./test.sh` + peer-sync suite output pasted;
orchestrator (Fable) reviews the diff before commit — this is inside the Anki-write
danger zone (`.claude/rules/anki-sync.md` envelope applies).

Big Pickle has implemented item 7 of docs/review-2026-07-10-followups.md
(sync_push must push one Anki revlog row per TT grade instead of one collapsed
row per dirty direction). Review its work before anything is committed. This is
inside the Anki-write danger zone — hold a high bar.

Read first (in this order):
1. docs/review-2026-07-10-followups.md §7 — the brief; its "decisions pre-made"
   and "do NOT touch" lists are the review contract.
2. docs/anki-parity-layers.md — Layer 74 (preferred_id / self-echo suppression)
   and Layer 78 (why tt_revlog rows are now Anki-faithful and thus pushable).
3. .claude/rules/anki-sync.md — required writes (usn=-1, mod, col.mod; NEVER
   col.usn) and the one-sync-sequence rule.

Then verify, in the diff and by running things yourself — do not accept pasted
output as the only evidence:

A. Mechanism. Candidate rows per pushed direction must be tt_revlog rows with
   id > MAX(revlog.id) for that cid in the Anki collection; each inserted at its
   own tt_revlog.id via the ONE existing writer path (generalized
   OfflineWriter.write_revlog_entry with the _revlog_id_exists collision guard),
   mapping ease=button_chosen, ivl=interval, lastIvl=last_interval,
   time=taken_millis, type=review_kind, usn=-1. A second/parallel insert path is
   an automatic reject (the b0a4b8a class). reps/lapses must bump per inserted
   row, not once per push.
B. Do-not-touch list respected: cards-table state writes, sync_pull ingest and
   held_ids, col.usn, _derive_revlog_shape's remaining users. git diff should
   show NO changes there beyond the reps/lapses increment relocation.
C. Guardrail tests from the brief all exist (two-grades-both-rows unit test;
   TestSociableSync-style outcome test asserting count_reviews_today_for_deck ==
   count_reviews_completed_today; idempotent re-push; peer-sync round-trip
   proving no duplicate re-ingest and no reviews_today inflation — the Layer 74
   signature). Each must be sabotage-drilled: disable the mechanism it guards,
   watch it go red, restore. If BP claims a drill, re-run at least one yourself.
D. BP's recurring gaps (memory: feedback_bp_review_checklist_recurring_gaps) —
   check each explicitly: (1) did it run the FULL ./test.sh, not just backend
   pytest? Run it yourself regardless and paste the tail. (2) Run
   `cd backend && uv run pytest tests/test_anki_peer_sync_selfhost.py
   --run-peer-sync --no-cov -v` yourself — mandatory for this change. (3) Any
   new `patch("app.…")` must be a true boundary, not the integration under test;
   mock_grandfather.txt must only shrink. (4) Any new `# pragma: no cover` is
   suspect — read the justification skeptically. (5) Tests must pin Anki-shaped
   outcomes (rows in the collection file), not TT's own intermediate values.
E. Edge honesty: the accepted phone-grade-newer edge must be documented, not
   "solved" with extra machinery; pre-Layer-78 malformed history must be
   excluded by the MAX(id) bound (verify with a seeded old bad row: kind=2,
   negative lastIvl, id below the collapsed push — it must NOT be pushed).
F. Deliverables: a new Layer entry in docs/anki-parity-layers.md, §7 marked DONE
   in the followups doc, and the commit message stating what was verified and
   how (Delivering convention in CLAUDE.md).

Report findings ranked by severity with file:line references. Do not fix
anything yourself unless the user asks — your deliverable is the assessment.
