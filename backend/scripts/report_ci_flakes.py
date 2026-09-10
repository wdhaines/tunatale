"""The CI half of the standing flake sweep (tunatale-1l26, STANDING FLAKE SWEEP).

For a time window, this reports every failed CI job attempt, classifies each
against a registry of known flake signatures, says whether the same commit
later went green, and says whether a flake marked FIXED has recurred in a run
that contains its fix. Bespoke to this repo's six-job ci.yml layout; no generic
value outside it.

Usage:
    cd backend && uv run python scripts/report_ci_flakes.py --since 2026-09-10T17:00:00Z [--until ...] [--json]

Both boundaries accept YYYY-MM-DD (meaning T00:00:00Z) or a full
YYYY-MM-DDTHH:MM:SSZ. The GitHub filter passed to ``gh api`` is DATE-ONLY and
URL-encoded (created=>=YYYY-MM-DD); the exact window is enforced client-side,
because a datetime filter silently truncated the list on 2026-09-10.

Exit codes: 0 = report produced (with any content); 2 = bad arguments, or a
`gh` failure while listing runs or jobs. On the error path nothing is written
to stdout — a silent empty report is exactly what "no flakes" looks like, so it
must never be produced on a fetch error.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

FLAKE_REGISTRY = [
    {"key": "chromium_segv", "status": "OPEN", "bead": "tunatale-1l26.6", "text_any": ["SEGV_MAPERR"]},
    {
        "key": "cassette_miss",
        "status": "FIXED",
        "bead": "tunatale-hvbv",
        "fix": "2533c7d",
        "fixed_at": "2026-09-04T02:04:07Z",
        "text_any": ["Cassette has no entry for prompt hash", "LLM cassette miss"],
    },
    {
        "key": "auth_login_mid_visit",
        "status": "FIXED",
        "bead": "tunatale-1l26.8",
        "fix": "3c98938",
        "fixed_at": "2026-09-10T13:24:40Z",
        "spec": "auth-login.spec.ts",
        "text_any": ["locator.click: Test timeout"],
    },
    {
        "key": "auth_login_signin_bounce",
        "status": "FIXED",
        "bead": "tunatale-vnf.16",
        "fix": "5185c87",
        "fixed_at": "2026-08-23T23:11:58Z",
        "spec": "auth-login.spec.ts",
        "text_any": ["page.waitForURL: Test timeout"],
    },
    {
        "key": "smoke_review_due",
        "status": "FIXED",
        "bead": "tunatale-g9kr",
        "fix": "7a881a1",
        "fixed_at": "2026-09-03T02:43:42Z",
        "spec": "smoke.spec.ts",
        "title_contains": "review page loads",
    },
    {
        "key": "cors_health_503",
        "status": "FIXED",
        "bead": "tunatale-yp7b",
        "fix": "6402b9d",
        "fixed_at": "2026-09-10T12:11:58Z",
        "spec": "cors-lockdown.spec.ts",
        "text_any": ["Received: 503"],
    },
    {
        "key": "render_event_loop_ticker",
        "status": "OPEN",
        "bead": "tunatale-v885",
        "test": "test_render_does_not_block_event_loop",
    },
]

REGISTRY_BY_KEY = {entry["key"]: entry for entry in FLAKE_REGISTRY}

_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_FULL_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

_TS_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z ")
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_HEADER = re.compile(r"\s*\d+\)\s*\[[^\]]+\]\s*›\s*(?P<path>[^\s]+?\.spec\.ts):\d+:\d+\s*›\s*(?P<title>.+)$")
# A parametrized id may contain spaces inside its brackets (this repo has
# `test_step_kind[e2e-E2E tests-TEST]`), so the id is `\S+?` plus an optional
# `[...]`, not `\S+`: the first version cut the id at the space and then the
# whole line failed to match, so the failure vanished from the report.
_FAILED_PYTEST = re.compile(r"^FAILED (?P<nodeid>\S+?(?:\[.*?\])?)(?: - (?P<error>.*))?$")

# GitHub: "This endpoint will return up to 1,000 results for each search when
# using the following parameters: ... created ...". A listing that size is
# truncated with no signal, so it is refused rather than reported.
GITHUB_FILTERED_RESULT_CAP = 1000

_TEST_STEPS = {"E2E tests", "Test", "Unit tests", "Oracle parity gate", "Peer-sync gate"}


class GhError(Exception):
    def __init__(self, endpoint: str, message: str) -> None:
        super().__init__(f"{endpoint}: {message}")
        self.endpoint = endpoint
        self.message = message


def step_kind(job: str, step: str | None) -> str:
    """TEST when the step is a flake candidate, CHECK for a deterministic gate, INFRA otherwise."""
    if step in _TEST_STEPS or (step is not None and step.startswith("Test at ")):
        return "TEST"
    if step == "Lint" or (step is not None and step.endswith(" check")):
        return "CHECK"
    return "INFRA"


def _normalise_bound(arg: str) -> str:
    arg = arg.strip()
    if _DATE_ONLY.match(arg):
        return arg + "T00:00:00Z"
    if not _FULL_Z.match(arg):
        raise ValueError(f"invalid window boundary {arg!r}: use YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ")
    return arg


def _parse_z(text: str) -> datetime:
    return datetime.fromisoformat(text)


def _clean_line(line: str) -> str:
    line = _TS_PREFIX.sub("", line)
    line = _ANSI_ESCAPE.sub("", line)
    return line


def _strip_param(segment: str) -> str:
    return segment.split("[", 1)[0]


def _first_error(cleaned: list[str]) -> str | None:
    for line in cleaned:
        stripped = line.strip()
        if stripped.startswith("Error:"):
            return stripped
    return None


def _pytest_ids(cleaned: list[str]) -> tuple[list[str], str | None]:
    ids: list[str] = []
    first_error: str | None = None
    for line in cleaned:
        m = _FAILED_PYTEST.match(line.strip())
        if not m or "::" not in m["nodeid"]:  # `FAILED (errors=1)` is not a test id
            continue
        ids.append(m["nodeid"])
        if first_error is None and m["error"]:
            first_error = m["error"].strip()
    return sorted(set(ids)), first_error


def _match_registry(*, specs: list[str], headers: list[tuple[str, str]], tests: set[str], text: str) -> str | None:
    for entry in FLAKE_REGISTRY:
        spec = entry.get("spec")
        if spec is not None and spec not in specs:
            continue
        title = entry.get("title_contains")
        if title is not None and not any(spec == s and title in title_text for s, title_text in headers):
            continue
        text_any = entry.get("text_any")
        if text_any is not None and not any(pattern in text for pattern in text_any):
            continue
        test = entry.get("test")
        if test is not None and test not in tests:
            continue
        return entry["key"]
    return None


def classify_e2e_log(text: str) -> tuple[str, list[str], str | None]:
    cleaned = [_clean_line(line) for line in text.splitlines()]
    headers: list[tuple[str, str]] = []
    for line in cleaned:
        m = _HEADER.match(line)
        if not m:
            continue
        title = re.sub(r"[\s─]+$", "", m["title"])
        headers.append((m["path"].split("/")[-1], title))
    specs = sorted({spec for spec, _ in headers})
    signature = _match_registry(specs=specs, headers=headers, tests=set(), text="\n".join(cleaned)) or "UNCLASSIFIED"
    return signature, specs, _first_error(cleaned)


def _gh(endpoint: str) -> str:
    if endpoint.endswith("/logs"):
        cmd = ["--allow-escape-sequences"]
    elif "/actions/runs/" in endpoint and "/attempts/" in endpoint:
        cmd = []
    else:
        cmd = ["--paginate", "--jq", ".workflow_runs[]"]
    result = subprocess.run(
        ["gh", "api", *cmd, endpoint],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )
    if result.returncode != 0:
        raise GhError(endpoint, result.stderr.strip())
    return result.stdout


def _is_ancestor(fix: str, sha: str) -> bool | None:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", fix, sha],
        capture_output=True,
        cwd=REPO_ROOT,
        check=False,
    )
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    return None


def build_report(since: str, until: str, *, gh, is_ancestor, workflow: str = "ci.yml") -> dict:
    since_full = _normalise_bound(since)
    until_full = _normalise_bound(until)
    since_dt = _parse_z(since_full)
    until_dt = _parse_z(until_full)
    if since_dt >= until_dt:
        # A swapped --since/--until reports zero runs, which reads as "no flakes".
        raise ValueError(f"empty window: since {since_full} is not before until {until_full}")

    runs_endpoint = (
        f"repos/{{owner}}/{{repo}}/actions/workflows/{workflow}/runs?per_page=100&created=%3E%3D{since_full[:10]}"
    )
    runs = [json.loads(line) for line in gh(runs_endpoint).splitlines() if line.strip()]
    if len(runs) >= GITHUB_FILTERED_RESULT_CAP:
        raise GhError(
            runs_endpoint,
            f"{len(runs)} runs returned: GitHub caps filtered listings at {GITHUB_FILTERED_RESULT_CAP}, so this "
            "list is probably truncated. Use a later --since.",
        )
    kept = [run for run in runs if since_dt <= _parse_z(run["created_at"]) < until_dt]

    fetched: list[dict] = []
    for run in kept:
        for attempt in range(1, int(run["run_attempt"]) + 1):
            jobs_text = gh(f"repos/{{owner}}/{{repo}}/actions/runs/{run['id']}/attempts/{attempt}/jobs?per_page=100")
            jobs = json.loads(jobs_text).get("jobs", [])
            for job in jobs:
                jobrec = dict(job)
                jobrec["_run"] = run
                jobrec["_attempt"] = attempt
                fetched.append(jobrec)

    green = {(job.get("head_sha"), job.get("name")) for job in fetched if job.get("conclusion") == "success"}

    e2e_suite_executions = 0
    attempts_seen: set[tuple[int, int]] = set()
    for job in fetched:
        attempt_key = (job["_run"]["id"], job["_attempt"])
        if job.get("name") != "e2e" or attempt_key in attempts_seen:
            continue
        attempts_seen.add(attempt_key)
        if any(
            step.get("name") == "E2E tests" and step.get("conclusion") in ("success", "failure")
            for step in job.get("steps") or []
        ):
            e2e_suite_executions += 1

    failures: list[dict] = []
    logs_unavailable = 0
    for job in fetched:
        if job.get("conclusion") != "failure":
            continue
        failed_step = None
        for step in job.get("steps") or []:
            if step.get("conclusion") == "failure":
                failed_step = step.get("name")
                break
        run = job["_run"]
        record = {
            "run_id": run["id"],
            "attempt": job["_attempt"],
            "created_at": run["created_at"],
            "head_sha": job.get("head_sha") or run.get("head_sha"),
            "branch": run.get("head_branch"),
            "event": run.get("event"),
            "job": job.get("name"),
            "failed_step": failed_step,
            "kind": step_kind(job.get("name"), failed_step),
            "rerun_green": (job.get("head_sha"), job.get("name")) in green,
            "signature": None,
            "status": None,
            "bead": None,
            "falsifies": False,
            "fix_check": "n/a",
            "failing_specs": [],
            "failing_tests": [],
            "first_error": None,
            "log": "not-fetched",
        }
        if record["kind"] == "TEST":
            try:
                log_text = gh(f"repos/{{owner}}/{{repo}}/actions/jobs/{job['id']}/logs")
            except GhError:
                record["log"] = "unavailable"
                record["signature"] = "UNCLASSIFIED"
                logs_unavailable += 1
            else:
                if job.get("name") == "e2e":
                    signature, specs, first_error = classify_e2e_log(log_text)
                    record.update(
                        {"log": "ok", "signature": signature, "failing_specs": specs, "first_error": first_error}
                    )
                else:
                    cleaned = [_clean_line(line) for line in log_text.splitlines()]
                    nodeids, first_error = _pytest_ids(cleaned)
                    last_segments = {_strip_param(nodeid.split("::")[-1]) for nodeid in nodeids}
                    signature = (
                        _match_registry(specs=[], headers=[], tests=last_segments, text="\n".join(cleaned))
                        or "UNCLASSIFIED"
                    )
                    record.update(
                        {
                            "log": "ok",
                            "signature": signature,
                            "failing_tests": nodeids,
                            "first_error": first_error,
                        }
                    )
        entry = REGISTRY_BY_KEY.get(record["signature"])
        if entry is None:
            record["status"] = None
            record["bead"] = None
            record["fix_check"] = "n/a"
            record["falsifies"] = False
        elif entry["status"] == "FIXED":
            contained = is_ancestor(entry["fix"], record["head_sha"])
            if contained is None:
                record["fix_check"] = "by-date"
                record["falsifies"] = _parse_z(record["created_at"]) > _parse_z(entry["fixed_at"])
            else:
                record["fix_check"] = "ancestry"
                record["falsifies"] = contained
            record["status"] = entry["status"]
            record["bead"] = entry["bead"]
        else:
            record["status"] = entry["status"]
            record["bead"] = entry["bead"]
            record["fix_check"] = "n/a"
            record["falsifies"] = False
        failures.append(record)

    failures.sort(key=lambda f: (f["created_at"], f["run_id"], f["attempt"], f["job"]))

    span = None
    if kept:
        span = [
            min(kept, key=lambda r: r["created_at"])["created_at"],
            max(kept, key=lambda r: r["created_at"])["created_at"],
        ]

    by_signature: dict[str, int] = {}
    for failure in failures:
        if failure["kind"] == "TEST":
            by_signature[failure["signature"]] = by_signature.get(failure["signature"], 0) + 1

    falsified = [
        {
            "run_id": failure["run_id"],
            "attempt": failure["attempt"],
            "signature": failure["signature"],
            "bead": failure["bead"],
            "fix": REGISTRY_BY_KEY[failure["signature"]]["fix"],
            "head_sha": failure["head_sha"],
        }
        for failure in failures
        if failure["falsifies"]
    ]

    return {
        "window": {"since": since_full, "until": until_full, "workflow": workflow},
        "runs": len(kept),
        "attempts": sum(int(run["run_attempt"]) for run in kept),
        "cancelled": sum(1 for run in kept if run.get("conclusion") == "cancelled"),
        "run_span": span,
        "e2e_suite_executions": e2e_suite_executions,
        "failures": failures,
        "by_signature": by_signature,
        "falsified": falsified,
        "logs_unavailable": logs_unavailable,
    }


def _print_text(report: dict) -> None:
    window = report["window"]
    print(f"CI flake report  workflow={window['workflow']}  since={window['since']}  until={window['until']}")
    span = f"{report['run_span'][0]}..{report['run_span'][1]}" if report["run_span"] else "none"
    print(
        f"runs={report['runs']}  attempts={report['attempts']}  cancelled={report['cancelled']}  "
        f"e2e_suite_executions={report['e2e_suite_executions']}  span={span}"
    )
    print(f"logs_unavailable={report['logs_unavailable']}")
    print()
    print(f"FAILURES ({len(report['failures'])})")
    for f in report["failures"]:
        fields = [
            f["created_at"],
            f"{f['run_id']}/{f['attempt']}",
            f["job"],
            f["kind"],
            f"step={f['failed_step'] or '-'}",
            (f["head_sha"] or "-")[:7],
            f["branch"] or "-",
            f["signature"] or "-",
            f["status"] or "-",
        ]
        if f["rerun_green"]:
            fields.append("rerun-green")
        if f["failing_specs"]:
            fields.append("specs=" + ",".join(f["failing_specs"]))
        if f["failing_tests"]:
            fields.append("tests=" + ",".join(f["failing_tests"]))
        print("  " + "  ".join(fields))
    print()
    falsified = report["falsified"]
    print(f"FALSIFIED ({len(falsified)})")
    if not falsified:
        print("  none")
    for item in falsified:
        print(
            f"  {item['signature']}  run={item['run_id']}/{item['attempt']}  bead={item['bead']}  "
            f"fix={item['fix']}  sha={item['head_sha'][:7]}"
        )
    print()
    print("BY SIGNATURE")
    for signature, count in sorted(report["by_signature"].items()):
        print(f"  {signature}  {count}")


def _fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 2


def main(argv: list[str] | None = None, *, gh=None, is_ancestor=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    gh_impl = _gh if gh is None else gh
    ancestor_impl = _is_ancestor if is_ancestor is None else is_ancestor

    since: str | None = None
    until: str | None = None
    json_out = False
    i = 0
    while i < len(argv):
        if argv[i] == "--since" and i + 1 < len(argv):
            since = argv[i + 1]
            i += 2
        elif argv[i] == "--until" and i + 1 < len(argv):
            until = argv[i + 1]
            i += 2
        elif argv[i] == "--json":
            json_out = True
            i += 1
        else:
            return _fail(f"bad argument: {argv[i]}")
    if since is None:
        return _fail("--since is required (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ)")
    if until is None:
        until = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        since_full = _normalise_bound(since)
        until_full = _normalise_bound(until)
    except ValueError as exc:
        return _fail(str(exc))

    try:
        report = build_report(since_full, until_full, gh=gh_impl, is_ancestor=ancestor_impl)
    except (GhError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if json_out:
        print(json.dumps(report, indent=2))
    else:
        _print_text(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
