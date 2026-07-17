# Master cleanup list — 2026-07-16

> **COMPLETE 2026-07-17 — archived.** Every item is ✅ FIXED except the
> "Deferred by design" section at the bottom, which stays deliberately
> unpicked (each entry names its trigger: the learning-modes rewrite, the
> ~2026-09 migration-removal window, the bucket split). Execution ran on
> branch `cleanup/master-list-2026-07`; per-item commits are annotated
> inline below.

Supersedes `docs/archive/refactor-suggestions-2026-07.md` (its open items are absorbed
below; its fixed/ruled-out findings are recorded at the bottom so nobody
re-investigates). Sources: critique of the 2026-07-09→16 commits, status
verification of the ranked-architecture assessment (plan
`what-are-the-biggest-splendid-hamster.md`), and diff review of the week's
feature commits. Line refs are as of `578d305`; prefer the `module::symbol`
anchors over line numbers (they survive refactors — see C5).

## Model routing legend

Optimize for scarce Fable tokens: Fable does judgment and parity review only,
never mechanical execution.

- **Haiku** — mechanical, fully specified, gate-verifiable diffs.
- **Big Pickle** (free Sonnet-class agent) — medium mechanical coding with a
  tight brief. Never: parity-sensitive code, pragma audits, or writing new
  coverage-driven tests (its documented failure modes). Verify its gate claims.
- **Sonnet** (paid subagent) — refactors needing moderate judgment.
- **Opus** — cross-module design + execution, non-parity.
- **Fable** — per-site parity verdicts, briefs for parity-adjacent work, final
  review of any diff touching queue/sync/day-boundary behavior.

## Bugs

1. ✅ FIXED (2026-07-17, with item 2 — one commit; regression tests frozen in
   the [midnight, 4 AM) window, fsrs guard sabotage-drilled both ways).
   **New endpoints bucket by local midnight, not the 4 AM Anki day.**
   `api/srs.py::mark_lesson_listened` (srs.py:537-540, used at :613,:657) and
   `api/srs.py::get_lesson_review_queue` (srs.py:850-853, used at :876 — the
   same 4-line block copy-pasted verbatim) hand-roll
   `datetime.date.today()` + midnight `combine(...)` windows for
   grade-eligibility and "touched today" classification. This is the exact
   anti-pattern `app/srs/anki_mirror/rollover.py` warns about (the 66-vs-73
   badge divergence): a card graded in `[midnight, 4 AM)` counts as "today"
   here while every count surface (`db_counts.py::count_new_created_today`,
   added in the *same commit*, correctly uses `_anki_day_bounds_utc`) says
   "yesterday". Fix: route both windows through the rollover helpers; add a
   frozen-clock regression test inside the `[midnight, 4 AM)` window.
   **Model: Sonnet executes from a tight brief; Fable reviews the small diff.
   Read `.claude/rules/anki-queue-parity.md` first. Not Big Pickle.**

2. ✅ FIXED (2026-07-17, with item 1). Verdict-audited per site; three
   additional sync_engine sites found and fixed in the same pass
   (`_compute_today_start_ms`, push-path `days_str`, sibling-bury backfill).
   **Finish the `date.today()` audit (was refactor-suggestions #11, now
   narrowed).** Most sites are wrapped in `due_at_rollover_utc(...)`; still
   unwrapped or unverified: `api/srs.py:233,933,1058,1068,1422`,
   `fsrs.py:830,834`, `srs/transcript.py:251` (the documented
   `is_due`-bolding cosmetic divergence), `queue_engine.py:201,316,331`.
   Each needs a one-line "calendar day vs Anki day" verdict — NOT a
   mechanical replace. **Model: Fable writes the per-site verdict table (one
   short pass); Sonnet executes + tests. Not Big Pickle.**

## Simplifications

3. ✅ FIXED (2026-07-17, 25a72ec — `_build_translated_phrases(en_first, slow)`
   helper; test file unmodified as behavior lock).
   **`section_builder.py` scene-loop ×4.** The two functions added in
   `eabfb4e` (`section_builder.py::build_en_translated_section` :312-356,
   `::build_slow_en_translated_section` :359-406) are near-verbatim copies of
   the pre-existing `build_translated_section`/`build_slow_translated_section`
   (:221-309) — the same ~35-line scene/line-validation loop four times,
   differing only in phrase order and the slow transform. Extract one
   parameterized helper. Well-tested area (`test_section_builder.py`).
   **Model: Big Pickle.**

4. ✅ FIXED (2026-07-17, 25a72ec — `_strip_derivational_suffixes` +
   `_build_syllable_inner` extracted; test file unmodified).
   **`norwegian_breakdown.py` internal duplication** (was refactor-suggestions
   #1+#2, both still open at the file's new home
   `app/plugins/languages/no/norwegian_breakdown.py`): the
   derivational-suffix-strip loop at :265-274 vs :285-294, and the
   backward-buildup loop in `_build_syllable_sequence` (:590-593) vs the
   multi-word branch of `build_norwegian_breakdown` (:687-690). A future
   suffix-list or `_MIN_STEM_LEN` change updating one copy is a silent
   segmentation bug. Tests exist (`test_norwegian_breakdown.py`).
   **Model: Big Pickle.**

5. ✅ FIXED (2026-07-17, c3520a8 — 27 pragmas removed + covered via
   closed-connection/malformed-blob/corrupted-cache recipes, no mocks; 11
   survivors carry structural unreachability proofs in their comments).
   **`queue_stats.py` pragma audit** (was #3): 38 `# pragma: no cover` in
   `app/srs/anki_mirror/queue_stats.py` (1100 lines), mostly bare
   "defensive" — the project's own pragma-discipline rule says that's not a
   justification. Convert to `caplog`/malformed-blob tests or delete dead
   branches. **Model: Sonnet (pragma work is a named Big Pickle failure
   mode).**

## Cleanups

6. ✅ FIXED (2026-07-17, 5087188). **Stale AnkiConnect-era docstrings** (was #4+#5):
   `app/plugins/anki_sync/model_discovery.py:1,15-16` ("AnkiConnect
   model-name discovery", "cache shared with the online path" — no online
   path exists) and `app/plugins/anki_sync/sync.py:1-6` ("S3.6: --force-fsrs
   gate + setSpecificValueOfCard", removed 2026-06-30). **Model: Haiku.**

7. ✅ FIXED (2026-07-17, 5087188). **Trim `sync.py` facade re-exports** (was #8): `sync.py:24,54,60` re-export
   `_FSRS_REPLAY_TOLERANCE`, `SyncConflict`, `_ms_to_datetime`; zero external
   consumers import them via the facade. **Model: Haiku.**

8. ✅ FIXED (2026-07-17, 25a72ec). NOTE the premise was stale: archive
   scripts were already repointed 2026-07-13; the real consumers were
   `sync_writer.py` + 4 test files (8 sites), all moved to
   `SLOVENE_VOCAB.name` / `list(SLOVENE_VOCAB.field_names)` direct use.
   **Retire the `app/cards/notetype.py` shim** (was #7): 17-line back-compat
   module kept only for the archived one-shot migrations; repoint
   `scripts/anki_archive/*` at `vocab_notetype` directly and delete.
   **Model: Haiku.**

9. ✅ FIXED (2026-07-17, 5087188 — `test_gate_scripts_tracked.py`).
   **Guard against untracked scripts wired into the gate.**
   `backend/scripts/.gitignore` is deny-by-default; `db6fcf7` wired
   `check_plugin_imports.py` into test.sh + CI while the script itself was
   still gitignored — fresh checkouts had a red gate until `9bc7278`
   allowlisted it 7 commits later. Add a tiny test asserting every
   `scripts/*.py` referenced by `test.sh`/CI is in `git ls-files`.
   **Model: Haiku.**

10. ✅ FIXED (2026-07-17, 5087188). **Doc-citation convention** (was #9): add one line to AGENTS.md doc
    conventions — cite `module::symbol`, not bare `file:line` (this sweep's
    most common rot class). **Model: Haiku (or by hand).**

## Test debt

11. ✅ FIXED (2026-07-17, 3e989f7 — 10 domain files; conservation-verified
    pure move). **Split `test_api.py` (4,928 lines).** It gained 757 pure-insertion lines
    this week alone (879d377/7c6b0bf/321842a). Split by route domain; the new
    listens/lesson-queue tests get their own file. Mechanical moving, gate
    verifies. **Model: Big Pickle (moving tests, not writing them).**

12. ✅ FIXED (2026-07-17, 3e989f7). **Promote cross-test-file helper hubs into `tests/_helpers/`** (hamster #6):
    `test_anki_sync_create_new.py` (4 consumers), `test_anki_sync_pull.py`
    (3), `test_anki_sync_push.py` (1). **Model: Big Pickle.**

13. ✅ FIXED (2026-07-17, c797bd0 — `_set_dir` now
    `get_collocation(text)` → mutate `DirectionState` →
    `update_direction(guid)`; no new production API needed).
    **`TestLessonReviewQueue` reaches into DB internals** — raw
    `UPDATE collocation_directions` via the private `db._get_conn()`
    (test_api.py:4595+, new in 321842a). Give it a public seam or use the
    directions mixin API. **Model: Sonnet (small API-design call).**

14. ✅ FIXED (2026-07-17, 84bb17b — `reset()` seam per the llmActivity
    house pattern; page tests use the real store with only `$lib/api`
    mocked; page.test.ts split into 5 domain files, it-count conserved;
    pipeline-store mock retained as a future item).
    **Frontend listened-store testability.** `listened.test.ts` needs
    `vi.resetModules()` + dynamic re-import per test because the store is a
    non-resettable module singleton; `page.test.ts` (now 2,960 lines) mocks
    the internal store module rather than only the `api` boundary. Add a
    reset/factory seam, then split the page tests. **Model: Sonnet (the
    frontend coverage gate has quirks; needs judgment).**

## Design work (needs a brief before anyone codes)

15. ✅ FIXED (2026-07-17 — declarative `cache_registry.py`: 19 keys with
    source/day-scoped/max-age/logic-version; KeyError write/read guard on
    unregistered keys; sync-refresh conservation test in the sociable
    harness, sabotage-drilled; `session_main_queue` payload carries a
    logic-version "v" discarded on mismatch like a day mismatch — the
    deploy-time stale-queue pitfall is now structural, not prose).
    **DB-persisted cache invalidation by convention** (hamster #5, untouched):
    `session_main_queue` & friends survive deploys; invalidation is prose.
    Design a registry (cache keys declare their invalidation events) or a
    versioned-key scheme. **Model: Fable writes the short design brief;
    Sonnet implements.**

## Deferred by design — do NOT pick these up early

- **`api/srs.py` extraction of `mark_lesson_listened` (~294 lines) and
  friends.** `docs/learning-modes.md:43-50` explicitly defers this until the
  learning-modes rewrite. Item 1 above (day-boundary fix) is still in-bounds —
  it's a bug fix, not the refactor. When the rewrite lands: Sonnet/Opus from a
  Fable brief.
- **Unify `api/srs.py::_analyze_lesson_words` with
  `srs/transcript.py::extract_transcript`'s NATURAL_SPEED walk.** Two
  independent tokenize→lemmatize→analyze passes over the same phrases now
  exist (the "anti-drift factoring" in 321842a only unified /listen with the
  review queue, not with transcript). Fold into the same rewrite.
- **Remove `listened.svelte.ts::migrateFromLocalStorage`** + the two
  `LEGACY_*_KEY` constants once returning users have migrated (shipped
  2026-07-16; revisit ~2026-09). Haiku, later.
- **Listening-recognition bucket**: `mark_lesson_listened` grades plain
  `Direction.RECOGNITION` as a documented stand-in until the bucket split
  (learning-modes.md Step 3+) exists. Not a bug; don't "fix".

## Closed since the prior assessments — don't re-investigate

- `srs/database.py` god module → 61-line facade over 14 `db_*.py` mixins;
  queue engine extracted to `app/srs/queue_engine.py` (455 lines).
- `_DIR_COLUMNS` / `_direction_differs` now derive from `DIRECTION_FIELDS` /
  `SYNC_COMPARABLE_MODEL_FIELDS` (db_base.py:128, direction_fields.py:170).
- Lemmatizer cache is keyed by `language_code` (`srs/lemmatizer.py:294-313`) —
  multi-language safe, not a singleton bug.
- `docs/walkthrough.md` PART sub-anchors exist (was refactor-suggestions #10).
- Archive-script usage strings + `sqlite_writer.py` relocation (was #6, fixed
  2026-07-13).
- Stage-3b shadow columns already dropped (migration v32); no
  AnkiConnect/`detect_mode` stragglers in `app/`; rollover + retrievability
  helpers single-sourced; zero TODO/FIXME in `backend/app`.
