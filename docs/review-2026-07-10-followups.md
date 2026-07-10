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

## 3. `syllabify.py` dispatch table exempted instead of routed (MED-LOW)

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

- **(a) `new_cards_ignore_review_limit` never synced.** TT hardcodes the default
  (off) in badge + queue. Fix: read the collection/deck-config bool in a
  `refresh_*` phase of `run_full_sync` (add to the ONE phase list +
  `TestRunFullSync`), cache in `anki_state_cache`, thread into
  `effective_review_budget` call sites and the Layer 77 new-slice cap.
- **(b) Candidate Layer 78 — interday learning charges the review limit.** Anki
  gathers interday-learning (queue=3) as `LimitKind::Review`
  (`gathering.rs:16+53`); rule 12's "learning exempt" is true only for intraday
  (queue=1). TT exempts ALL learning/relearning. Needs an oracle test first
  (interday learning card + saturated review cap → does Anki serve fewer
  reviews/new?); only then mirror. Do NOT change `_compute_live_main` without the
  oracle pin.

## 5. Player prefetch downloads everything (LOW)

`LessonPlayer.svelte` onMount prefetches the full concatenated track **plus all 5
section tracks** unconditionally; the plan specified active + likely-next (~2).
Fix: prefetch the resolved current section + the enunciation-cycle neighbor;
skip the legacy full track when `trackMode` (it's immediately src-swapped away).
Mobile-data cost only — nothing is broken.

## 6. Small hygiene (LOW)

- `backend/app/srs/transcript.py` / `db_lemma_cache.py` "row vanished between
  queries" `pragma: no cover` is a real TOCTOU branch across two connections, not
  an unreachable one — write the two-connection test or restructure to one query
  (pragma discipline, `.claude/rules/testing.md`).
- `check_language_literals.py` known remaining bypass: string concatenation
  (`"n" + "o"`) — two Constant nodes, neither matches. Document as a known
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
