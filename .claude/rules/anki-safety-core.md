# Anki Safety Core

Always-loaded hard invariants. The detailed rules (`anki-sync.md`, `anki-queue-parity.md`, `anki-oracle-harness.md`, `testing.md`, `frontend-coverage-gate.md`) are path-scoped — they auto-load when you read files they cover. For Anki/SRS work that starts from conversation alone (e.g. a divergence report), read the relevant rule file FIRST.

- The user's Anki collection is production data. Never `sqlite3.connect()` on `collection.anki2` — always `app.plugins.anki_sync.safety.safe_open(mode="rw"|"ro")` (lock probe, SHA256 backup, integrity check).
- Every Anki mutation: `usn = -1` and `mod = now_ts` on touched rows; bump `col.mod` after batch writes; NEVER set `col.usn = -1`. Deletes go through `graves` rows, never bare `DELETE` — recipe in `.claude/rules/anki-sync.md`.
- `backend/app/**` must never `import anki` (Anki = reference, not runtime dependency). The collection is read at sync time only — never on a live request path.
- Exactly one sync sequence: `run_full_sync` in `app/plugins/anki_sync/sync.py`. Never add a second HTTP sync path or inline a phase subset at a call site (the b0a4b8a regression class).
- Any TT↔Anki divergence report (queue head, badge counts): read `.claude/rules/anki-queue-parity.md` before proposing a fix — the three most common causes are benign and documented at its top.
