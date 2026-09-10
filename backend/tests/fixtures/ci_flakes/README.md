# CI flake-report fixtures (tunatale-1l26.10)

Real GitHub Actions data for `scripts/report_ci_flakes.py`, captured 2026-09-10
from wdhaines/tunatale. Nothing here is synthetic. Synthetic cases live inline
in `tests/test_report_ci_flakes.py`, where a reader can see they are made up.

- `runs.ndjson`: one run object per line, trimmed to the fields the script
  reads. Seven runs: six inside 2026-09-01..2026-09-10T12Z, plus 33442906154
  (2026-08-31), which is OUTSIDE that window and must never be reported.
- `jobs/<run>_<attempt>.json`: `GET /actions/runs/<run>/attempts/<n>/jobs`,
  trimmed to id/name/conclusion/status/head_sha/steps.
- `logs/<job>.log`: `GET /actions/jobs/<job>/logs`, cut to the failure region
  and with most `[WebServer]` chatter removed. The per-line timestamp prefix is
  real; the raw API output for these jobs held no ANSI escapes.

| run/attempt | job | what it is |
|---|---|---|
| 34176264474/1 | e2e | Chromium SEGV; green on attempt 2 |
| 33700913846/1, /2 | e2e | smoke "review page loads"; red on BOTH attempts |
| 33765847479/1 | e2e | cassette miss in planner-chat; green on attempt 2 |
| 33461480444/1 | e2e | auth-login click timeout (the 1l26.8 mode, before its fix) |
| 33792179013/1 | backend-hostile-hour | 4 pytest failures |
| 34074558668/1 | (none) | fully green |
| 33442906154/1 | 3 backend jobs | before the window, must not appear |

Re-capture only by re-running the same `gh api` calls. Never hand-edit a
fixture to make a test pass.
