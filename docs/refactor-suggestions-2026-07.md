# Refactor / cleanup suggestions — 2026-07-11 doc-sweep byproducts

Assessment only — nothing here is implemented. Collected while verifying every doc
claim against the code for the 2026-07-11 documentation refresh. Ranked by value.
Items already tracked in `docs/archive/review-2026-07-10-followups.md`,
`docs/ui-review-backlog.md`, or `docs/archive/bug-refactor-backlog.md` are excluded
(one pointer exception noted at the bottom).

1. **`norwegian_breakdown.py` — duplicated derivational-suffix-stripping loop.**
   `backend/app/generation/norwegian_breakdown.py:258-267` and `:278-287` are the
   same `while True: for sfx in _DERIVATIONAL_SUFFIXES …` loop copy-pasted inside
   `_find_derivational_with_inflection`. Why: a future suffix-list or
   `_MIN_STEM_LEN` change that updates only one copy is a silent segmentation bug —
   exactly the duplication class the Pre-Layer checklist exists to prevent.

2. **`norwegian_breakdown.py` — backward-buildup loop duplicated with
   `section_builder`'s syllable path.** `_build_syllable_sequence`
   (`norwegian_breakdown.py:538-546`) and the multi-word branch of
   `build_norwegian_breakdown` (`:636-643`) re-implement the same
   per-syllable backward-buildup sequence. Why: one shared helper would keep the
   Pimsleur buildup shape identical across the compound and generic paths.

3. **`queue_stats.py` — 38 `# pragma: no cover` lines, mostly bare
   "defensive".** e.g. `backend/app/srs/queue_stats.py:230,240,322,433,467,494,
   521,559,585,710,778,805,863,1097`. Why: the project's own pragma-discipline
   rule (`.claude/rules/testing.md`) says "defensive" alone is not a
   justification — a batch audit would likely convert several to real
   `caplog`/malformed-blob tests or delete dead branches.

4. **`app/anki/model_discovery.py` — misleading AnkiConnect-era docstrings.**
   `model_discovery.py:1` says "AnkiConnect model-name discovery"; `:15-16` claims
   the cache is "shared with the online path". No online path exists (AnkiConnect
   support was deleted; `get_or_discover_model_name_offline` is the only
   function). Why: actively misdirects a reader toward machinery that's gone.

5. **`app/anki/sync.py:1-6` — stale module docstring.** Still advertises
   "S3.6: --force-fsrs gate + setSpecificValueOfCard"; the CLI/`--force-fsrs`
   interactive gate was removed 2026-06-30 and `setSpecificValueOfCard` was
   AnkiConnect-era. Why: the facade's own `run_full_sync` docstring contradicts
   it; first thing a new reader sees is wrong.

6. ✅ FIXED (2026-07-13). **Archived one-shot scripts still self-document the old invocation.**
   e.g. `backend/scripts/anki_archive/merge_dupes.py:20` said
   `uv run python -m app.anki.merge_dupes` — the module moved to
   `scripts.anki_archive.*` (fixed in `docs/anki-recovery.md` this sweep, but the
   scripts' own usage strings still lie). Why: a recovery scenario is exactly
   when you paste a usage string verbatim.
   Fix: the usage strings in `backfill_guids.py`, `merge_dupes.py`, and
   `migrate_homonyms.py` now read `-m scripts.anki_archive.*`. Same pass restored
   runnability: `sqlite_writer.py` (deleted from `app/` in dceffed but still
   imported by `backfill_guids`/`merge_dupes`) was relocated into the archive
   package next to `notetype_builders.py`; all four archive modules import clean.

7. **`app/anki/notetype.py` — 16-line back-compat shim.** Kept, per its own
   docstring, only for the archived one-shot migrations. Why: if those archive
   scripts are updated to import `vocab_notetype` directly (or declared dead),
   the shim can go; until then it's a decoy module name (`notetype` vs
   `vocab_notetype`).

8. **`app/anki/sync.py` facade re-exports nothing external uses.**
   `sync.py:24,54,60` re-export `_FSRS_REPLAY_TOLERANCE`, `SyncConflict`,
   `_ms_to_datetime`; no non-sync module imports them via `app.anki.sync`.
   Why: every re-export widens the facade surface the one-sync-sequence rule has
   to police; trim to what's actually consumed.

9. **Doc-citation convention: prefer owning module + symbol over frozen
   `file:line`.** This sweep's most common fix class (~25 items) was
   `database.py:<line>` / `sync.py:<line>` citations pointing into what are now
   59-line facades. Why: citing `db_counts.py::count_new_introduced_today`
   (symbol-anchored) survives refactors; bare line numbers rot in weeks. Candidate
   for a line in CLAUDE.md's doc conventions.

10. **`docs/walkthrough.md` PART sub-references have no anchors.** Prose says
    "PART 12.6" / cross-docs said "PART 21.2" but there are no such headings —
    one dangling reference (in `anki-recovery.md`) shipped this way. Why: a
    `###`-heading-per-subsection convention (or symbol references) would make
    cross-doc links verifiable.

11. **Unaudited `date.today()` (midnight-local) vs 4 AM Anki-rollover — the live
    item migrated here from `docs/archive/bug-refactor-backlog.md` (2026-07-13).** Authority
    moved because that backlog is fully closed (35/36 fixed) and slated for
    `docs/archive/`; this is the one item that was still open, so it lives here now
    to keep it findable. Layer 67 fixed the badge/queue surfaces to the 4 AM-anchored
    Anki day, but ~10 `date.today()` call sites still use the midnight boundary.
    Confirmed divergence: the reading-transcript `is_due`/`collocation_is_due` flags
    (`api/srs.py:667` → `transcript.py::_is_due`, `due_at.date() <= today`) bold words
    as due in the `[midnight, 4 AM)` window that the review surfaces don't serve yet —
    same class as the Layer-67 undercount, lower stakes (cosmetic, self-corrects at
    4 AM). Current sites: `api/srs.py:230,450,667,792,802,1144`,
    `queue_engine.py:201,316,331`, `transcript.py:251`. Each needs a per-call-site
    "calendar day vs Anki day" judgment routed through the existing
    `app.anki.rollover.anki_today()` helper — NOT a mechanical replace. Parity-
    sensitive; keep out of Big Pickle's hands.

Ruled out during verification (don't re-report): the Stage-3b shadow columns are
NOT dead schema weight — migration v32 (`migrations.py:938-949`) already dropped
them; no `sqlite_writer`/AnkiConnect/`detect_mode` stragglers remain in `app/` (the
archive keeps a relocated `sqlite_writer.py` for its migrations — see item #6); rollover
and retrievability helpers are already single-sourced; zero TODO/FIXME in
`backend/app`.
