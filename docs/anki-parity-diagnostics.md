# Anki Parity — Diagnostics & Reference

Pull-up reference for `.claude/rules/anki-queue-parity.md`. Split out of the rule file so the always-loaded guidance stays lean; open this when you're actively hunting a divergence. Nothing here is new — it's the bash/python snippets, the source file:line map, and the load-bearing-helper table that the rule file used to carry inline.

## Snapshot the DBs first

So analysis doesn't race the live DBs:

```bash
cp "$HOME/Library/Application Support/Anki2/Will/collection.anki2" /tmp/anki_inspect.db
cp "$HOME/Library/Application Support/Anki2/Will/collection.anki2-shm" /tmp/anki_inspect.db-shm 2>/dev/null
cp "$HOME/Library/Application Support/Anki2/Will/collection.anki2-wal" /tmp/anki_inspect.db-wal 2>/dev/null
sqlite3 /tmp/anki_inspect.db "PRAGMA wal_checkpoint(TRUNCATE);" >/dev/null
cp backend/tunatale.db /tmp/tt_inspect.db
```

## Benign-divergence diagnostics

### Cutoff frozen (divergence #1)

```bash
sqlite3 /tmp/tt_inspect.db "SELECT value FROM anki_state_cache WHERE key='learning_cutoff';"
sqlite3 /tmp/tt_inspect.db "SELECT c.text, cd.direction, cd.due_at FROM collocation_directions cd JOIN collocations c ON cd.collocation_id=c.id WHERE cd.state IN ('learning','relearning') ORDER BY cd.due_at LIMIT 5;"
```
If the earliest learning `due_at` is just past the cutoff, you've found it.

### Grading drift (divergence #2)

```bash
sqlite3 /tmp/tt_inspect.db "SELECT c.text || ' ' || cd.direction, cd.anki_card_id, strftime('%s', cd.last_review) as tt_grade, cast(strftime('%s', cd.due_at) as int) as tt_due FROM collocation_directions cd JOIN collocations c ON cd.collocation_id=c.id WHERE cd.state IN ('learning','relearning') ORDER BY cd.due_at LIMIT 8;" | while IFS='|' read card cid tt_grade tt_due; do
  anki=$(sqlite3 /tmp/anki_inspect.db "SELECT mod || '|' || due FROM cards WHERE id=$cid")
  echo "  $card  TT(grade=$tt_grade due=$tt_due) Anki=$anki"
done
```
If per-card deltas are O(seconds) and the step (due − grade) matches between apps, it's drift, not a bug.

## Live TT badges + queue head

```bash
curl -s http://localhost:8000/api/srs/queue-stats | python3 -m json.tool
curl -s http://localhost:8000/api/srs/review-queue | python3 -c "
import json, sys
d = json.load(sys.stdin)
for i, c in enumerate(d.get('queue', [])[:10]):
    print(f'  {i:2d} {c.get(\"text\",\"\")[:30]:<30s} state={c.get(\"state\")} due_at={c.get(\"due_at\",\"\")[:25]}')"
```

## Compare introduced-today (TT vs Anki revlog)

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

## Compare step state (left / reps / lapses) for current learning cards

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

## Compare R values for two suspect cards

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

## Force fresh queue build (clears `session_main_queue` cache)

```bash
cd backend && uv run python -c "
import sys; sys.path.insert(0,'.')
from app.srs.database import SRSDatabase
from app.srs.queue_stats import clear_session_main_queue
from app.config import settings
clear_session_main_queue(SRSDatabase(settings.database_url.removeprefix('sqlite:///')))"
```

## Reproduce queue head against the Anki binary (rule 13)

`/tmp/anki-source/` is a shallow clone of `main`, not a release tag — it can be ahead of or behind the user's Anki. When TT mirrors source and still diverges, reproduce against the binary. Anki must be CLOSED (Collection wants exclusive write access).

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

## Soak compare-shadow classifier (TT-side only, no Anki needed) — ⚠️ COMPARE MODE ONLY

**Check the mode first:** `sqlite3 backend/tunatale.db "SELECT value FROM anki_state_cache WHERE key='event_sync_pull';"`

This classifier is valid **only when `event_sync_pull='compare'`**. The live flag flipped to **`new`** on 2026-06-02, and in `new` mode `_write_compare_shadow` is gated off — the `*_replayed` columns are **frozen** at the last compare-era sync, so this query reports **false positives** (every direction graded since the flip shows as "diverging"). For the new-mode soak signal, jump to "New-mode soak signal" below.

In compare mode: `stability_diverge=0` and `difficulty_only_diverge=0` ⇒ healthy. Full trail: memory `project_stage3b_soak_finding_difficulty_replay`.

```bash
sqlite3 backend/tunatale.db "
SELECT 'stability_diverge' k, COUNT(*) FROM collocation_directions WHERE stability_replayed IS NOT NULL AND ABS(stability_replayed-stability)>1e-4
UNION ALL SELECT 'difficulty_only_diverge', COUNT(*) FROM collocation_directions WHERE fsrs_difficulty_replayed IS NOT NULL AND ABS(fsrs_difficulty_replayed-fsrs_difficulty)>1e-4 AND ABS(COALESCE(stability_replayed,stability)-stability)<=1e-4;"
```

## New-mode soak signal (`event_sync_pull='new'`)

In `new` mode the authoritative write is take-Anki-verbatim, so the signal is **`recompute_divergences ≈ 0` per sync** (a non-zero count flags a genuine Anki recompute event — Optimize / FSRS-param / retention / FSRS-toggle / restore — the forward-step replay can't reproduce). It is NOT persisted to a DB column. Read it from:

```bash
# Durable per-sync soak log (each non-dry CLI sync appends a SYNC_SOAK heartbeat
# + one RECOMPUTE_DIVERGENCE line per divergence). Expect the grep to be empty.
tail -20 ~/.tunatale/logs/sync.log
grep RECOMPUTE_DIVERGENCE ~/.tunatale/logs/sync.log
```

**Strongest check — read-only TT-authoritative vs Anki `cards.data`** (no sync, safe while Anki is open; take-Anki-verbatim ⇒ bit-exact post-sync). Verified 1345/1345 on 2026-06-02:

```bash
cd backend && uv run python - <<'PY'
import sqlite3, json, os
tt = sqlite3.connect("file:tunatale.db?mode=ro", uri=True)
ak = sqlite3.connect(f"file:{os.path.expanduser('~/Library/Application Support/Anki2/Will/collection.anki2')}?mode=ro", uri=True)
anki = {}
for cid, data in ak.execute("SELECT id, data FROM cards"):
    try:
        j = json.loads(data) if data else {}
        anki[cid] = (j.get("s"), j.get("d"))
    except Exception:
        anki[cid] = (None, None)
sdiv = ddiv = both = 0
for akid, s, d in tt.execute("SELECT anki_card_id, stability, fsrs_difficulty FROM collocation_directions WHERE anki_card_id IS NOT NULL"):
    a_s, a_d = anki.get(akid, (None, None))
    if a_s is None or a_d is None:
        continue
    both += 1
    if abs((s or 0) - a_s) > 1e-2: sdiv += 1
    if abs((d or 0) - a_d) > 1e-2: ddiv += 1
print(f"compared={both} stability_diverge={sdiv} difficulty_diverge={ddiv}")  # both 0 ⇒ healthy
PY
```

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

## Load-bearing helpers (Pre-Layer checklist Step 2)

If your fix is going to compute X, and one of these helpers already computes something X-shaped, the fix should extend the helper, not reimplement it elsewhere.

| Helper | Path | Covers |
|---|---|---|
| `_elapsed_days_for_fsrs` | `app/srs/fsrs.py` | Dual-branch fractional-vs-integer-day elapsed since `last_review`. Used by both R formula and FSRS scheduling. |
| `compute_retrievability` | `app/srs/fsrs.py` | R formula (forgetting curve + null-state → desired_retention). |
| `_next_stability_recall` / `_next_stability_lapse` / `_stability_short_term` | `app/srs/fsrs.py` | FSRS stability update for recall / lapse / same-day. Lapse path has fsrs-rs's ceiling (Layer 42). **Never call these from a grade path directly** — go through `_next_stability_for_grade`, which selects the right one by `delta_t` (Layer 62). |
| `_next_stability_for_grade` / `_clamp_stability` | `app/srs/fsrs.py` | TT's fsrs-rs `step()`-equivalent: selects short-term (Δt=0) vs recall/lapse (Δt>0) AND clamps to `[S_MIN, S_MAX]` (Layers 57, 62, 63). Used by the REVIEW-passing, learning-step, and graduation paths. |
| `_next_difficulty` | `app/srs/fsrs.py` | FSRS difficulty update with linear damping + reversion. |
| `_schedule_with_steps` | `app/srs/fsrs.py` | LEARNING/RELEARNING step transitions + Layer 41 single-step Hard delay. |
| `_pack_left` / `_parse_left` | `app/srs/fsrs.py` | Anki's `cards.left = today_left*1000 + total_remaining` encoding. |
| `_merge_by_retrievability_ascending` | `app/api/srs.py` | R-asc queue sort + FNV tiebreaker (Layer 37). |
| `_merge_directions` | `app/api/srs.py` | Cross-direction gather + sibling-bury + Template stable-sort (Layer 28). |
| `_fnv1a_64_i64` | `app/api/srs.py` | Anki's tiebreaker hash; required identical port. |
| `_pull_merge_direction` | `app/anki/sync.py` | Per-card sync_pull merge (post Phase 1.3 extraction). |
| `_direction_differs` | `app/anki/sync.py` | Field-by-field diff for sync write-back; must include `left`, `due_at`, `prior_state`, `bury_kind`, `anki_card_mod` (rule 6, Layer 17, Layer 37). |
| `_resolve_prior_state` / `_grade_prior_state` | `app/anki/sync.py` + `app/srs/fsrs.py` | Sticky-NEW `prior_state` (Layers 20-22). |
| `_resolve_introduced_at` | `app/anki/sync.py` | One-shot intro stamp (Layer 26). |
| `_anki_step_ahead` | `app/anki/sync.py` | "Anki's `left` is further along than TT's" check (Layers 18, 19). |
| `_bury_kind_from_queue` | `app/anki/sync.py` | `queue=-2/-3 → 'sched'` mapping (Layer 35, Layer 39). |
| `_queue_to_state` | `app/anki/sync.py` | `cards.queue → SRSState`, trusts queue not reps (Layer 30). |
| `_read_config_value_from_deck_config_table` | `app/srs/queue_stats.py` | Unified deck-config protobuf/legacy-JSON reader (Phase 1.1). Use this for any new deck-config field. |
| `count_new_available_collocations` | `app/srs/database.py` | Bury-aware new-badge count: NEW directions minus collocations with a graded-today / learning / review-due-today sibling (`bury_new`, Layer 64). Mirror image of `count_review_due_collocations`. `count_new_available` (raw `COUNT(*)`) stays for the overfetch upper bound only. |
| `unbury_if_needed` | `app/srs/database.py` | Daily unbury sweep (Layers 27, 35). |
| `clear_session_main_queue` + `build_and_freeze_main_queue` | `app/srs/queue_stats.py` + `app/api/srs.py` | Session-queue cache management (Layers 4, 7, 29). |
