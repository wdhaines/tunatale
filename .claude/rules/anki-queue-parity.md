# TT ↔ Anki Queue Parity

Required reading before changing `backend/app/api/srs.py`, `backend/app/srs/fsrs.py`, `backend/app/srs/queue_stats.py`, or `backend/app/anki/sync.py`. Full layer history at `docs/anki-parity-layers.md` — open only when "have we hit this exact thing before?"

## What "parity" means

User grades the same deck in both apps. Between syncs they run independently; next-served card and the three badge counts (`new`/`learning`/`review`) must stay close enough that switching apps doesn't feel discontinuous. **Sync is the alignment moment.** Drift between syncs is bounded and acceptable.

Anki is the reference implementation. TT mirrors Anki's algorithms — but **reads `collection.anki2` only at sync time**, never on the live request path. A request handler that consults Anki's collection is a regression unless it's `sync_pull`/`sync_push`.

## Three most common benign divergences

These three account for the bulk of "TT and Anki disagree on head card" reports. **Check all three before pattern-matching to anything algorithmic.** All three resolve at the next TT sync.

### #1 — Cutoff frozen at last grade (rule 11)

**Signature**: Anki serves a *learning* card, TT serves a *main/review* card, both apps have the card, TT's `learning_cutoff` is within seconds-to-minutes of the learning card's `due_at`.

**Why**: cutoff advances only on grade / session-start / sync_pull / end-of-session auto-bump. If you grade in Anki 2s *after* the learning card ripens, Anki advances past `due` and serves it. TT, last graded *before* the ripening, keeps cutoff frozen and the card waits in `pending_learning` tail.

**Resolution**: refresh `/review` (frontend `onMount` sends `?session_start=1` → cutoff = `now`) or grade any TT card. **Do not** add a "live `now`" path or per-poll cutoff advance — stickiness is intentional and Anki works the same way.

**Diagnostic**:
```bash
sqlite3 /tmp/tt_inspect.db "SELECT value FROM anki_state_cache WHERE key='learning_cutoff';"
sqlite3 /tmp/tt_inspect.db "SELECT c.text, cd.direction, cd.due_at FROM collocation_directions cd JOIN collocations c ON cd.collocation_id=c.id WHERE cd.state IN ('learning','relearning') ORDER BY cd.due_at LIMIT 5;"
```
If the earliest learning `due_at` is just past the cutoff, you've found it.

### #2 — Independent grading drift

Each app stamps its own `last_review` at the moment the grade fires, computes `due_at = last_review + step + fuzz`. When you grade the same card in both apps, sub-second-to-few-seconds button-press variation produces `due_at` deltas of 1–5s — enough to invert order of two cards whose Anki gap is only 1s.

**Signature (cross-queue)**: TT shows review/main, Anki shows learning, TT's `anki_due` is much older than Anki's `cards.due`.

**Signature (intra-learning)**: BOTH apps show a learning card at head, but *different* cards from the same stack — positions 0/1 (or 0/2) swapped. Per-card `due_at` deltas are O(seconds).

**Why**: identical FSRS fuzz on slightly different timestamps. peti graded in Anki 3s before TT, risati 2s after. Anki: peti(X) → risati(X+1). TT: risati(X−2) → peti(X+3). 5s total swing inverts a 1s gap.

**Resolution**: sync. `_direction_differs` includes `due_at` (rule 6); after one round-trip both apps converge on the later grader's timestamp per card. **Do not** overwrite timestamps client-side — convergence is sync's job.

**Diagnostic**:
```bash
sqlite3 /tmp/tt_inspect.db "SELECT c.text || ' ' || cd.direction, cd.anki_card_id, strftime('%s', cd.last_review) as tt_grade, cast(strftime('%s', cd.due_at) as int) as tt_due FROM collocation_directions cd JOIN collocations c ON cd.collocation_id=c.id WHERE cd.state IN ('learning','relearning') ORDER BY cd.due_at LIMIT 8;" | while IFS='|' read card cid tt_grade tt_due; do
  anki=$(sqlite3 /tmp/anki_inspect.db "SELECT mod || '|' || due FROM cards WHERE id=$cid")
  echo "  $card  TT(grade=$tt_grade due=$tt_due) Anki=$anki"
done
```
If per-card deltas are O(seconds) and the step (due − grade) matches between apps, it's drift, not a bug.

### #3 — Asymmetric queue-rebuild cadence (R-asc inversion captured by only one app)

Both apps freeze the main review queue and never re-sort mid-session (Anki `CardQueues.main` is a `VecDeque`, pop-only; `rslib/.../queue/main.rs:8-46`). But **Anki's rebuild trigger set is much larger than TT's**, so the two frozen queues can capture different snapshots of the same state.

**Anki rebuilds on** (`queue/mod.rs:207-215`): day rollover, **File→Sync** (and AnkiWeb auto-sync receiving remote changes incl. TT's push), **profile/collection reopen** (lazy-built on first access), **deck navigation**, deck-config/prefs change, undo, any non-grade card/deck mutation.

**TT rebuilds only on `sync_pull`** (rule 2). Grading does not rebuild in either app.

**Critical non-obvious trigger: TT sync itself triggers an Anki rebuild.** TT's `safe_open(mode="rw")` (`backend/app/anki/safety.py:148-162`) requires Anki to be closed. User closes Anki → runs TT sync → reopens Anki → **the reopen rebuilds Anki's queue with current R values**. TT's sync_pull moment and Anki's reopen moment can be seconds-to-minutes apart, and an R-asc cross can land between them. This is how "I only synced once, never touched Anki" still produces divergent heads. Forensic signature: a multi-minute gap in Anki's revlog spanning the cross point.

**Signature**: both apps show a *review* card at head, same candidate set, two different cards with **wildly different stabilities** (e.g. s=0.65 vs s=7.5), R values near-tie (delta ~0.001–0.005). R decays at `1/(s·86400)`/sec, so low-s decays ~12× faster than high-s — small wall-clock gaps between rebuild moments invert near-tie pairs.

**Worked example** (May 17, 2026): synced TT ~20:45 PDT. Frozen TT: prodati (R=0.8635) < cloze "se" (R=0.8639). At ~20:55 they crossed. At ~21:00 user hit **File→Sync in Anki**, clearing Anki's cache. Anki's next rebuild: cloze (R=0.8620) < prodati (R=0.8633). TT still showed prodati first.

**Resolution**: `sync_pull` from TT to rebuild its frozen queue with current R values.

**Don't try to "fix" this** with mid-session re-sort or by mirroring all of Anki's rebuild triggers. The freeze-at-build design is intentional (rule 2). Cheap fix: "sync more often." Structural fix (match Anki's trigger set) only if this class proves frequent.

## Architectural principles (don't undo these)

1. **Anki is reference, not runtime dependency.** Every badge endpoint and queue-build path reconstructs from TT state alone (`collocation_directions` + `anki_state_cache`). If you find yourself opening `collection.anki2` in `app/api/`, wrong turn — look for the TT-state-only equivalent (`count_new_introduced_today`, `count_review_due_collocations`, etc.).

2. **Cache invalidation + eager rebuild on sync.** Non-dry-run `sync_pull` *clears AND eagerly rebuilds* `session_main_queue` via `build_and_freeze_main_queue` (Layer 29). Freeze moment = sync time. New cache keys depending on Anki state must clear here too. **Deploy-time pitfall**: cache lives in `anki_state_cache` (DB-backed), survives restarts. After changing queue-assembly logic, the cache replays the OLD order until next sync — restart alone doesn't invalidate. **Always run `clear_session_main_queue` first** before concluding a fix is broken.

3. **Sibling-bury via `last_review` filter.** Anki's `bury_reviews=true` removes a note from today's pool the moment any sibling is graded. TT mirrors by excluding collocations where any direction has `last_review` today. Don't write count queries that ignore this.

4. **Sync rebuilds with current pool.** Both apps gather with current-pool counts at sync time, not session-start. Intersperser ratio is `(one_len+1)/(two_len+1)` over natural list lengths — do **not** add a session-start override (Layer 9, reverted at 14).

5. **Two-branch R formula.** `extract_fsrs_retrievability` has lrt branch (sub-day fractional elapsed) and day-level fallback (integer-day elapsed). TT mirrors both in `compute_retrievability`.

6. **Sync must merge both directions.** `sync_push` defers to Anki when Anki is ahead (graduated or smaller `total_remaining`). `sync_pull` defers to Anki when Anki is ahead. `_direction_differs` must include `left`, `due_at`, `prior_state`, `bury_kind`, `anki_card_mod` so self-heal writes actually fire.

7. **`prior_state='new'` is sticky.** Set on intro by `_resolve_prior_state` (sync) or `_grade_prior_state` (TT). Persists across same-state-class grades and LEARNING→REVIEW graduation. Released only on REVIEW→RELEARNING (lapse) for revlog `type=1` correctness. **Do not** overwrite `prior_state='new'` without checking new state.

8. **`introduced_at` is a one-shot stamp, NOT a sticky marker (Layer 26).** Written exactly once per direction on first NEW→non-NEW transition by `fsrs.schedule` or `sync_pull._resolve_introduced_at` (from `MIN(revlog.id)`). `count_new_introduced_today` queries this column, NOT `prior_state` + `last_review`. Don't conflate: `prior_state='new'` lives for the entire intro arc (revlog correctness); `introduced_at` is a fixed timestamp (anchors Anki's `newToday` parity).

9. **Daily unbury sweep at queue-build (Layer 27, refined 35).** `db.unbury_if_needed(today)` runs at the top of `/queue-stats`, `/review-queue`, `sync_pull`. Restores `state='buried' AND bury_kind='sched'` to `state='review'` (reps>0) or `'new'` (reps=0). Tracked via `anki_state_cache['last_unbury_day']`; idempotent within local day. Mirrors Anki's `unbury_on_day_rollover` (releases both `queue=-3` AND `queue=-2`). **Do NOT** let `state='buried' bury_kind='sched'` rows accumulate.

10. **`bury_kind` split (Layer 35, corrected 2026-05-16; Layer 39, corrected 2026-05-17).** Anki has `queue=-3` and `queue=-2` for buried. Source claims grade-time sibling-bury writes -3 and only explicit UI actions write -2. **Binary contradicts**: `col.sched.answerCard` placed the sibling at `queue=-2` (per rule 13). Both released by `unbury_on_day_rollover` (`rslib/.../bury_and_suspend.rs:44-50` — SQL `c.queue in (-3, -2)`). TT mirrors via `collocation_directions.bury_kind`:
    - `'sched'` → released by daily sweep
    - `'user'` → sticks across rollover (from `POST /api/srs/bury`)
    - `NULL` → non-buried

    `_bury_kind_from_queue` maps **both `queue=-2` and `queue=-3` to `'sched'`**. Pre-Layer-35 buried rows backfilled to `'user'` (pessimistic). **Do NOT** add an unconditional `UPDATE … WHERE state='buried'` — pre-Layer-35 bug that wiped 18 manually-buried cards per poll.

    **Two follow-up bugs found 2026-05-16:**
    - `_DIR_COLUMNS` in `database.py` didn't include `bury_kind` — every read returned `None`. Fixed.
    - `_direction_differs` didn't check `bury_kind` — kind-only flips were silent no-ops. Fixed.

    **BURY_TRACE diagnostic.** Every sync_pull emits `BURY_TRACE` INFO lines per buried direction + summary (`anki_queue_minus2_seen`, `anki_queue_minus3_seen`, `buried_to_released_writes`, etc.). `anki_queue_minus2_seen > 0` is no longer suspicious — Anki's binary writes -2 for sibling-bury.

11. **`learning_cutoff` has 4 advancement triggers (Layer 36).** Mirrors Anki's `current_learning_cutoff`:
    1. **Grade** — `drill_feedback` → `advance_learning_cutoff(db, now)` (Anki: `queue/mod.rs:217-243`).
    2. **Session-start** — `/review-queue?session_start=1` → cutoff = `now`. Anki: queue build at deck open.
    3. **sync_pull ingest** — advances cutoff to latest revlog timestamp pulled.
    4. **counts.all_zero auto-bump** — when `ready_learning` AND `ordered_main` are both empty AND any `pending_learning.due_at ≤ now`, advance cutoff to `now` and re-split. Mirrors `CardQueues::counts()` end-of-session "surface ripened card without a grade" path.

    **Stickiness invariant**: if main OR ready_learning has items, cutoff is frozen between grades. Auto-bump is end-of-session only. A learning card that ripens mid-session does NOT preempt the card on screen. Don't add a "live `now`" or per-poll advance.

12. **Daily caps are render-only.** `daily_new_cap` and `daily_review_cap` affect ONLY badge counters. Queue assembly (`_compute_live_main`, `get_review_queue`) does NOT cap. Anki also only applies caps to the deck-list badge, not the review flow.

13. **Trust the binary, not the source, when they disagree.** `/tmp/anki-source/` is a shallow clone of `main`, not a release tag — can be ahead of or behind the user's Anki. When TT mirrors source and still diverges, reproduce against the binary:
    ```bash
    cp "$HOME/Library/Application Support/Anki2/Will/collection.anki2" /tmp/anki_inspect.db
    sqlite3 /tmp/anki_inspect.db "PRAGMA wal_checkpoint(TRUNCATE);" >/dev/null
    uv run --with anki python -c "
    import shutil; shutil.copy('/tmp/anki_inspect.db', '/tmp/anki_writable.db')
    from anki.collection import Collection
    col = Collection('/tmp/anki_writable.db'); col.decks.select(1)
    q = col.sched.get_queued_cards(fetch_limit=20)
    for qc in q.cards:
        c = qc.card; n = col.get_note(c.note_id)
        print(c.id, c.queue, n.fields[0][:25])
    col.close()"
    ```
    Anki must be CLOSED (Collection wants exclusive write access). Layer 38 was found this way. Source remains the right starting point — just not the final word.

14. **NULL R-value sorts at `desired_retention` (Layer 38).** Cards with no FSRS memory_state (`cards.data='{}'`) are placed by Anki at the position `desired_retention` occupies in R-asc — between R<dr and R>dr, NOT NULLs-first, NOT NULLs-last. `compute_retrievability` returns `desired_retention` (default 0.9) instead of None. Cached at sync via `refresh_desired_retention` (**proto field 37**, NOT field 40 = `historical_retention`, a pre-Layer-38 footgun).

15. **`anki_card_mod` must be in `_direction_differs` (Layer 37).** FNV tiebreaker `fnvhash(cards.id, cards.mod)` is appended to every Anki `review_order_sql` variant (`rslib/.../card/mod.rs:897`). Anki bumps `cards.mod` for non-FSRS reasons (sync mtime, housekeeping, bury); if the diff misses the bump, TT's FNV hash drifts. Keep `or local.anki_card_mod != candidate.anki_card_mod` — it's an ORDER BY input.

## Divergence playbook

Walk this tree on a divergence report. Each leaf → mechanism that handles it; verify it's still firing, then look for new edge cases.

**Badge wrong:**
- `new`: `db.count_new_introduced_today(today)` filters `introduced_at` within today's UTC range (Layer 26). Stamped once per intro arc. Pre-Layer-26 rows have NULL and don't count (intentional). "Introduced X, badge didn't decrement" → check `SELECT introduced_at FROM collocation_directions WHERE collocation_id=...`. Empty = grade path didn't stamp. Don't reintroduce the legacy `prior_state='new' AND last_review today` filter.
- `learning`: `db.count_learning()` — pure TT state. Drifts on Anki-side grades until sync (expected). **Caveat**: `promote_to_learning` from listen-first UI sets `state='learning'` without `left`/`due_at` — TT counts it while Anki keeps `queue=0`. Documented TT-only addition.
- `review`: `min(db.count_review_due_collocations(today), max(0, daily_review_cap − reviews_today))`. **Layer 27**: stale `state='buried'` rows under-count. **Layer 36**: cap via `reviews_per_day`. If consistently lower than raw count, check `anki_state_cache['daily_review_cap']` and `count_reviews_completed_today`. Daily unbury sweep fires on every relevant request — check `COUNT(*) WHERE state='buried'` against siblings of today's grades.

**Queue head wrong (review-state):**
- **CHECK FIRST** — top-of-file divergence #3 (asymmetric rebuild cadence) if heads have wildly different stabilities and R near-tie.
- Compare R values (snippet below). Different R → check which branch of `extract_fsrs_retrievability` Anki used (lrt presence) and whether `compute_retrievability` matched (Layer 11/15).
- Check `session_main_queue` cache — stale + mid-session transition → Layer 7's invalidation may not have fired.
- Check `today_col_day` vs Anki's `last_day_studied` — timezone/rollover bugs (Layer 13).

**Duplicate TT collocations linked to same Anki note (Layer 35 cleanup):**
- Two collocations sharing `anki_note_id` → "phantom direction" mis-classifications in `_merge_directions` (Layer 33). `sync_pull`'s `get_collocation_by_anki_note_id` returns FIRST cid SQLite finds → one gets live updates, other stays stale → Layer 33 sinks the stale one to bottom. Symptom: "card disappears from TT new-head though Anki shows it next."
- Diagnostic: `SELECT anki_note_id, COUNT(*) FROM collocations WHERE anki_note_id IS NOT NULL GROUP BY anki_note_id HAVING COUNT(*) > 1`. Should be 0 (3 pairs merged in Layer 35: ulica, Bog, ura). If more appear, use `app/anki/dedupe_tt_collocations.py` pattern.
- Architectural note: any new code path that creates a second collocation for an existing Anki note MUST also handle dedupe.

**Queue head wrong (Anki=learning, TT=main, both have card):**
- **CHECK FIRST** — top-of-file divergence #1. Almost always one of:
  1. **Cutoff frozen** — refresh `/review` or grade any card.
  2. **Independent grading drift (no sync)** — File→Sync, refresh TT.
- Signature: TT's `anki_due` is much older than Anki's `cards.due`.

**User-buried cards keep coming back in TT (Layer 35):**
- Anki has card at `queue=-2`, TT shows `'review'` or `'new'`. Causes:
  1. Sync hasn't run → expected.
  2. **Expected after rollover.** Both -2 and -3 map to `'sched'`; both released by rollover. NOT a bug unless before rollover or you explicitly used `POST /api/srs/bury`.
  3. `_bury_kind_from_queue` wasn't called for this write path. Audit all 5 DirectionState sites in `sync_pull`.
  4. **Rollover + true user-bury.** `_bury_kind_from_queue` can't distinguish user-bury from sibling-bury (both = -2). After rollover Anki releases; only way to keep TT-buried is `POST /api/srs/bury` (writes `'user'` directly). Check `BURY_TRACE` for `buried_to_released_writes`.

**TT shows 140-ish stuck `state='buried' bury_kind='user'` rows (2026-05-16 incident):**
- See `docs/bury-kind-investigation-2026-05-16.md`. Short: Layer 35 pessimistic backfill + 2 latent bugs (now fixed) locked the cohort. Next sync releases via state-mismatch.
- Diagnostic: `SELECT bury_kind, state, COUNT(*) FROM collocation_directions GROUP BY bury_kind, state`. Non-trivial `'user'` count AND no `'sched'` rows anywhere = locked cohort. Check `BURY_TRACE` `buried_to_released_writes` after next sync.

**Queue head wrong (new card placement):**
- Verify intersperser ratio `(R_remaining + 1) / (N_quota + 1)`. NO session-start override (Layer 14).
- Sibling-bury: gather `new_quota * 4`, bury siblings, cap at `new_quota` post-bury.
- **Sort key (Layer 25)**: `get_new_items` ORDER BY = `d.anki_due DESC NULLS FIRST, c.created_at DESC, d.anki_card_id ASC, c.id ASC`. **Requires Anki deck setting**: New card gather order = "Descending position".
- **Cross-direction gather + bury + Template sort (Layer 28)**: per-direction isn't enough. Anki gathers BOTH ords in one pass and buries second-seen → higher-due wins (`rslib/.../queue/builder/gathering.rs:157-169`). Then `sort_new` stably re-sorts by `ord`. TT pipeline: `_merge_directions` (gather), `_bury_siblings_in_queue` (keep first-seen), `get_review_queue` (stable sort by ord). If TT new-head disagrees with Anki:
  1. **Cache check first.** `clear_session_main_queue` and refetch.
  2. `_merge_directions` output sorted by `(anki_due DESC NULLS FIRST, ord ASC, anki_card_id ASC, row_id ASC)`?
  3. After bury: each collocation_id once, on the higher-anki_due direction (or ord=0 on ties).
  4. After stable sort by ord: all surviving rec before surviving prod, gather order preserved per group.
  5. **Don't re-introduce per-direction-only sorts in `get_new_items`.** Layer 25's ORDER BY is necessary input ordering, not sufficient alone — `_merge_directions` re-sorts the combined pool.

**Queue head wrong (both apps learning, different card):**
- **CHECK FIRST** — top-of-file divergence #2.
- Resolution: sync. Each card converges to later grader's `due_at` per rule 6 / Layer 17.
- **Do not** chase this in queue-assembly code — both sorts work correctly; inputs differ.

**Queue head wrong (learning card re-appears immediately after grading):**
- Anki's "collapse" (`rslib/.../queue/learning.rs:94-113`) shifts a just-graded card past next-soonest pending when main is empty. TT mirrors in `get_review_queue` by swapping `pending_learning[0]` and `[1]` when head's `last_review == cutoff`. If you change queue assembly, re-verify the collapse.

**`left`/`total_remaining` mismatch:**
- `_direction_differs` must compare `left` (Layer 17). If still wrong:
  - `sync_pull`: dirty_fsrs + `_anki_step_ahead` (Layer 18) takes Anki's `left` when ahead.
  - `sync_push`: `OfflineWriter.get_current_card_state` + skip-when-Anki-ahead (Layer 19).
- Push doesn't currently write `reps`/`lapses` — open issue.

**Sync silently skipped a card:**
- TT row missing for Anki note. `sync_pull` doesn't create new TT rows; only `import_seed` does. Run `uv run python -m app.anki.import_seed --deck "0. Slovene"`.

## Diagnostic commands

Snapshot first so analysis doesn't race the live DBs:

```bash
cp "$HOME/Library/Application Support/Anki2/Will/collection.anki2" /tmp/anki_inspect.db
cp "$HOME/Library/Application Support/Anki2/Will/collection.anki2-shm" /tmp/anki_inspect.db-shm 2>/dev/null
cp "$HOME/Library/Application Support/Anki2/Will/collection.anki2-wal" /tmp/anki_inspect.db-wal 2>/dev/null
sqlite3 /tmp/anki_inspect.db "PRAGMA wal_checkpoint(TRUNCATE);" >/dev/null
cp backend/tunatale.db /tmp/tt_inspect.db
```

**Live TT badges + queue head:**
```bash
curl -s http://localhost:8000/api/srs/queue-stats | python3 -m json.tool
curl -s http://localhost:8000/api/srs/review-queue | python3 -c "
import json, sys
d = json.load(sys.stdin)
for i, c in enumerate(d.get('queue', [])[:10]):
    print(f'  {i:2d} {c.get(\"text\",\"\")[:30]:<30s} state={c.get(\"state\")} due_at={c.get(\"due_at\",\"\")[:25]}')"
```

**Compare introduced-today (TT vs Anki revlog):**
```bash
cd backend && uv run python << 'PY'
import sys, sqlite3, datetime; sys.path.insert(0, '.')
from app.srs.database import SRSDatabase
from app.srs.queue_stats import _register_unicase
from app.config import settings

today = datetime.date.today()
local = datetime.datetime.now().astimezone().tzinfo
today_4am = datetime.datetime.combine(today, datetime.time(4), tzinfo=local).astimezone(datetime.UTC)
db = SRSDatabase(settings.database_url.removeprefix("sqlite:///"))
print(f"TT count_new_introduced_today = {db.count_new_introduced_today(today)}")

ac = sqlite3.connect("file:/tmp/anki_inspect.db?mode=ro", uri=True); _register_unicase(ac)
ms = int(today_4am.timestamp() * 1000)
n = ac.execute("""
    SELECT COUNT(*) FROM (
      SELECT r.cid FROM revlog r JOIN cards c ON c.id=r.cid AND c.did=1
      GROUP BY r.cid HAVING MIN(r.id) >= ?
    )""", (ms,)).fetchone()[0]
print(f"Anki introduced today (revlog) = {n}")
PY
```

**Compare step state (left / reps / lapses) for current learning cards:**
```bash
cd backend && uv run python << 'PY'
import sqlite3, sys; sys.path.insert(0, '.')
from app.srs.queue_stats import _register_unicase
tt = sqlite3.connect("/tmp/tt_inspect.db"); tt.row_factory = sqlite3.Row
ac = sqlite3.connect("file:/tmp/anki_inspect.db?mode=ro", uri=True); _register_unicase(ac)
for r in tt.execute("""SELECT cd.anki_card_id, cd.state, cd.left, cd.prior_state, cd.reps, cd.lapses, c.text
    FROM collocation_directions cd JOIN collocations c ON cd.collocation_id=c.id
    WHERE cd.state IN ('learning','relearning') ORDER BY c.text""").fetchall():
    a = ac.execute("SELECT queue, type, left, reps, lapses FROM cards WHERE id=?", (r['anki_card_id'],)).fetchone()
    flag = "" if a and (r['left'] == a[2] and r['reps'] == a[3] and r['lapses'] == a[4]) else "  <-- DIFFERS"
    print(f"{r['text']:<14s} TT(l={r['left']!s:<6s} prior={r['prior_state']!s:<10s} r={r['reps']} lap={r['lapses']}) Anki={a}{flag}")
PY
```

**Compare R values for two suspect cards:**
```bash
cd backend && uv run python << 'PY'
import sys, sqlite3, json, datetime; sys.path.insert(0, '.')
from app.srs.database import SRSDatabase
from app.srs.fsrs import compute_retrievability
from app.models.srs_item import Direction
from app.config import settings

CARDS = ('drevo', 'svet')  # ← edit
db = SRSDatabase(settings.database_url.removeprefix("sqlite:///"))
today, now = datetime.date.today(), datetime.datetime.now(datetime.UTC)
for name in CARDS:
    item = db.get_collocation(name)
    for d in (Direction.RECOGNITION, Direction.PRODUCTION):
        ds = item.directions.get(d) if item else None
        if ds and ds.state.value == 'review' and ds.due_date <= today:
            print(f"TT  {name:<10s} {d.value:<11s} stab={ds.stability:.4f} lr={ds.last_review} R={compute_retrievability(ds, today, now=now):.4f}")
conn = sqlite3.connect("file:/tmp/anki_inspect.db?mode=ro", uri=True)
for name in CARDS:
    rows = conn.execute("SELECT c.id, c.ord, c.due, c.ivl, c.data FROM cards c JOIN notes n ON c.nid=n.id WHERE n.flds LIKE ? AND c.queue=2", (f'{name}%',)).fetchall()
    for r in rows:
        data = json.loads(r[4]) if r[4] else {}
        s, lrt, decay = data.get('s'), data.get('lrt'), data.get('decay', 0.5)
        if s:
            elapsed = (now.timestamp() - lrt) if lrt else (r[2] - (r[2] - r[3])) * 86400
            R = (1 + 19/81 * elapsed/86400 / s) ** -decay
            print(f"Anki {name:<10s} ord={r[1]} cid={r[0]} s={s:.4f} lrt={'Y' if lrt else 'N'} R={R:.4f}")
PY
```

**Force fresh queue build (clears `session_main_queue` cache):**
```bash
cd backend && uv run python -c "
import sys; sys.path.insert(0,'.')
from app.srs.database import SRSDatabase
from app.srs.queue_stats import clear_session_main_queue
from app.config import settings
clear_session_main_queue(SRSDatabase(settings.database_url.removeprefix('sqlite:///')))"
```

## When to escalate to Path 2

Current model — TT reconstructs from TT state — has held through ~22 fixes. If a new divergence:
- Not-yet-mirrored R formula / queue gather branch → patch.
- Input quality (sync didn't carry a field) → patch.
- Fundamental algorithmic difference where mirroring complexity keeps growing → **Path 2**.

**Path 2** = at sync time, execute Anki's `review_order_sql` and persist the card-id sequence as TT's "today's anchor queue." Between syncs, TT serves from snapshot filtered by "graded since sync." Dissolves the entire "mirror Anki's algorithm with all branches" class. ~2-3 hour refactor. Most freeze / intersperser / R-asc reconstruction code goes away. Only when the leak count won't stop.

## Source references

`anki-source-expert` subagent reads `/tmp/anki-source/`. Key files:
- `rslib/.../scheduler/queue/builder/mod.rs` — `learn_count`, `current_learning_cutoff`, assembly
- `rslib/.../scheduler/queue/learning.rs` — intraday-now vs intraday-ahead, `requeue_learning_entry` collapse, `update_learning_cutoff_and_count`
- `rslib/.../scheduler/queue/mod.rs:149-157` — serve order: `intraday_now → main → intraday_ahead`
- `rslib/.../scheduler/queue/builder/intersperser.rs` — ratio `(one_len+1)/(two_len+1)`
- `rslib/.../storage/sqlite.rs:312-364` — `extract_fsrs_retrievability` (two branches)
- `rslib/.../scheduler/timing.rs:27-81` — `sched_timing_today_v2_new`
- `rslib/.../scheduler/answering/mod.rs:632-648` — `get_fuzz_seed_for_id_and_reps` = `card.id + card.reps`

Ask the subagent when in doubt: cites file:line, pairs Anki's behavior with TT's parallel code path.

## Cross-references

- `.claude/rules/anki-sync.md` — USN, safety envelope, schema-change workflow.
- `docs/anki-parity-layers.md` — full layer history.
