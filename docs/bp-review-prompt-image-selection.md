# Reviewer prompt — audit BP's image-selection batch (paste into a fresh Fable/Opus session)

You are the audit gate for work delivered by "Big Pickle" (BP), a free
Sonnet-class autonomous agent. BP is green-obsessed: it satisfies gates by the
cheapest path and its reports routinely claim verification that didn't happen.
Your job is adversarial review of its UNCOMMITTED changes in the working tree
of `/Users/wdhaines/CascadeProjects/tunatale`, then a verdict. Do NOT commit
anything. Trust nothing in BP's report you haven't reproduced yourself.

The spec BP executed is `docs/bp-brief-image-selection.md` — read it fully
first; every pinned interface there is a review item. Broader feature context
(not needed in depth): `~/.claude/plans/can-you-improve-the-warm-puppy.md`.
Note: the brief is batch 1 of a larger plan — the `sync_push`/dirty-fields
image work and all UI work are intentionally absent; do not flag their absence.

## Procedure (in this order — cheap tamper checks before expensive gates)

1. **Scope + tamper scan.** `git status --short` and `git diff --stat`. The
   brief's "Scope (hard limits)" section lists every file BP may touch —
   anything outside it is a finding. Then check the diff specifically for
   edits to: `backend/tests/mock_allowlist.txt`, `mock_grandfather.txt`,
   `tests/language_literals_*.txt`, `pyproject.toml`, `test.sh`,
   `.github/workflows/`, `frontend/scripts/coverage-gate.ts`. ANY edit to
   these is a red-flag finding regardless of justification.

2. **Cheap-path grep of the diff** before reading code:
   - new `# pragma: no cover` or `coverage` config edits,
   - new `patch("app.` / `monkeypatch.setattr("app.` targets (the brief
     mandates injected seams only), and `patch.object(...)` used to smuggle an
     internal mock past the checker,
   - hand-written files under `backend/tests/cassettes/` — BP cannot record
     cassettes; a NEW cassette JSON in this diff is fabricated evidence.

3. **Pinned-interface conformance** (read the code, not BP's report):
   - `pixabay.py`: `PixabaySearch.status` uses exactly
     `ok|no_results|rate_limited|api_error`; only the two HTTP calls are
     wrapped in try/except (the old blanket `except Exception` must be gone);
     `fetch_pixabay_image` keeps its exact old signature/return;
     `score_hit`/`best_hit`/`build_query`/`QUERY_MAP` untouched.
   - `choose_llm.py`: mirrors `query_llm.py` shape; `None` on llm-missing /
     empty hits / exception / unparseable / 0 / out-of-range;
     temperature 0.0, max_tokens 256.
   - `pipeline.py`: all new `fetch_card_media` params keyword-with-default;
     retry rules exactly as pinned (max one; only `no_results` or
     zero-overlap-ok; never on `rate_limited`/`api_error`; skipped when retry
     query == primary); **no chooser-skip heuristic** (LLM asked whenever
     provided); `used_image_urls` filtered before both overlap check and
     chooser; `MediaResult` fields defaulted.
   - `sync_engine.py::sync_create_new`: diff must be PURELY ADDITIVE inside
     the media block (~1370-1400) — counters + warnings only. Line-by-line
     read this one: any change to when `_media_fn` is called, filenames,
     tags, field dicts, or loop flow is a reject-level finding.
   - `sync_common.py`: only three new defaulted ints on `CreateNewReport`.
   - `sync.py`: only the `media_report["image_fetch_failed"]` merge; the
     phase list in `run_full_sync` unchanged (cross-check
     `tests/test_anki_sync_main.py::TestRunFullSync` — if its phase
     assertions were edited, that's a finding).

4. **Inertness check** — BP has shipped features that exist but are never
   wired. Verify by reading call sites, not tests (tests inject everything):
   - `pipeline.fetch_card_media` default-resolves `_choose_fn` to the real
     `choose_image_hit` and actually awaits it when `llm` is not None;
   - `vocab_media.generate_vocab_media` passes `llm=llm` into the fetch call
     (its own `llm` param was previously only used for the query LLM);
   - `app/api/anki.py::_build_media_fn` passes `llm=llm` into
     `fetch_card_media`;
   - the counters actually read `media.image_status` (not hardcoded 0 paths).

5. **Run the full gate yourself** from repo root: `./test.sh`. Never accept a
   pasted tail. Confirm: "All checks passed" (or this repo's equivalent final
   lines), backend coverage at 100% with no new omits, ruff file count ~278,
   oracle tests RAN (a backend skip count around 42 means the oracle suite
   never ran — real runs have ~14 skips **plus exactly one** new skip, the
   commit-2 cassette test; get its name and confirm it's the only addition).

6. **Sabotage drills.** The work is UNCOMMITTED: `git checkout -- <file>`
   would DESTROY it. Before each drill, `cp` the target file aside (e.g. to
   `/tmp/drill-backup/`), mutate, run the narrowest relevant pytest, restore
   from the copy, and confirm `git diff` matches the pre-drill state. Drill
   at least:
   a. Make the retry condition never fire (e.g. force the zero-overlap check
      False) → the retry tests must go RED.
   b. Swap the 429→`rate_limited` mapping to `api_error` → status tests RED.
   c. Make `choose_image_hit`'s out-of-range branch return the last hit
      instead of None → fallback tests RED.
   d. Stop incrementing `image_failed` in `sync_create_new` → report-counter
      tests RED.
   A drill that stays green means the test asserts in a floor's shadow —
   finding, and identify what the test actually pins.
   Also skim the new tests for pinned-but-wrong behavior: do the assertions
   match the brief's semantics, or just whatever the implementation does?

7. **Report** (this exact shape):
   - Verdict: ACCEPT / ACCEPT-WITH-FIXES (list) / REJECT.
   - Findings ranked by severity, each with file:line and the brief clause
     violated.
   - Drill results table (drill → expected RED test → observed).
   - Gate evidence: the `./test.sh` tail YOU produced, skip count, ruff count.
   - Confirmation the working tree after your review is byte-identical to
     BP's delivery (`git diff --stat` before vs after).
   Do not fix anything, even trivia — the fix pass happens after the human
   reads the verdict.
