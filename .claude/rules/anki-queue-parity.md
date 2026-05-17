# TT ↔ Anki Queue Parity

Required reading before changing anything in `backend/app/api/srs.py`, `backend/app/srs/fsrs.py`, `backend/app/srs/queue_stats.py`, or `backend/app/anki/sync.py`. The full layer-by-layer history of how the current behavior was reached lives at `docs/anki-parity-layers.md` — open it only when you suspect "have we hit this exact thing before?"

## What "parity" means

The user grades the same deck in both Anki and TunaTale. Between syncs, both apps run independently; the next-served card and the three badge counts (`new` / `learning` / `review`) must stay close enough that switching apps doesn't feel discontinuous. **Sync is the alignment moment.** Drift between syncs is bounded and acceptable.

Anki is the reference implementation. TunaTale mirrors Anki's algorithms — but **reads `collection.anki2` only at sync time**, never on the live request path. A request handler that consults Anki's collection is a regression unless it's `sync_pull`/`sync_push` itself.

## Architectural principles (don't undo these)

1. **Anki is reference, not runtime dependency.** Every badge endpoint and every queue-build path reconstructs from TT state alone (`collocation_directions` + `anki_state_cache`). If you find yourself opening `collection.anki2` in `app/api/`, you've taken a wrong turn — look for the TT-state-only equivalent (`count_new_introduced_today`, `count_review_due_collocations`, `list_collocations_reviewed_today`, etc.).

2. **Cache invalidation + eager rebuild on sync.** Non-dry-run `sync_pull` *clears AND eagerly rebuilds* `session_main_queue` via `build_and_freeze_main_queue` (Layer 29). The freeze moment is sync time — matching Anki's `requires_study_queue_rebuild` at session-open. New cache keys that depend on Anki-side state must clear here too. **Deploy-time pitfall**: the cache lives in `anki_state_cache` (DB-backed), so it survives backend restarts. After changing queue-assembly logic, an existing cache row will replay the OLD order until the next sync — restart alone does not invalidate it. When debugging a "fix doesn't seem to be working" report, **always run `clear_session_main_queue` first** before concluding the fix is broken — see the diagnostic command lower in this file.

3. **Sibling-bury via `last_review` filter.** Anki's `bury_reviews=true` removes a note from today's pool the moment any sibling is graded. TT mirrors this by excluding collocations where any direction has `last_review` today. Don't write count queries that ignore this — they over-count vs Anki.

4. **Sync rebuilds with current pool.** Both apps gather with current-pool counts at sync time, not session-start pool. The intersperser ratio is `(one_len+1)/(two_len+1)` over natural list lengths — do **not** add a session-start override; that was Layer 9, reverted at Layer 14.

5. **Two-branch R formula.** `extract_fsrs_retrievability` has an lrt branch (sub-day fractional elapsed) and a day-level fallback (integer-day elapsed). TT must mirror both in `compute_retrievability` — flipping a card from one branch to the other changes R-asc ordering.

6. **Sync must merge both directions.** `sync_push` (TT→Anki) must defer to Anki when Anki is ahead (graduated or smaller `total_remaining`). `sync_pull` (Anki→TT) must defer to Anki when Anki is ahead. The `_direction_differs` diff must include `left`, `due_at`, **and** `prior_state` so a self-heal write actually fires.

7. **`prior_state='new'` is sticky.** Set on the introduction event by sync_pull's `_resolve_prior_state` or by the grade endpoint's `_grade_prior_state`. Persists across same-state-class grades and across LEARNING→REVIEW graduation. Released only on REVIEW→RELEARNING (lapse), where revlog `type=1` correctness wins. **Do not** add code that overwrites `prior_state='new'` without checking the new state.

8. **`introduced_at` is a one-shot stamp, NOT a sticky marker (Layer 26).** Written exactly once per direction on the first NEW→non-NEW transition by `fsrs.schedule` (TT-side grade) or `sync_pull._resolve_introduced_at` (Anki-side, from `MIN(revlog.id)`). Never updated after that. `count_new_introduced_today` queries this column, NOT `prior_state` + `last_review`. Don't conflate the two — `prior_state='new'` lives for the entire intro arc and applies to revlog correctness; `introduced_at` is a fixed timestamp that anchors Anki's `newToday` parity.

9. **Daily unbury sweep at queue-build (Layer 27, refined Layer 35).** `db.unbury_if_needed(today)` runs at the top of `/queue-stats`, `/review-queue`, and `sync_pull`. It restores `state='buried' AND bury_kind='sched'` rows to `state='review'` (reps>0) or `state='new'` (reps=0). Tracked via `anki_state_cache['last_unbury_day']`; idempotent within the local day so today's sibling-buries from sync don't get wiped. Mirrors Anki's `unbury_on_day_rollover` (releases both `queue=-3` AND `queue=-2`, see rule 10). **Do NOT** let `state='buried' bury_kind='sched'` rows accumulate — they under-count `count_review_due_collocations`.

10. **`bury_kind` split (Layer 35, corrected 2026-05-16; Layer 39, corrected 2026-05-17).** Anki has two queues for buried cards: `queue=-3` and `queue=-2`. Anki's source claims grade-time sibling-bury writes `queue=-3` and only explicit UI actions (Ctrl-J, browse-bulk-bury) write `queue=-2`. **Anki's binary contradicts this**: running `col.sched.answerCard` against a real collection placed the sibling at `queue=-2` (verified 2026-05-17, per rule 13). Both queues are released by `unbury_on_day_rollover` (`rslib/src/scheduler/bury_and_suspend.rs:44-50` + `sqlwriter.rs:471-476` — `StateKind::Buried` → SQL `c.queue in (-3, -2)`). TT mirrors via `collocation_directions.bury_kind`:
    - `'sched'` → released by daily sweep (Layer 27)
    - `'user'` → sticks across rollover in TT (user-initiated bury from `POST /api/srs/bury`)
    - `NULL` → non-buried row (no kind needed)
    `sync_pull`'s `_bury_kind_from_queue` helper writes the kind from Anki's queue value: **both `queue=-2` and `queue=-3` map to `'sched'`**. Pre-Layer-35 buried rows were backfilled to `'user'` (safe default — pessimistic about losing data). **Do NOT** add an unconditional `UPDATE … WHERE state='buried'` — that's the pre-Layer-35 bug that wiped 18 manually-buried cards on every `/queue-stats` poll.

    **Two follow-up bugs found 2026-05-16:**
    - `_DIR_COLUMNS` in `database.py` did not include `bury_kind` — every `get_collocation_by_*` read returned `bury_kind=None` regardless of the DB value. Fixed: column added to the SELECT list.
    - `_direction_differs` in `sync.py` did not check `bury_kind` — kind-only flips (e.g. migration's pessimistic `'user'` vs sync's correct `'sched'`) were silent no-ops, locking the kind permanently. Fixed: `or local.bury_kind != candidate.bury_kind` added to the diff.

    **BURY_TRACE diagnostic (2026-05-16).** Every sync_pull now emits one `BURY_TRACE` INFO log line per direction where local OR candidate state involves BURIED, plus a summary at the end (`anki_queue_minus2_seen`, `anki_queue_minus3_seen`, `buried_to_released_writes`, `released_to_buried_writes`, `kind_only_flips_written`, `buried_state_match_no_write`). Grep server stderr for `BURY_TRACE` after any sync to reconstruct exactly which queue value Anki returned and whether the diff fired. **`anki_queue_minus2_seen > 0` is no longer suspicious** — Anki's binary writes `queue=-2` for grade-time sibling-bury (per rule 13, trust the binary). The counter is useful for trending but not an anomaly indicator.

11. **`learning_cutoff` has 4 advancement triggers, not 3 (Layer 36).** TT mirrors Anki's `current_learning_cutoff`. Triggers in order of frequency:
    1. **Grade** — `drill_feedback` calls `advance_learning_cutoff(db, now)` (Anki: `update_queues_after_answering_card` → `update_learning_cutoff_and_count`, `rslib/scheduler/queue/mod.rs:217-243`).
    2. **Session-start** — `/review-queue?session_start=1` (frontend `onMount`) advances cutoff to `now`. Anki: queue build at deck open, `builder/mod.rs:224`.
    3. **sync_pull ingest** — advances cutoff to the latest revlog timestamp pulled.
    4. **counts.all_zero auto-bump (Layer 36)** — when `ready_learning` AND `ordered_main` are both empty in `/review-queue`, and any `pending_learning.due_at ≤ now`, advance cutoff to `now` and re-split. Mirrors Anki's `CardQueues::counts()` (`rslib/scheduler/queue/mod.rs:187-196`), which calls `update_learning_cutoff_and_count` whenever `counts.all_zero()` — the "end of session, learning card just ripened, surface it without a grade" path.
    **Stickiness invariant**: if main has items OR ready_learning has items, the cutoff is frozen between grades. The auto-bump is end-of-session only. A learning card that ripens mid-session does NOT preempt the card on screen — matches Anki's "card on screen is sticky" semantic. Don't add a "live `now`" path or per-poll cutoff advance — that breaks stickiness and was rejected in design discussions.

12. **Daily caps are render-only, not serving contracts.** `daily_new_cap` and `daily_review_cap` affect ONLY the badge counters on `/queue-stats`. The queue assembly (`_compute_live_main`, `get_review_queue`) does NOT cap what it serves. Anki also only applies the caps to the deck-list badge, not to the card-at-a-time review flow. See Layer 36 in `docs/anki-parity-layers.md`.

13. **Trust the binary, not the source, when they disagree.** The `/tmp/anki-source/` checkout the `anki-source-expert` subagent reads is a shallow clone of `main` at no specific tag — it can be ahead of or behind the user's actual Anki release. When TT mirrors what source says and still diverges from observed Anki behavior, **reproduce against the running binary** before assuming TT is wrong. Concrete protocol:
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
    Layer 38 (NULL-R sort = `desired_retention`, not NULLs-first) was found this way after source-code reasoning predicted the wrong placement. Anki must be CLOSED for this to work (Collection wants exclusive write access). The source remains the right starting point — it's just not the final word.

14. **NULL R-value sorts at `desired_retention`, not NULLs-first or NULLs-last (Layer 38).** Cards with no FSRS memory_state (`cards.data='{}'`, no `s`/`d` keys) are placed by Anki at the position `desired_retention` occupies in R-asc — between R<dr and R>dr cards, NOT at the SQLite-default NULLs-first head, and NOT at the tail. `compute_retrievability` returns `desired_retention` (default 0.9) instead of None for missing-state cases. The deck's actual `desired_retention` is cached at sync time via `refresh_desired_retention` (proto field 37, NOT field 40 — field 40 is `historical_retention`, a pre-Layer-38 footgun). `_merge_by_retrievability_ascending` calls `resolve_desired_retention()` once and threads the value into every R-key computation.

15. **`anki_card_mod` must be in `_direction_differs` (Layer 37).** The FNV tiebreaker `fnvhash(cards.id, cards.mod)` is appended to every Anki `review_order_sql` variant (`rslib/src/storage/card/mod.rs:897`), so any sort that ties on the primary key falls to mod. Anki bumps `cards.mod` for reasons that don't change FSRS state (server sync mtime resolution, housekeeping, bury actions); if sync_pull's diff doesn't notice the bump, TT's local copy stays stale and the FNV hash drifts. Keep `or local.anki_card_mod != candidate.anki_card_mod` in `_direction_differs` even if it looks like a metadata field — it's an ORDER BY input.

## Divergence playbook

When a "TT and Anki disagree" report comes in, walk this tree. Each leaf points to the mechanism that handles it; verify it's still firing, then look for new edge cases.

**Badge wrong:**
- `new` badge: `db.count_new_introduced_today(today)` filters `introduced_at` within today's UTC range (Layer 26). The column is stamped once per intro arc — by `fsrs.schedule` on the first NEW→non-NEW transition and by `sync_pull._resolve_introduced_at` on first observed Anki revlog. Pre-Layer-26 rows have NULL and don't count (intentional — keeps the count from over-firing on sticky-NEW review cards). If "I just introduced X and the badge didn't decrement" → check that `introduced_at` is set: `SELECT introduced_at FROM collocation_directions WHERE collocation_id=(SELECT id FROM collocations WHERE text=?)`. Empty means the grade path didn't stamp it — that's the regression. The older `prior_state='new' AND last_review today` filter is gone; do not reintroduce it (it over-counted sticky-NEW cards reviewed today whose actual intro was on a prior day).
- `learning` badge: `db.count_learning()` — pure TT state (`state IN ('learning','relearning')`). Drifts when the user grades in Anki and TT hasn't synced. Expected drift; not a bug. **Caveat (Layer 22+)**: `promote_to_learning` from the listen-first UI sets `state='learning'` without `left`/`due_at`, so TT counts it while Anki keeps the card as `queue=0` (new). Promoted-but-not-graded cards are a documented TT-only addition to the learning count.
- `review` badge: `min(db.count_review_due_collocations(today), max(0, daily_review_cap − reviews_today))` — due collocations, capped by Anki's `reviews_per_day` deck option, depleted by `db.count_reviews_completed_today(today)`. Drifts on Anki-side grades until sync. **Layer 27**: stale `state='buried'` rows (e.g., sync_pull pulled Anki's queue=-2 yesterday and TT never unburied) under-count this badge. **Layer 36**: the raw due count is capped at `min(due_raw, max(0, daily_review_cap - reviews_today))` — mirrors Anki's `reviews_per_day` deck option. If TT's review badge is consistently lower than raw `count_review_due_collocations`, check `anki_state_cache['daily_review_cap']` and `db.count_reviews_completed_today(today)`. The daily unbury sweep (`db.unbury_if_needed`) fires on every `/queue-stats` and `/review-queue` request — if reviews look low, check `SELECT COUNT(*) FROM collocation_directions WHERE state='buried'` against the count for siblings of today's grades. Should be close.

**Queue head wrong (review-state card):**
- Compare R values for the divergent cards (snippet below). If TT and Anki produce different R, check which branch of `extract_fsrs_retrievability` Anki used (presence/absence of `cards.data.lrt`) and whether `compute_retrievability` picked the matching branch (Layer 11/15).
- Check `session_main_queue` cache — if it's stale and the divergent card transitioned mid-session, Layer 7's invalidation may not have fired.
- Check `today_col_day` (`_compute_today_col_day`) vs Anki's `last_day_studied` — timezone/rollover bugs land here (Layer 13).

**Duplicate TT collocations linked to same Anki note (Layer 35 cleanup):**
- Two collocation rows with the same `anki_note_id` cause "phantom direction" mis-classifications in `_merge_directions` (Layer 33). `sync_pull`'s `get_collocation_by_anki_note_id` returns the FIRST cid SQLite finds, so one collocation gets the live `anki_due` updates and the other stays stale → Layer 33 sinks the stale one to the queue bottom. The visible symptom is "card disappears from TT new-card head even though Anki shows it next."
- Diagnostic: `SELECT anki_note_id, COUNT(*) FROM collocations WHERE anki_note_id IS NOT NULL GROUP BY anki_note_id HAVING COUNT(*) > 1`. Should be 0 — three pairs were merged in Layer 35 (ulica, Bog, ura). If more appear, the LingQ-import-bug or some other path is creating dupes; use the dedupe pattern from `app/anki/dedupe_tt_collocations.py`.
- Architectural note: `sync_pull` looks up TT rows by `anki_note_id`. If you ever add a code path that creates a second collocation for an existing Anki note (e.g. a homonym import script), you MUST also handle the dedupe — otherwise sync_pull silently picks the wrong one.

**Queue head wrong (Anki shows learning card, TT shows main, both have it):**
- This is almost always one of two things — **NOT a bug**:
  1. **Cutoff frozen at last grade (Layer 13/36).** TT's `learning_cutoff` only advances on grade/session-start/sync/auto-bump. If the learning card's `due_at` is just after the cutoff (e.g. user graded, learning step expired 2 seconds later, user stared at the next card for a minute), TT keeps it in `pending_learning` → tail. Anki, in deck mode, may have advanced its own cutoff via the `counts.all_zero()` auto-bump trigger if its main was empty. Resolution: refresh `/review` (calls `?session_start=1` → cutoff = now) or grade any card.
  2. **Independent grading drift (no sync).** The user has graded the card in BOTH apps without syncing in between. Each app advances its own FSRS with its own fuzz seed, producing slightly-different next-step times. TT's `anki_due` will be stale (still showing the pre-divergence value). Resolution: Anki → File → Sync, then refresh TT.
- Symptom signature: TT's `anki_due` for the card is many hours/days older than Anki's `cards.due`. Diagnostic: compare `cards.due` for the cid against TT's `anki_due` for the matching direction.

**User-buried cards keep coming back in TT (Layer 35):**
- Anki has the card at `queue=-2` (user-buried or sibling-buried) AT THE MOMENT OF GRADE/BURY. TT shows `state='review'` or `state='new'`.
- Root cause options:
  1. Sync hasn't run since the user buried in Anki → expected drift. Run sync_pull.
  2. **Expected after rollover.** `_bury_kind_from_queue` maps both `queue=-2` and `queue=-3` to `'sched'`. Anki releases both at rollover (`unbury_on_day_rollover` → SQL `c.queue in (-3, -2)`), and TT's daily sweep releases `bury_kind='sched'` on every `/queue-stats` and `/review-queue` poll. This is NOT a bug — it's parity. If the card reappears before rollover or you explicitly user-buried via `POST /api/srs/bury`, proceed to next option.
  3. sync_pull's `_bury_kind_from_queue` wasn't called for this card's write path. Audit every DirectionState construction in `sync.py` — five sites in `sync_pull`; all must pass `bury_kind=_bury_kind_from_queue(card_rec.queue)`.
  4. **Anki rolled over, and the card was a true user-bury.** `_bury_kind_from_queue` has no way to distinguish user-buries from sibling-buries (both produce `queue=-2` in Anki). After rollover, Anki releases it (expected); the only way to keep it buried in TT is via `POST /api/srs/bury` (writes `bury_kind='user'` directly, bypassing Anki). Check `BURY_TRACE` for `buried_to_released_writes` after the next sync.

**TT shows 140-ish stuck `state='buried' bury_kind='user'` rows that won't release (2026-05-16 incident):**
- See `docs/bury-kind-investigation-2026-05-16.md` for the full story. Short version: Layer 35 migration's pessimistic `bury_kind='user'` backfill, combined with two latent bugs (read-path missing column + diff blind to bury_kind), locked the cohort. Bugs fixed 2026-05-16. Next sync should release the cohort via state-mismatch (Anki has them released, TT has them BURIED).
- Diagnostic: `SELECT bury_kind, state, COUNT(*) FROM collocation_directions GROUP BY bury_kind, state`. If `'user'` count is non-trivial AND no `'sched'` rows exist anywhere (suspicious — `'sched'` rows should exist transiently between sibling-bury and rollover), the cohort is locked. After next sync, check `BURY_TRACE` summary — `buried_to_released_writes` should equal the previously-stuck count.

**Queue head wrong (new card placement):**
- Verify the intersperser ratio against natural list lengths: `(R_remaining + 1) / (N_quota + 1)`. Do NOT add a session-start counts override — Layer 14 reverted that.
- Check sibling-bury: gather should pull `new_quota * 4` then proactively bury siblings, capping the post-bury list at `new_quota`. If you serve fewer than `new_quota` new cards, the over-fetch is missing.
- **Sort key (Layer 25)**: `get_new_items` ORDER BY is `d.anki_due DESC NULLS FIRST, c.created_at DESC, d.anki_card_id ASC, c.id ASC`. Per direction. **Requires Anki deck setting**: New card gather order = "Descending position".
- **Cross-direction gather + bury + Template sort (Layer 28)**: per-direction ordering isn't enough — Anki gathers BOTH ords in one pass and buries the second-seen sibling, so the *higher-due* sibling wins (`rslib/.../queue/builder/gathering.rs:157-169`). Then `sort_new` stably re-sorts by `ord` (`rslib/.../queue/builder/sorting.rs:14-36`). TT's `_merge_directions` mirrors the gather order; `_bury_siblings_in_queue` keeps the first-seen survivor; `get_review_queue` then re-sorts the post-bury new pool by `0 if recognition else 1`. If TT's new-bucket head disagrees with Anki, walk this pipeline in order:
  1. **Cache check first.** Stale `session_main_queue` cache can mask a working fix. Run `clear_session_main_queue` (diagnostic below) and refetch before anything else.
  2. `_merge_directions` output: should be sorted by `(anki_due DESC NULLS FIRST, ord ASC, anki_card_id ASC, row_id ASC)`. If not, the merge regressed.
  3. After `_bury_siblings_in_queue`: each collocation_id appears once, on the direction whose anki_due was higher (or ord=0 on ties).
  4. After the final stable sort by ord: all surviving recognition cards come before surviving production cards, gather order preserved within each ord group.
  5. **Don't re-introduce per-direction-only sorts in `get_new_items`.** Layer 25 tried that and it looked right in isolation, but the gather+bury+template pipeline requires the interleaved merge to land siblings in the correct relative position. The Layer-25 ORDER BY in `get_new_items` is fine (and necessary as the input ordering) but is NOT sufficient on its own — `_merge_directions` re-sorts the combined pool, and that re-sort is the load-bearing step.

**Queue head wrong (learning card re-appears immediately after grading):**
- Anki's "collapse" (`rslib/scheduler/queue/learning.rs:94-113`) shifts a just-graded learning card past the next-soonest pending card when main is empty. TT mirrors this in `get_review_queue` by swapping `pending_learning[0]` and `[1]` when the head's `last_review == cutoff`. If you change the queue assembly, re-verify the collapse fires.

**`left` / `total_remaining` mismatch between apps:**
- Sync didn't carry the value. `_direction_differs` must compare `left` (Layer 17). If it does and the card still has wrong `left`:
  - Check `sync_pull`: the dirty_fsrs branch with `_anki_step_ahead` (Layer 18) takes Anki's `left` when Anki is further along. If both apps were ahead in different ways, the timestamp comparison wins (line 801).
  - Check `sync_push`: `OfflineWriter.get_current_card_state` + the skip-when-Anki-ahead guard (Layer 19) must fire to avoid overwriting Anki's progress.
- Push doesn't currently write `reps`/`lapses` to Anki. If `reps` is the divergent field, that's the open issue — Anki's reps doesn't advance via `set_learning_state`.

**Sync silently skipped a card:**
- TT row missing for a note Anki has. `sync_pull` doesn't create new TT rows from Anki — only `import_seed` does. This is the open Layer 22 (no fix yet); manual `uv run python -m app.anki.import_seed --deck "0. Slovene"` populates the missing rows.

## Diagnostic commands

Always snapshot first so the analysis doesn't race the live DBs:

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

The current model — TT reconstructs Anki's queue from TT state — has held through ~22 divergence fixes. If a new divergence:
- Comes from a not-yet-mirrored branch of Anki's R formula or queue gather → patch (add another branch).
- Comes from input quality (sync didn't carry a needed field) → patch (add to `sync_pull`/`sync_push`).
- Comes from a fundamental algorithmic difference where mirroring keeps growing in complexity → consider **Path 2**.

**Path 2** = at sync time (while `collection.anki2` is open), execute Anki's actual `review_order_sql` and persist the resulting card-id sequence as TT's "today's anchor queue." Between syncs, TT serves from the snapshot, filtered by "graded since sync." Dissolves the entire class of "mirror Anki's algorithm with all branches" bugs. Estimated cost: 2-3 hour refactor. Most of the freeze / intersperser / R-asc reconstruction code goes away. Recommended only when the leak count won't stop.

## Source references

The `anki-source-expert` subagent has Anki's source at `/tmp/anki-source/`. Key files:
- `rslib/src/scheduler/queue/builder/mod.rs` — `learn_count`, `current_learning_cutoff`, queue assembly
- `rslib/src/scheduler/queue/learning.rs` — intraday-now vs intraday-ahead, `requeue_learning_entry` collapse, `update_learning_cutoff_and_count`
- `rslib/src/scheduler/queue/mod.rs:149-157` — serve order: `intraday_now → main → intraday_ahead`
- `rslib/src/scheduler/queue/builder/intersperser.rs` — ratio `(one_len+1)/(two_len+1)`
- `rslib/src/storage/sqlite.rs:312-364` — `extract_fsrs_retrievability` (two branches)
- `rslib/src/scheduler/timing.rs:27-81` — `sched_timing_today_v2_new`
- `rslib/src/scheduler/answering/mod.rs:632-648` — `get_fuzz_seed_for_id_and_reps` = `card.id + card.reps`

When in doubt, ask the subagent: it cites file:line and pairs Anki's behavior with TT's parallel code path.

## Cross-references

- `.claude/rules/anki-sync.md` — USN, safety envelope, schema-change workflow. Required for anything writing to `collection.anki2`.
- `docs/anki-parity-layers.md` — the full Layers 1-22 history. Reference, not auto-loaded.
