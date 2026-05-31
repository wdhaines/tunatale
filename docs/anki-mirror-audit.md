# Anki / FSRS Mirror Audit Workflow

A repeatable, inspection-driven process for finding divergences between TunaTale's
FSRS/scheduler mirror (`backend/app/srs/fsrs.py`, `queue_stats.py`, `app/api/srs.py`,
`app/anki/sync.py`) and Anki's reference implementation — **before** they surface as
a user-visible divergence report.

This complements, and is upstream of, the two existing safety nets:

- **`docs/anki-parity-layers.md`** — the post-hoc log of divergences already found and fixed.
- **The compare-shadow soak** (`.claude/rules/anki-queue-parity.md` "Soak health check") —
  catches divergences that survive a *sync round-trip*. Critically, the soak runs
  **incrementally**: it anchors each card to its last-synced `DirectionState` (= Anki's
  authoritative `cards.data`) and forward-steps only revlog rows newer than `since_id`
  (`_write_compare_shadow` → `rebuild_from_revlog(starting_state=…, since_id=…)`). So the
  soak validates *"Anki's value + the few grades since last sync"*, **not** the live
  `schedule()` path re-derived from scratch. A bug in `schedule()` that only bites
  *between* syncs (and is overwritten by the next sync's anchor) is **invisible to the
  soak** even though the soak and the live path share the exact same `schedule()` code.

That blind spot is the reason this workflow exists. Audit `schedule()` against the
algorithm, not against the soak.

---

## 0. When to run it

- Anytime the installed Anki binary or its bundled `fsrs` crate version changes (the
  source you mirror moved under you).
- Before opening a new Layer fix (extends the "Pre-Layer checklist" in
  `.claude/rules/anki-queue-parity.md`).
- Periodically, as a cheap inspection sweep — it found two real bugs on the 2026-05-30
  pass (Layers 62, 63) on an otherwise clean-soak codebase.

---

## 1. Pin the exact source you mirror (do this first — it's the #1 footgun)

`/tmp/anki-source` is a shallow clone of `main`; `.claude/rules/anki-queue-parity.md`
rule 13 warns it can be ahead of or behind the user's binary. Pin **both** repos to the
versions the user actually runs:

```bash
# 1a. What Anki binary is installed?
cd backend && uv run --with anki python -c \
  "from anki.buildinfo import version; print(version)"      # e.g. 25.09.4

# 1b. Clone Anki at that exact tag (NOT main).
cd /tmp && git clone --depth 1 https://github.com/ankitects/anki.git anki-source
cd /tmp/anki-source && git fetch --depth 1 origin tag 25.09.4 && git checkout 25.09.4

# 1c. The FSRS math is an external crate. Find the pinned version...
grep -A3 'name = "fsrs"' /tmp/anki-source/Cargo.lock   # e.g. version = "5.1.0"

# 1d. ...and clone fsrs-rs at THAT tag. This is the real source of truth for the
#     stability/difficulty/interval arithmetic; rslib only wires it into the scheduler.
cd /tmp && git clone --depth 1 --branch v5.1.0 \
  https://github.com/open-spaced-repetition/fsrs-rs.git fsrs-rs
```

The Python oracle for the math is `fsrs_rs_python` (declared in `pyproject.toml`), which
wraps the same crate — it's the executable counterpart of `/tmp/fsrs-rs`.

---

## 2. Read the live deck config (decides live vs dormant)

Every divergence is either **live** (the user's config/data can reach it),
**dormant-reachable** (a config change or enough grades would reach it), or **inert** (a
source divergence with no observable output difference). You can't triage without the
real config. Snapshot read-only and dump the FSRS fields:

```bash
cp "$HOME/Library/Application Support/Anki2/Will/collection.anki2" /tmp/anki_inspect.db
sqlite3 /tmp/anki_inspect.db "PRAGMA wal_checkpoint(TRUNCATE);" >/dev/null
```

```python
# cd backend && uv run python
import sqlite3
from app.anki.protobuf_wire import find_varint_field, find_fixed32_field
from app.srs.queue_stats import _pb_find_packed_float_field, _read_conf_id_for_deck
from app.anki.safety import _register_anki_collations
c = sqlite3.connect("file:/tmp/anki_inspect.db?mode=ro", uri=True); _register_anki_collations(c)
blob = bytes(c.execute("SELECT config FROM deck_config WHERE id=?",
            (_read_conf_id_for_deck(c, "0. Slovene"),)).fetchone()[0])
print("learn_steps", _pb_find_packed_float_field(blob, 1))      # f1
print("relearn_steps", _pb_find_packed_float_field(blob, 2))    # f2
print("desired_retention", find_fixed32_field(blob, 37))        # f37 (NOT 40!)
print("max_review_interval", find_varint_field(blob, 16))       # f16
print("review_order", find_varint_field(blob, 33))              # f33 (7 = R-asc)
# collection-level bools live in the `config` table, not deck_config:
for k in ("fsrsShortTermWithStepsEnabled", "loadBalancerEnabled", "rollover"):
    print(k, c.execute("SELECT val FROM config WHERE key=?", (k,)).fetchone())
```

**Live values captured 2026-05-30** (the regime the current fixes were triaged against):
`learn=[1,10]`, `relearn=[10]`, **`desired_retention=0.86`** (not 0.9), `max=36500`,
`review_order=7`, FSRS-5 custom weights, `fsrsShortTermWithStepsEnabled=false`,
`loadBalancerEnabled=true`, leech_threshold=99 (effectively off), max stored stability
≈310 (≪ S_MAX), min stored stability ≈0.0048.

---

## 3. The source map — what to read, paired with TT's mirror

The FSRS math (`/tmp/fsrs-rs/src/model.rs`) and the scheduler state machine
(`/tmp/anki-source/rslib/src/scheduler/states/*.rs`) are the two surfaces. Audited
against **anki 25.09.4 / fsrs-rs 5.1.0** on 2026-05-30.

| Anki / fsrs-rs source | What it does | TT mirror |
|---|---|---|
| `model.rs:52 power_forgetting_curve` | R = (t/s·factor+1)^decay, factor=exp(ln0.9/decay)−1 | `_forgetting_curve`, `_fsrs_factor_f32` |
| `model.rs:58 next_interval` | s/factor·(dr^(1/decay)−1) | `_next_interval`, `_next_interval_raw` |
| `model.rs:68 stability_after_success` | recall stability | `_next_stability_recall` |
| `model.rs:91 stability_after_failure` (+ `new_s_min` floor) | lapse stability | `_next_stability_lapse` |
| `model.rs:107 stability_short_term` | same-day (Δt=0) stability | `_stability_short_term` |
| `model.rs:117 mean_reversion` / `134 next_difficulty` / `130 linear_damping` | difficulty update | `_next_difficulty` |
| `model.rs:139 step` — **selects by (rating, Δt) then `clamp(S_MIN,S_MAX)`** | the per-grade memory state | `_next_stability_for_grade` (+ `_clamp_stability`) |
| `simulation.rs:41-44` S_MIN/S_MAX/D_MIN/D_MAX = 0.001/36500/1/10 | clamps | `_clamp_stability`, difficulty clamp in `_next_difficulty` |
| `inference.rs:223 next_states` — interval from **unrounded** `memory.stability` | per-rating (memory, interval) | `schedule` cascade (see Finding F-3) |
| `states/steps.rs` get_index / hard_delay_secs / good_delay_secs | learning step indexing + Hard-first-step delay | `_schedule_new`, `_schedule_with_steps` |
| `states/learning.rs` answer_*  | learning→learning / graduation | `_schedule_with_steps`, `_graduate_to_review` |
| `states/relearning.rs` answer_* | relearning→relearning / graduation | `_schedule_review_again`, `_schedule_with_steps` |
| `states/review.rs passing_fsrs_review_intervals` | hard/good/easy cascade + greater_than_last | `_passing_intervals_with_fuzz` |
| `states/fuzz.rs` with_review_fuzz / constrained_fuzz_bounds / fuzz_delta | interval fuzz | `_review_interval_fuzz`, `_constrained_fuzz_bounds`, `_fuzz_delta` |
| `answering/learning.rs learning_ivl_with_fuzz` | learning-step in-seconds fuzz | `_learning_step_fuzz_seconds` |
| `answering/mod.rs:485-496` short-term gates | `fsrs_allow_short_term` = w17>0 && w18>0; `fsrs_short_term_with_steps` = collection bool | (see Finding F-2) |

---

## 4. The executable heart: differential tests against `fsrs_rs_python`

Reading is how you *find* a candidate; a differential test is how you *prove* it and
keep it fixed. The pattern (no Anki subprocess needed, runs in default `./test.sh`):

```python
import fsrs_rs_python
f = fsrs_rs_python.FSRS(DEFAULT_FSRS5_PARAMS.weights)
anki = f.next_states(fsrs_rs_python.MemoryState(s, d), dr, days_elapsed)  # .again/.hard/.good/.easy
#   → .<rating>.memory.stability / .difficulty   (CLAMPED, post-step)
#   → .<rating>.interval                         (from UNROUNDED stability)
```

Compare to TT at the **value Anki persists** (4dp stability / 3dp difficulty —
`_quantize_stability` / `_quantize_difficulty`), not raw `==`: f32 transcendentals differ
by ~1 ULP across libm, far below storage resolution. See the rationale block in
`tests/test_parity_fsrs_f32.py`.

**Two axes that the per-helper tests miss and are worth sweeping explicitly** (each
caught a real bug this pass):

1. **`days_elapsed == 0` across all four ratings, driven through the public `schedule()`
   path** (not just the helpers) — `tests/test_parity_same_day_review.py`. The helpers
   are correct; the bug was `schedule()` calling the wrong helper at Δt=0 (Finding F-1).
2. **Stability at the `[S_MIN, S_MAX]` boundaries** — `tests/test_parity_stability_clamp.py`.
   The math helpers don't clamp (fsrs-rs clamps in `step`, not in the formulas), so the
   boundary only shows up when you drive a card to the floor (Finding F-2).

For full state-machine parity (queue order, learning steps, fuzz, transitions) use the
Anki **oracle harness** instead (`tests/test_parity_*.py` + `--run-oracle`); see
`.claude/rules/anki-oracle-harness.md`.

---

## 5. Triage rubric (live / dormant / inert)

For each candidate divergence, before writing a fix:

1. **Reproduce against `fsrs_rs_python`** (or the oracle) at a concrete input. If TT and
   fsrs-rs agree, it's not a divergence — stop.
2. **Classify with §2's live config:**
   - **Live** — the user's config + plausible grading reaches it *between syncs*. Fix now.
     (F-1 was live: 717 card-days had same-day passing re-reviews.)
   - **Dormant-reachable** — a config change or enough grades would reach it. Fix if
     clean/low-risk; it's a latent correctness bug. (F-2: floor card ~5 lapses away.)
   - **Inert** — a real source divergence with no observable output difference under the
     live config. **Do not change the code** (you can't test the difference and you risk a
     validated hot path). Document it here so the next audit doesn't re-litigate it.
3. **TDD the fix** (red → green): write the differential test that fails first, then the
   minimal change, then re-run `./test.sh` *and* `--run-oracle`.

---

## 6. Findings log

### F-1 — REVIEW + passing same-day grade ignored the short-term override → **Layer 62 (fixed)**
`schedule()`'s REVIEW + HARD/GOOD/EASY branch called `_next_stability_recall` directly,
but fsrs-rs `step` (model.rs:163) overrides with `stability_short_term` for *every* rating
when `delta_t==0`. REVIEW+AGAIN already handled it; the passing path didn't. **Live**
(reproduced 175.05 vs Anki 132.667 on a card graded EASY twice on 05-08). Fixed by routing
the cascade through `_next_stability_for_grade`. Test: `test_parity_same_day_review.py`.

### F-2 — Stability not clamped to `[S_MIN, S_MAX]` → **Layer 63 (fixed)**
fsrs-rs clamps every `step` output to `[0.001, 36500]` (model.rs:178); TT clamped
inconsistently (`max(0.001, …)` some sites, none on the lapse/learning-step paths, never
S_MAX). **Dormant-reachable** at the lower bound (lapse floor drops below 0.001 near the
minimum-stability regime). Fixed via `_clamp_stability` in `_next_stability_for_grade` and
`_schedule_review_again`. Test: `test_parity_stability_clamp.py`.

### F-3 — Review interval from 4dp-quantized stability vs fsrs-rs's unrounded `memory.stability` → **inert, not fixed**
`next_states` (inference.rs:252-261) computes `states.X.interval` from the unrounded
post-step stability; TT feeds `_quantize_stability(s)` into `_next_interval_raw`. At the
user's dr the amplification is `s·~1.5`, so a 4dp (5e-5) stability delta moves the float
interval by <1e-4 — never tips an integer-day boundary (Layers 50/51 measured intervals
bit-exact at dr=0.86). **Inert.** Don't change it: it's a validated hot path and the
difference is unobservable.

### F-4 — `fsrsShortTermWithStepsEnabled` read into cache but unused in `schedule()` → **dormant (flag off), documented**
The collection bool gates the learning/relearning "stay-in-learning when interval<0.5"
short-term branches (learning.rs:55, relearning.rs:73). The flag is cached
(`refresh_fsrs_short_term_flag`) but `schedule()` always graduates directly. **Dormant** —
the user's flag is `false`, so the gated branch is `false` regardless. If the user ever
enables it, this becomes live and needs the branch plumbed through. Tracked here, not
fixed, to avoid threading a substantial dormant code path through the hot path.

### F-5 — Hard-first-step delay: minute-averaging + no `maybe_round_in_days` → **inert for integer steps, documented**
`_schedule_new`/`_schedule_with_steps` compute the Hard-on-first-step delay as
`(steps[0]+steps[1])/2` minutes, while Anki averages in integer seconds and applies
`maybe_round_in_days` (steps.rs:55-65). For the user's integer-minute steps (`[1,10]`,
relearn `[10]`) the two agree exactly (330s, 900s). Only non-integer-minute or multi-day
steps would diverge. **Inert** under the live config.

### F-6 — No leech suspension → **dormant (threshold=99), documented**
Anki suspends at `leech_threshold` lapses when `leech_action=Suspend` (answering/mod.rs:193);
TT has no auto-leech path. The user's `leech_threshold=99` (max lapses=12), so unreachable.
If the threshold is lowered, leeched cards would be suspended in Anki but keep appearing in
TT until sync.

### Queue + sync surfaces — **audited 2026-05-30 @ 25.09.4, all verified clean (no change)**
The second audit pass swept the queue-ordering and sync-merge surfaces in predicted-ROI
order. Unlike the FSRS arithmetic (a dense port with few prior divergence reports), these
are the most-hammered code (Layers 25–39, 56–61) and held up:
- **R-asc `ORDER BY`** — Anki's `review_order_sql` (`card/mod.rs:850-901`) for FSRS is just
  `extract_fsrs_relative_retrievability(...) asc, fnvhash(id, mod)`. TT's key
  `(r, 0, _fnv1a_64_i64(id, mod), 0)` matches; `_fnv1a_64_i64` is a bit-exact FNV-1a port
  (`sqlite.rs:143-151`) — correct byte order, arg order, and signed-i64 cast for SQLite sort.
- **R value** — `extract_fsrs_relative_retrievability` (`sqlite.rs:370-451`) sorts by
  *relative* R `-(R^(-1/decay)-1)/(dr^(-1/decay)-1)`, a **monotonic** transform of raw R for
  fixed dr/decay, so TT's raw-R sort yields identical order. The `seconds_elapsed`/`days_elapsed`
  branches match `_elapsed_days_for_fsrs`. No-memory cards fall through to the SM2 formula
  `-(days_elapsed+0.001)/ivl` — the Layer 38/43 territory, already understood.
- **`_direction_differs`** (`sync.py:956`) — every sync-relevant field Anki can mutate is
  compared; the not-compared fields (`introduced_at`, `prior_left/stability`,
  `last_review_time_ms`) are either stamped on a co-occurring state change (which *is*
  compared) or are TT-internal grade artifacts Anki never sets. Complete.
- **Intersperser ratio** (`intersperser.rs:26`) `(one_len+1)/(two_len+1)` and **serve order**
  (`queue/mod.rs:158-160`) `intraday_now → main → intraday_ahead` both match TT.

Takeaway: the high-ROI inspection yield is the FSRS arithmetic, not the queue/sync layers.
Re-audit queue/sync only if a divergence report points there or the Anki version moves.

---

## 7. One-command re-run

```bash
cd backend
uv run pytest tests/test_parity_fsrs_f32.py tests/test_parity_same_day_review.py \
              tests/test_parity_stability_clamp.py -q --no-cov           # the math differential
uv run pytest tests/test_parity_*.py --run-oracle --no-cov -q            # the state-machine oracle
./test.sh                                                                # full gate (100% cov + frontend + e2e)
```
