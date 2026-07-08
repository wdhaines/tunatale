# Workstream: enforce the language-plugin architecture (make it real, not aspirational)

**Status:** DONE (2026-07-08) — Phases 1–3 landed + verified; Phase 4 docs/memory done;
final `./test.sh` evidence at the bottom of the progress log.
**Owner/handoff:** this doc is the pick-up point for a fresh chat context. Read it
top-to-bottom before touching code. Sibling workstream `field-invariant-hardening.md`
(weakness #3) is the template — same shape: single declarative source + mechanical pin.

## The problem being fixed (architectural weakness #4)

> The language-plugin architecture is aspirational, not enforced. Stated design:
> "no hardcoded language logic — use language plugins." Reality: `get_lemmatizer()`
> was an `@lru_cache(maxsize=1)` singleton that broke in multi-language mode
> (backlog #25); the renderer preprocessor was pinned to the default language
> (#21); per-language settings are threaded ad-hoc via `_tt_settings(language_code)`;
> Norwegian is recognition-only via special-casing. **The second language (Norwegian)
> keeps finding these seams one bug at a time.**

The point is NOT that any single seam is unfixed — it's that **nothing prevents the
next seam**. Each was found reactively, in production, when Norwegian tripped over it.
"Enforced" means: a hardcoded-language regression fails a gate *before* it ships, and
per-language wiring has exactly one source (the registry), so there is no ad-hoc site
to forget.

## Constraint: behavior-preserving only

Like weakness #3, this is a **maintainability / enforcement** fix. Slovene behavior
must stay byte-identical; Norwegian must stay recognition-only. The safety nets:
`./test.sh` and `cd backend && uv run pytest tests/test_parity_*.py --run-oracle
--no-cov` must stay green. Phase 3 (settings/LanguageContext) touches the sync seam —
pin it with the sociable sync test (`TestSociableSync`) and the peer-sync round-trip,
never mock-and-assert.

## Findings — current state (2026-07-08)

### Already CLOSED — the three named seams are individually fixed

The registry `app/languages.py` already exists as the single source for several
per-language properties. `LanguageConfig` fields today: `language`,
`preprocessor_factory`, `deck_name`, `vocab_notetype`, `lemmatizer_type`. Accessors:
`get_language`, `get_preprocessor`, `get_deck_name`, `get_tts_voice`,
`get_lemmatizer_type`, `get_vocab_notetype`.

- **#25 `get_lemmatizer` singleton — FIXED 2026-07-05.** `get_lemmatizer(language_code)`
  is `@cache`-per-code; engine from `get_lemmatizer_type(code)`; module-level
  `_lemmatizer` singleton deleted; warm-up loops each configured language.
- **#21 renderer preprocessor — FIXED 2026-07-03.** `LessonRenderer` takes
  `dict[str, TextPreprocessor]`; `_render_section` receives `language_code`.
- **#28 card-media Slovene-hardcoding — FIXED 2026-07-03.** `get_tts_voice(code)`,
  `fetch_forvo_audio(language_code=…)`, cloze/vocab voices resolve from the registry.

So the *individual wiring bugs* are closed. What remains is the **enforcement gap**
and **two residual ad-hoc seams** below.

### STILL OPEN — the enforcement gap + residual seams (recon 2026-07-08)

1. **No mechanical enforcement of "no hardcoded language logic."** Nothing scans
   `backend/app/**` for hardcoded language literals (`"sl"`, `"no"`, `"Slovene"`,
   `"classla"`, `"stanza"`, `sl-SI-…`/`nb-NO-…` voices, deck names) outside the
   registry. #21/#25/#28 were all "found by Norwegian in prod, then fixed" — exactly
   the failure mode a gate prevents. The repo already has the *pattern* for this
   (`scripts/check_mock_boundaries.py` + `mock_grandfather.txt` shrink-only ledger).
   **This is the core of "make it enforced" and is unambiguously worth doing.**

2. **Renderer preprocessor — residual latent bug (beyond #21's top-level fix).**
   `LessonRenderer._render_section(..., language_code: str = "sl")` — a **hardcoded
   Slovene default** at `audio/renderer.py:156` — and the lookup at `renderer.py:171`:
   `self._preprocessors.get(language_code, next(iter(self._preprocessors.values())))`
   **silently substitutes an arbitrary preprocessor** on a language miss instead of
   erroring. Masked today because single-language mode's dict has one entry and both
   preprocessors are pass-throughs. Also `generation/breakdown_audio.py:41,78` builds
   a Norwegian-only renderer with `_LANGUAGE_CODE = "no"` hardcoded. Fix = raise on a
   miss (fail loud) + drop the `"sl"` default. Behavior-preserving for correct usage.

3. **`_tt_settings(language_code)` threads per-language settings ad-hoc.** At
   `app/anki/sync_orchestrator.py:150-178`: returns a **cloned `Settings`** via
   `settings.model_copy(update=…)` — no language-context type; threads
   `database_url` / `anki_deck_name` (`get_deck_name`) / `target_language` as three
   ad-hoc keys, **only when `settings.database_urls.get(code)` is truthy** (multi-lang
   mode). In single-language mode a non-default `code` silently falls back to the
   `.env` default deck/db (docstring `:163-166` documents the exact prior bug). One
   prod caller: `sync_orchestrator.py:474`. Candidate fold: a single `LanguageContext`
   so callers thread one object, not N ad-hoc lookups. **Parity-sensitive** (peer-sync
   path) — must be behavior-preserving, pinned by `TestSociableSync` + peer-sync.

4. **Norwegian recognition-only — NOT a hardcoded special-case (design holds).**
   Recon found **no `if lang=="no"`** in direction assignment. It's *structural*: the
   imported "6000 Most Frequent Norwegian Words" deck is a recognition-only notetype,
   so `import_seed._build_directions` (`import_seed.py:170-184`) creates only the
   directions that exist in Anki. The runtime "special-casing" is a **generic
   defensive check** — `resolve_active_direction` (`srs/transcript.py:152-161`) keeps
   single-direction cards on the direction they have (guards a `KeyError`), not a
   language branch. **Caveat/inconsistency (the real, minor seam):** `NORWEGIAN_VOCAB`
   (`vocab_notetype.py:45`) defines *both* templates, and TT-minted cards get both
   directions (`db_collocations.py:102-105`, keyed on `card_type` not language), so
   "Norwegian = recognition-only" is true only for the *seed deck*. Direction policy
   is spread across 3 sites (mint / import / runtime) with no single
   `directions_for(...)` helper. **A `directions` registry field would be WRONG** here
   (it'd contradict the mint behavior) — the only actionable item is optional
   *legibility* (single-source the policy + a doc note), not a behavior change.

## Plan (TDD, behavior-preserving) — phased, mirrors weakness #3

### Phase 1 — Enforcement net (the core of "make it enforced") — HIGH VALUE, LOW RISK
A pytest gate (`tests/test_no_hardcoded_language.py`, mirroring
`check_mock_boundaries.py`) that AST/regex-scans `backend/app/**` for hardcoded
language literals **outside an allowlist** (the plugin modules: `languages.py`,
`models/language.py`, the preprocessor/notetype/voice-map plugin files, config
defaults). Literal set: bare codes (`"sl"`,`"no"`), language names (`Slovene`,
`Norwegian`), engine names (`classla`,`stanza`), TTS voices (`sl-SI-…`,`nb-NO-…`),
deck-name strings. Ship with a **shrink-only grandfather ledger**
(`tests/language_literals_grandfather.txt`) of existing literals so it's adoptable
immediately and ratchets down. Wire into `./test.sh` + CI backend job. **This catches
the NEXT seam before Norwegian does — the whole point of #4.**

### Phase 2 — Harden the two genuine residual seams (behavior-preserving)
NOT "add a `directions` registry field" (recon: that would contradict TT-mint
behavior — see finding 4). Instead:
- **2a — Renderer fail-loud.** `renderer.py:171` raise `KeyError`/`ValueError` on a
  preprocessor miss instead of `next(iter(...))` silent-substitute; drop the `"sl"`
  default on `_render_section`. Guardrail: two-language renderer, ask for an
  unconfigured code → raises (today: silently mis-preprocesses).
- **2b — (optional) single-source direction policy.** A `directions_for(...)` helper
  the mint/import/runtime sites read, purely for legibility. Behavior-identical.
  Lower priority — the design already holds.

### Phase 3 — Fold per-language settings into one `LanguageContext` (`_tt_settings`)
Make the registry the single source for db_url + deck + target_language; optionally a
`LanguageContext` dataclass bundling {code, language, preprocessor, lemmatizer_type,
tts_voice_map, deck_name, db_url}. **Riskiest, parity-adjacent** (peer-sync) — pin
with `TestSociableSync` + `--run-peer-sync`; oracle harness green before/after.
**Default = DEFER** unless the user opts in.

### Phase 4 — Docs + memory
Update root `CLAUDE.md` "No hardcoded language logic" convention to point at the
registry + the Phase-1 gate. Update this doc's progress log. Write a memory entry.

## Scope decision
**2026-07-08: user chose FULL scope (Phases 1–3 incl. the `_tt_settings`/LanguageContext
fold)** — same "strongest scope" call as weakness #3. Order: 1 → 2 → 3 → 4, TDD,
behavior-preserving, `./test.sh` + `--run-oracle` + `--run-peer-sync` green at each
phase boundary. Phase 3 is the parity-sensitive one — sociable + peer-sync pinned.

## Key files
- `app/languages.py` — the registry (extend: `directions`, maybe `db_url`) + `get_directions`
- `app/models/language.py` — `Language` domain model (`tts_voice_map`, direction sets?)
- `app/anki/sync.py` / `sync_*.py` — `_tt_settings`, peer-sync language threading
- `app/audio/preprocessing/{base,slovene,norwegian}.py` — preprocessor plugins
- `app/anki/vocab_notetype.py` — notetype plugins
- `app/srs/lemmatizer.py` — `get_lemmatizer(code)` (already per-code)
- `scripts/check_mock_boundaries.py` + `tests/mock_grandfather.txt` — the ledger PATTERN to copy
- `tests/test_no_hardcoded_language.py` (NEW, Phase 1) + `tests/language_literals_grandfather.txt` (NEW ledger)
- `docs/bug-refactor-backlog.md` §§21/25/28 — the fixed-seam history

## Progress log
- 2026-07-08: Opened workstream. Confirmed #21/#25/#28 already fixed; registry
  `app/languages.py` exists. Recon agent mapped exact call-sites (findings above):
  seam 1 (lemmatizer) holds; seam 2 has a residual silent-fallback latent bug
  (`renderer.py:171`); seam 3 is the real ad-hoc `_tt_settings` seam
  (`sync_orchestrator.py:150-178`, prod caller `:474`); seam 4 design HOLDS (structural,
  no `if lang=="no"`) — a `directions` registry field would be wrong. User chose FULL
  scope (1–3). Set up tasks #1–4 (blocked-by chain).
- 2026-07-08: **Phase 1 dispatched** to a Sonnet subagent (prescriptive brief). Gate
  design settled from a noise measurement (`grep` counts): match AST `str` Constants
  (docstrings EXCLUDED) that are (a) exact bare code `sl`/`no`/`nb`, (b) case-insens.
  substring `slovene`/`slovenian`/`norwegian`, (c) `classla`/`stanza`, (d) TTS-voice
  regex `\b[a-z]{2}-[A-Z]{2}-[A-Za-z]+Neural\b`. Allowlist = FILE globs for sanctioned
  plugin homes (`languages.py`, `models/language.py`, `audio/preprocessing/*.py`,
  `anki/vocab_notetype.py`, `anki/add_vocab_notetype.py`, `srs/lemmatizer.py`,
  `config.py`). Everything else → auto-generated shrink-only ledger. Seams the measure
  surfaced (destined for the ledger, refactor targets later): `section_builder.py:61,210`
  (`if language_code == "no"`), `api/srs.py:722` (`_VALID_LANGUAGE_CODES` frozenset),
  `generation/syllabify.py:203` (code-keyed dispatch), `media/pixabay.py:136`,
  `generation/breakdown_audio.py:41` (`_LANGUAGE_CODE="no"`), `audio/renderer.py:156`
  (`language_code="sl"` default — Phase 2a removes it → ledger ratchets down).
- 2026-07-08: **Phase 3 design (read-only recon).** Per-language resolution is
  scattered across 3 sites with *non-identical* rules — DO NOT naively merge:
  * `_tt_settings` (`sync_orchestrator.py:150-178`): `db_url = database_urls[code]`
    **iff** `database_urls.get(code)` truthy, else `settings.database_url` (even when
    `code != target_language`); `anki_deck_name`/`target_language` overridden **only**
    in that multi-mode branch; URL absolutized via `_absolute_sqlite_url`; always sets
    `anki_collection_path = tt_collection_path`. A `None`/unconfigured code
    **intentionally** falls back to singular defaults (docstring `:163-166`) — preserve.
  * `_language_db_map` (`main.py:86-95`): `dict(database_urls)` if set, else
    `{target_language: database_url}`.
  * middleware (`main.py:215-228`): `X-TT-Language` header → `target_language`;
    unknown code → `target_language`; binds `srs_db/content_store/language` from the
    pre-built `srs_dbs` map.
  Approach: `LanguageContext` dataclass + `resolve_language_context(code, settings)` in
  `app/languages.py` reproducing `_tt_settings`'s rule EXACTLY (incl. the quirks);
  `_tt_settings` becomes a thin adapter. **Characterization-test FIRST** (pin current
  `_tt_settings("sl"/"no"/None/unconfigured)` field-by-field), THEN refactor under it;
  `TestSociableSync` + `--run-peer-sync` green before/after. If merging the request-path
  sites would risk any behavior delta for marginal gain, STOP at the `_tt_settings`
  fold + a shared `get_db_url` accessor and note it — don't touch the middleware.
- 2026-07-08: **Phase 1 DONE + VERIFIED.** Sonnet subagent built
  `scripts/check_language_literals.py` (AST gate), `tests/language_literals_allowlist.txt`
  (7 plugin-home globs), auto-generated `tests/language_literals_grandfather.txt`
  (27→ shrink-only ledger), `tests/test_check_language_literals.py` (40 tests); wired into
  `test.sh` + `ci.yml` (+ a `scripts/.gitignore` `!` line so the script is trackable).
  I independently **sabotage-drilled** it (a fresh `"no"` in app/ → FAIL exit 1; cleanup →
  exit 0) so the gate provably catches its target. Backend gate green: **3594 passed,
  100% cov**. The ledger's genuine seams (future targets, not this workstream):
  `section_builder.py` `if language_code=="no"` (×2), `api/srs.py` `_VALID_LANGUAGE_CODES`,
  `syllabify.py` + `prompts.py` code-keyed dispatch dicts, ~8 `"sl"` default-param hardcodes.
- 2026-07-08: **Phase 2a DONE + VERIFIED.** `renderer.py:_render_section` now raises
  `ValueError` on a preprocessor language-miss (was `.get(code, next(iter(...)))` silent
  substitute) and the `"sl"` default param is gone → its ledger line removed (ratchet
  worked). TDD: red (`DID NOT RAISE`) → green. Backend gate green: **3595 passed, 100% cov**.
- 2026-07-08: **Phase 3 code LANDED (gate running).** `LanguageContext` frozen dataclass
  + pure `resolve_language_context(code, settings)` added to `app/languages.py` (allowlisted
  → no ledger impact), reproducing `_tt_settings`'s resolution EXACTLY; `_tt_settings`
  (`sync_orchestrator.py`) rewritten as a thin adapter producing byte-identical `Settings`
  clones (always sets deck/target, which equal the defaults in the fallback branch, so
  no behavior delta). The 5 existing `_tt_settings` tests (characterization net) pass
  unchanged; added 4 `TestResolveLanguageContext` tests incl. the config-present-in-fallback
  branch. Targeted 43 passed, ruff+checker clean. **Deliberately LEFT** `_language_db_map`
  + the request middleware (`main.py`) — their per-language rules differ from `_tt_settings`
  and merging risks a behavior delta for marginal gain; `LanguageContext` is the adoption
  point if we consolidate them later. Next: full backend gate + `--run-peer-sync`.
- 2026-07-08: **Phase 3 VERIFIED.** Full backend gate green (**3599 passed, 100% cov**,
  incl. `TestSociableSync` — the b0a4b8a phase-drop guard). Peer-sync round-trip
  (`--run-peer-sync`, throwaway server) **6/6 passed** (bidirectional convergence,
  TT-added-card propagation, media round-trip parity) — the refactored `_tt_settings`
  is behavior-preserving on the real sync path.
- 2026-07-08: **Phase 4 DONE.** Updated `AGENTS.md` (= `CLAUDE.md` symlink) "No
  hardcoded language logic" convention → points at the registry accessors +
  `resolve_language_context` + the enforced gate. Wrote memory
  `project_language_plugin_hardening`. **Final `./test.sh`: `=== All checks passed ===`,
  exit 0** — backend (100% cov + ruff + mock-boundary + language-literal checkers),
  frontend coverage gate 100% on all 46 files, E2E **18/18** (incl.
  `generate-norwegian.spec.ts` — nb-NO voices). Changes uncommitted (awaiting user's
  go-ahead to commit/push; no CI URL yet).

## Follow-on: ledger-shrinking loop (2026-07-08, post-#4)

Routing the genuine ledger seams through the registry. Two recon subagents triaged
all 26 entries. Committed `97bc0db` = the weakness-#4 base (ledger 26).

**Batch A — dispatch/config routing (registry scalar flags, behavior-preserving):**
- `api/srs.py` `_VALID_LANGUAGE_CODES` → `known_language_codes()` (= `frozenset(_CONFIGS)`).
- `section_builder.py` two `if …=="no"` branches → `uses_compound_word_breakdown(code)`
  (new `LanguageConfig.compound_word_breakdown` bool, `True` for `no`). Used SCALAR
  flags, NOT callables, to keep `languages.py` free of a `generation` import / cycle.
- `prompts.py` `_MORPHOLOGY_SECTIONS={"sl": …}` → `get_morphology_profile(code)=="slavic"`
  (new `LanguageConfig.morphology_profile`, `"slavic"` for `sl`); content stays in
  `prompts.py` (only a scalar flag on the registry → no cycle).
- `syllabify.py` → **ALLOWLISTED** (not routed): 2 of its 3 `"sl"` hits are phonotactic
  onset clusters (`_VALID_ONSETS`), NOT codes — it's a phonotactics plugin home like
  `audio/preprocessing/`. Registry-routing the dispatch table alone couldn't clear the
  onset hits, so allowlisting is the correct + complete call.
- Ledger 26 → 20. Import-sanity checked (no cycle); `known_language_codes` +
  `uses_compound_word_breakdown` + `get_morphology_profile` pinned in `test_languages.py`.
  Committed `378df0e`.

**Batch B — allowlist a schema home (20 → 18):**
- `app/anki/field_map.py` → **ALLOWLISTED**. Its flagged literals are Anki notetype +
  FIELD NAMES (the "6000 Most Frequent Norwegian Words" notetype's "Norwegian word"
  field) — external Anki schema strings, exactly like the allowlisted `vocab_notetype.py`.
- **Tried & reverted**: sourcing `tts.DEFAULT_VOICE` from `get_tts_voice("sl")` — the
  gate correctly rejected it (it only *relocated* the literal `sl-SI-PetraNeural` → bare
  `"sl"`, no real de-hardcoding; an import-time `settings.target_language` read would be a
  footgun). The named fallback voice constants stay frozen in the ledger — that is the
  correct disposition, not a miss.
- Ledger regenerated from ground truth via `--write-grandfather` (beats manual line
  surgery). 18 seams remain, all in the "left frozen" classes below.

**Deliberately LEFT FROZEN in the ledger (recon-backed rationale — NOT bugs):**
- The `"sl"` **default params** on helpers (`forvo`, `pipeline`, `vocab_media`,
  `sync_writer.create_note`/`create_cloze_note`, `db_collocations.add_collocation`):
  every **production** caller passes `language_code` explicitly — the default is dead in
  prod and exists only for **test convenience**. Re-sourcing to `settings.target_language`
  would be an import-time-captured value (footgun) for zero behavioral/architectural gain
  and would churn ~30–50 test call sites. Frozen is correct.
- `sync.py:327` `getattr(_s, "target_language", "sl")` — defensive fallback for
  duck-typed `FakeSettings` test doubles; `_s` may intentionally differ from global
  settings, so it can't read `settings.target_language`. Not a registry seam.
- `regloss_lessons.py:160` — argparse `--language` default; funnels into `get_language()`
  one line later. CLI ergonomics.
- Benign/false-positive: `pixabay.py "no"` (English word key, not a code),
  `story.py`/`lesson.py` `en-US-GuyNeural` (the English narrator, not an L2 —
  already in each language's voice map under the `narrator` role; routing the domain
  model through the registry is a layering call left for later), the `tts.py`/`cloze_tts.py`
  `sl-SI-PetraNeural` fallback voice constants (de-hardcoding just relocates the literal —
  see Batch B), `prompts.py` SYSTEM_PROMPT blob (illustrative),
  `breakdown_audio.py` CLI description, `sqlite_reader.py` `class="slovene"` Anki-template
  regex (genuinely Slovene-template-specific parsing).

**Two genuine findings surfaced by recon (flagged to user, NOT auto-fixed):**
1. **`sqlite_writer.py::plan_guid_backfill` is ORPHANED** — zero production callers
   (its `archive/backfill_guids.py` CLI is gone; only a stale `.pyc` remains). Dead code
   → delete, or keep for a future backfill? User's call.
2. **`audio/backfill_cloze_tts.py` has a pre-existing multi-language bug** — it queries
   cloze collocations across ALL languages (`WHERE card_type='cloze'`, no language
   filter) but calls `synthesize_cloze_audios(...)` without a `voice`, so every non-Slovene
   cloze gets voiced with `sl-SI-PetraNeural`. Real behavior bug (like the renderer one),
   NOT a mechanical default-swap — fix = resolve each row's `language_code` and pass
   `voice=get_tts_voice(row_lang)`. Its own ticket.

## Outcome
Weakness #4 is closed as an *enforcement* fix, not a one-off seam patch. The gate
(`check_language_literals.py`) now fails a hardcoded-language regression in CI/pre-commit
*before* Norwegian hits it in prod — the recurring failure mode. `LanguageContext`
single-sources the per-language sync wiring in the registry. Remaining ledger entries
are genuine seams (see the Phase-1 progress entry) to route through the registry
opportunistically, shrinking the ledger over time.
