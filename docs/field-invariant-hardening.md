# Workstream: derive per-direction field invariants from a single source

**Status:** DONE (gate green) — `./test.sh` EXIT=0 "=== All checks passed ===": ruff+format clean, backend 3576 pass / 100% cov / oracle included, svelte-check 0 errors, frontend gate 100%/46 files, playwright 18 pass. Pending only: commit (not yet committed — awaiting user; changes sit in the working tree alongside unrelated Norwegian work, stage only the invariant files).
**Owner/handoff:** this doc is the pick-up point for a fresh chat context. Read it top-to-bottom before touching code.

## What shipped (2026-07-07/08)

Half B (column invariants) is now mechanically enforced, mirroring Half A:

- **Registry metadata** (`app/srs/direction_fields.py`): `WritePolicy{FREE,ONE_SHOT,STICKY_NEW}`
  + `domain` on `DirectionField`; `BURY_KIND_DOMAIN=(None,'sched','user')`,
  `PRIOR_STATE_DOMAIN=(None,*SRSState values)` (single-sourced from the enum);
  `DOMAIN_CONSTRAINED_FIELDS` view; `iter_domain_violations` /
  `iter_coupling_violations` / `iter_direction_invariant_violations` pure validator.
- **v35 SQL CHECK migration** (`app/srs/migrations.py::migrate_v34_to_v35`,
  `CURRENT_VERSION=35`): table-recreate adding `CHECK` on `bury_kind` + `prior_state`.
  Frozen literals; pinned to the registry by `test_check_domains_match_registry`.
- **Sync diagnostic**: `_write_sync_soak_log(..., db=db)` sweeps the TT db post-sync and
  emits `INVARIANT_TRACE` lines (coupling + any domain gap). `conn` in `run_full_sync`
  is the **Anki** collection, not TT — must sweep `db` (SRSDatabase). Seam is safe:
  `TestRunFullSync` patches `_write_sync_soak_log`, so the added `db=` kwarg doesn't
  touch the b0a4b8a phase list.
- **Resolver pins** (`tests/test_direction_invariants.py`): `_grade_prior_state`↔STICKY_NEW,
  `_resolve_introduced_at`↔ONE_SHOT, `_bury_kind_from_queue`+CHECK↔bury_kind domain.
- **Docs**: `direction_fields.py` docstring + rules 7/8/10 in `anki-queue-parity.md`
  now point at the code enforcement.

Test-value fixups the CHECK surfaced: `test_anki_replay_fsrs_from_revlog.py` wrote
`prior_state='REVIEW'` (uppercase, non-canonical) → changed to `'review'`;
`test_srs_migrations.py` version assertion 34→35.

Files touched: `app/srs/direction_fields.py`, `app/srs/migrations.py`,
`app/anki/sync.py`, `tests/test_direction_invariants.py` (new),
`tests/test_anki_sync_main.py`, `tests/test_anki_replay_fsrs_from_revlog.py`,
`tests/test_srs_migrations.py`, `.claude/rules/anki-queue-parity.md`, this doc.

## The problem being fixed (architectural weakness #3)

> Sync correctness rests on hand-maintained field lists. `_direction_differs` must
> enumerate every comparable field; `_DIR_COLUMNS` must enumerate every readable
> column. At least 3 Layers (17, 35, 37) were "a field was missing from a list."
> Nothing derives these from the schema; adding a column requires remembering N
> prose rules.
>
> Same class: column-level invariants (`prior_state` sticky, `introduced_at`
> one-shot, `bury_kind` tri-state) live only in rule-file prose, enforced by
> vigilance.

## Constraint: behavior-preserving only

This is a **maintainability/legibility** fix, in the spirit of the parity-rule
"minimize the cost of *holding* the mirror — behavior-preserving only." It must
NOT change any sync/queue/FSRS behavior. The oracle harness + soak are the safety
nets: `./test.sh` and `cd backend && uv run pytest tests/test_parity_*.py
--run-oracle --no-cov` must stay green.

## Findings (2026-07-07)

### Half A — field lists — ALREADY DONE (landed 2026-07-05)

- `app/srs/direction_fields.py` is the single registry: `DIRECTION_FIELDS`
  (`DirectionField(column, model_field, sync_comparable, reason)`) +
  `NON_STATE_COLUMNS`.
- `_DIR_COLUMNS` (`app/srs/db_base.py:119`) derives from it.
- `_direction_differs` (`app/anki/sync_engine.py:47`) derives from
  `SYNC_COMPARABLE_MODEL_FIELDS`.
- `tests/test_direction_fields.py` pins registry ↔ real table schema ↔
  `DirectionState` model ↔ `_DIR_COLUMNS` ↔ per-field `_direction_differs`.

**So the Layers-17/35/37 class ("a field missing from a list") is closed.**

### Half B — column-level invariants — STILL PROSE-ONLY (this workstream)

Three write-time invariants live only in `.claude/rules/anki-queue-parity.md`
prose (rules 7, 8, 10), enforced by vigilance:

1. **`prior_state` sticky** (rule 7): `prior_state='new'` set on intro, persists
   across same-state-class grades and LEARNING→REVIEW graduation; released only on
   REVIEW→RELEARNING (lapse). "Do not overwrite `prior_state='new'` without checking
   new state."
2. **`introduced_at` one-shot** (rule 8 / Layer 26): written exactly once per
   direction on first NEW→non-NEW transition; never re-stamped, never cleared.
3. **`bury_kind` tri-state** (rule 10): `NULL` = non-buried, `'sched'` = released by
   daily sweep, `'user'` = sticks across rollover. Both Anki queue=-2 and -3 →
   `'sched'`. The 2026-05-16 bury-kind incident (140 stuck rows) was a violation.

## Enforcement map (recon 2026-07-07)

None of the three invariants has DB-level or app-level enforcement; all live in
resolver functions + `dataclasses.replace()` copy-semantics.

- **`prior_state` sticky**: `_grade_prior_state` (`app/srs/fsrs.py:763`),
  `_resolve_prior_state` (`app/anki/sync_engine.py:60`). Gap: `promote_to_learning`
  (`db_directions.py:315`) writes `state` without touching `prior_state`.
- **`introduced_at` one-shot**: guard in `_resolve_introduced_at`
  (`sync_engine.py:100`, `if is not None: return existing`), conditional stamp in
  `_graduate_to_review` (`fsrs.py:1463`), `COALESCE(introduced_at, ?)` in
  `set_state_by_id`/`mark_known` (`db_directions.py:180,240,250`). Explicitly
  *cleared* only on the NEW full-reset branch (`db_directions.py:174`).
- **`bury_kind` tri-state**: `_bury_kind_from_queue` (`sync_engine.py:179`) maps
  -2/-3 → `'sched'`, else `None`. `'user'` is written only by the one-time
  migration backfill (`migrations.py:589`). Released via `unbury_if_needed`
  (`db_queue.py:249`). Type is bare `str | None` (`srs_item.py:77`).
- **No** SQL CHECK constraint on any of the three (all bare `TEXT`,
  `migrations.py:747-751`). **No** runtime validator/assertion anywhere.
- Stale rule-file claims (do NOT fix in this workstream): "5 DirectionState sites
  in sync_pull" (actually 2) and a live `POST /api/srs/bury` endpoint (absent).

## Plan (TDD, behavior-preserving)

Mirror the Half-A solution: make the invariants a **single declarative source**
and **mechanically pin** the enforcement code to it.

1. **Registry metadata.** Add a `WritePolicy` enum + per-`DirectionField`
   invariant declaration (or a companion `DIRECTION_INVARIANTS` map):
   - `bury_kind` → ENUM_DOMAIN {None, 'sched', 'user'}
   - `introduced_at` → ONE_SHOT (set once NEW→non-NEW; cleared only on NEW reset)
   - `prior_state` → STICKY_NEW (persists until REVIEW→RELEARNING)
   - everything else → FREE. Pure data; zero behavior change.
2. **Pure validator.** `iter_direction_invariant_violations(rows)` +
   `validate_direction_state(state)` — at-rest domain checks (bury_kind ∈ domain;
   bury_kind set ⇒ state buried; prior_state ∈ SRSState|None; introduced_at set ⇒
   state != new modulo legacy NULL). No raise in prod.
3. **Pinning tests** (the mechanical enforcement): `_bury_kind_from_queue` domain
   over a swept range; `_grade_prior_state`/`_resolve_prior_state` STICKY_NEW over
   every (prev.state,new_state) pair; `_resolve_introduced_at` ONE_SHOT; validator
   clean-pass + each-seeded-violation-detected.
4. **Logged sync diagnostic (default scope):** run validator on post-pull rows in
   the sync soak heartbeat; emit non-fatal `INVARIANT_TRACE` WARNING per violation
   (mirrors BURY_TRACE / SYNC_SOAK). Behavior-preserving.
5. (**Opt-in, deferred**) SQL CHECK constraint migration on `bury_kind`
   (+ prior_state domain). Strongest, but a table-recreate migration + read-only
   pre-check of the real user DB for pre-existing violators.
6. Update `direction_fields.py` docstring + `.claude/rules/anki-queue-parity.md`
   rules 7/8/10 to point at the registry as the source of truth.

## Progress log

- 2026-07-07: Confirmed Half A done. Wrote memory
  `feedback_delegate_cheaper_models_subagents` (delegate mechanical work to
  Sonnet/Haiku sub-agents; critical under Fable). Dispatched Sonnet recon agent
  (135k subagent tokens, kept off the parent context) → filled in the Enforcement
  map + Plan above. Wrote this tracking doc.
- 2026-07-07: User chose **strongest scope (steps 1-6 incl. SQL CHECK)**.
  Read-only pre-check of BOTH real DBs (`backend/tunatale_sl.db`,
  `backend/tunatale_no.db`, both at user_version=34): `bury_kind` ∈ {NULL,sched},
  `prior_state` ∈ valid SRSState values, 0 coupling violations → CHECK migration
  is SAFE on real data. Confirmed no triggers/views on the table; `CURRENT_VERSION=34`
  → migration is v34→v35. Captured exact current 26-column schema from the DB (the
  recreate must include known_prior_* + fsrs_force_next, added after v25). Read the
  four resolver fns to pin. Migration mirrors the v24→v25 table-recreate pattern.
  Design decisions:
  * Registry gains `WritePolicy{FREE,ONE_SHOT,STICKY_NEW}` + `domain` per field.
  * Domains single-sourced: `PRIOR_STATE_DOMAIN=(None,*SRSState values)`,
    `BURY_KIND_DOMAIN=(None,'sched','user')`.
  * Pin `_grade_prior_state`→STICKY_NEW (NOT `_resolve_prior_state`, whose sync
    intro-reconstruction legitimately differs); `_resolve_introduced_at`→ONE_SHOT;
    `_bury_kind_from_queue`+CHECK→bury_kind domain.
  * Migration hardcodes CHECK literals (frozen/historical); a FUNCTIONAL test ties
    the enforced constraint back to the registry domain so a new SRSState value can't
    silently drift (UPDATE with the new value would raise → test fails → widen via a
    new migration).
  * Diagnostic (step 4): `_write_sync_soak_log` carries no DB/direction state — will
    wire a validator sweep at a clean seam LAST, or ship the validator ready + note
    it. Next: write tests (red) → implement registry+migration → green → gate → docs.

## Key files

- `app/srs/direction_fields.py` — the registry (extend here)
- `app/srs/db_base.py` — `_DIR_COLUMNS`
- `app/anki/sync_engine.py` — `_direction_differs`, prior_state/introduced_at resolution
- `app/srs/db_directions.py` — `update_direction` write helper
- `app/srs/fsrs.py` — `schedule` (stamps introduced_at, grade prior_state)
- `app/models/srs_item.py` — `DirectionState` model
- `tests/test_direction_fields.py` — the registry pin

## Open questions

- Runtime-guard (raise/log on violation in the write path) vs test-only pins vs
  a logged sync-time diagnostic? Runtime raise risks behavior change — leaning
  toward: declarative invariant metadata on the registry + a pure validator +
  tests, plus a non-fatal logged at-rest check on the sync soak path.
