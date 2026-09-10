"""The CI half of the standing flake sweep (tunatale-1l26.10).

LOCKED TESTS, written by the orchestrator before the implementation. Their
oracle is REAL CI data (tests/fixtures/ci_flakes/, see its README), measured
independently on 2026-09-10. The inline cases below are synthetic and exist to
pin the properties a naive matcher gets wrong.

Every network or git touch goes through two injected callables, `gh` and
`is_ancestor`, so nothing here patches the module and nothing reaches GitHub.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from scripts.report_ci_flakes import (
    GhError,
    build_report,
    classify_e2e_log,
    main,
    step_kind,
)

FIX = Path(__file__).parent / "fixtures" / "ci_flakes"
SINCE, UNTIL = "2026-09-01T00:00:00Z", "2026-09-10T12:00:00Z"


class FakeGh:
    """Serves fixture files by endpoint, and records every endpoint asked for."""

    def __init__(self, missing_logs: frozenset[int] = frozenset(), fail_runs: bool = False) -> None:
        self.calls: list[str] = []
        self.missing_logs = missing_logs
        self.fail_runs = fail_runs

    def __call__(self, endpoint: str) -> str:
        self.calls.append(endpoint)
        if "/actions/workflows/" in endpoint and "/runs" in endpoint:
            if self.fail_runs:
                raise GhError(endpoint, "HTTP 401: Bad credentials")
            return (FIX / "runs.ndjson").read_text()
        m = re.search(r"/actions/runs/(\d+)/attempts/(\d+)/jobs", endpoint)
        if m:
            return (FIX / "jobs" / f"{m[1]}_{m[2]}.json").read_text()
        m = re.search(r"/actions/jobs/(\d+)/logs", endpoint)
        if m:
            jid = int(m[1])
            path = FIX / "logs" / f"{jid}.log"
            if jid in self.missing_logs or not path.exists():
                raise GhError(endpoint, "HTTP 410: logs expired")
            return path.read_text()
        raise AssertionError(f"unexpected endpoint {endpoint}")


def never_contains(fix: str, sha: str) -> bool | None:
    return False


def _key(f: dict) -> tuple:
    return (f["run_id"], f["attempt"], f["job"], f["kind"], f["rerun_green"], f["signature"])


class TestFixtureWindowAnswerKey:
    """The measured answer for the fixture set over 2026-09-01..2026-09-10T12Z."""

    def test_counts(self) -> None:
        r = build_report(SINCE, UNTIL, gh=FakeGh(), is_ancestor=never_contains)
        assert (r["runs"], r["attempts"], r["cancelled"], r["e2e_suite_executions"]) == (6, 9, 0, 9)
        assert r["run_span"] == ["2026-09-01T02:09:33Z", "2026-09-08T01:19:35Z"]
        assert r["window"] == {"since": SINCE, "until": UNTIL, "workflow": "ci.yml"}

    def test_every_failure_classified_as_measured(self) -> None:
        r = build_report(SINCE, UNTIL, gh=FakeGh(), is_ancestor=never_contains)
        assert sorted(_key(f) for f in r["failures"]) == sorted(
            [
                (33461480444, 1, "e2e", "TEST", False, "auth_login_mid_visit"),
                (33700913846, 1, "e2e", "TEST", False, "smoke_review_due"),
                (33700913846, 2, "e2e", "TEST", False, "smoke_review_due"),
                (33765847479, 1, "e2e", "TEST", True, "cassette_miss"),
                (33792179013, 1, "backend-hostile-hour", "TEST", False, "UNCLASSIFIED"),
                (34176264474, 1, "e2e", "TEST", True, "chromium_segv"),
            ]
        )
        assert r["by_signature"] == {
            "auth_login_mid_visit": 1,
            "smoke_review_due": 2,
            "cassette_miss": 1,
            "UNCLASSIFIED": 1,
            "chromium_segv": 1,
        }
        assert r["falsified"] == []
        assert r["logs_unavailable"] == 0

    def test_the_run_before_the_window_is_never_reported(self) -> None:
        """33442906154 (2026-08-31) failed three jobs. The server filter is date-granular
        and GitHub returned a silently TRUNCATED list for a datetime filter on
        2026-09-10, so the exact window is enforced client-side."""
        r = build_report(SINCE, UNTIL, gh=FakeGh(), is_ancestor=never_contains)
        assert all(f["run_id"] != 33442906154 for f in r["failures"])

    def test_the_server_filter_is_url_encoded_and_date_only(self) -> None:
        gh = FakeGh()
        build_report(SINCE, UNTIL, gh=gh, is_ancestor=never_contains)
        runs_calls = [c for c in gh.calls if "/runs?" in c]
        assert len(runs_calls) == 1
        assert "workflows/ci.yml/runs" in runs_calls[0]
        assert "created=%3E%3D2026-09-01" in runs_calls[0]
        assert "T00" not in runs_calls[0]

    def test_failure_details_carry_what_a_sweep_reads(self) -> None:
        r = build_report(SINCE, UNTIL, gh=FakeGh(), is_ancestor=never_contains)
        segv = next(f for f in r["failures"] if f["signature"] == "chromium_segv")
        assert segv["failing_specs"] == ["transcript-overflow.spec.ts"]
        assert segv["status"] == "OPEN" and segv["bead"] == "tunatale-1l26.6"
        assert segv["failed_step"] == "E2E tests" and segv["log"] == "ok"
        assert segv["head_sha"].startswith("baaa5e7") and segv["created_at"] == "2026-09-08T01:19:35Z"
        backend = next(f for f in r["failures"] if f["job"] == "backend-hostile-hour")
        assert backend["failing_tests"] == [
            "tests/test_colday_helper_consistency.py::TestNativeGradeMatchesSyncWriteback::test_native_due_date_advances_by_interval",
            "tests/test_colday_helper_consistency.py::TestNativeGradeMatchesSyncWriteback::test_native_equals_sync_for_each_interval",
            "tests/test_fsrs.py::TestReviewScheduling::test_review_again_uses_integer_col_day_elapsed_LAYER_50",
            "tests/test_fsrs.py::TestReviewScheduling::test_review_good_uses_integer_col_day_elapsed_LAYER_50",
        ]
        assert backend["failing_specs"] == []


class TestFalsification:
    """A FIXED signature recurring in a run that CONTAINS its fix falsifies the fix."""

    def test_a_fixed_signature_in_a_run_containing_the_fix_falsifies_it(self) -> None:
        r = build_report(SINCE, UNTIL, gh=FakeGh(), is_ancestor=lambda fix, sha: True)
        keys = sorted((x["signature"], x["run_id"], x["attempt"]) for x in r["falsified"])
        assert keys == [
            ("auth_login_mid_visit", 33461480444, 1),
            ("cassette_miss", 33765847479, 1),
            ("smoke_review_due", 33700913846, 1),
            ("smoke_review_due", 33700913846, 2),
        ]
        segv = next(f for f in r["failures"] if f["signature"] == "chromium_segv")
        assert segv["falsifies"] is False  # OPEN has no fix to falsify
        assert all(f["fix_check"] == "ancestry" for f in r["failures"] if f["status"] == "FIXED")

    def test_an_unknown_sha_falls_back_to_dates_and_says_so(self) -> None:
        """All four FIXED-signature fixture failures predate their fix commits."""
        r = build_report(SINCE, UNTIL, gh=FakeGh(), is_ancestor=lambda fix, sha: None)
        fixed = [f for f in r["failures"] if f["status"] == "FIXED"]
        assert len(fixed) == 4
        assert all(f["fix_check"] == "by-date" and f["falsifies"] is False for f in fixed)


class TestLoudness:
    def test_a_failure_to_list_runs_is_an_error_not_an_empty_report(self, capsys) -> None:
        """A silent zero is exactly what "no flakes" looks like. Never produce one on a fetch error."""
        rc = main(["--since", SINCE, "--until", UNTIL, "--json"], gh=FakeGh(fail_runs=True), is_ancestor=never_contains)
        out, err = capsys.readouterr()
        assert rc == 2
        assert out == ""
        assert "Bad credentials" in err

    def test_an_expired_log_keeps_the_failure_and_counts_it(self) -> None:
        r = build_report(SINCE, UNTIL, gh=FakeGh(missing_logs=frozenset({101906165362})), is_ancestor=never_contains)
        f = next(x for x in r["failures"] if x["run_id"] == 34176264474)
        assert (f["log"], f["signature"]) == ("unavailable", "UNCLASSIFIED")
        assert r["logs_unavailable"] == 1
        assert len(r["failures"]) == 6

    def test_a_bad_since_is_rejected(self, capsys) -> None:
        assert main(["--since", "last tuesday"], gh=FakeGh(), is_ancestor=never_contains) == 2

    def test_json_output_round_trips_the_report(self, capsys) -> None:
        rc = main(["--since", SINCE, "--until", UNTIL, "--json"], gh=FakeGh(), is_ancestor=never_contains)
        out, _ = capsys.readouterr()
        assert rc == 0
        assert json.loads(out) == build_report(SINCE, UNTIL, gh=FakeGh(), is_ancestor=never_contains)

    def test_text_output_names_every_failure(self, capsys) -> None:
        rc = main(["--since", SINCE, "--until", UNTIL], gh=FakeGh(), is_ancestor=never_contains)
        out, _ = capsys.readouterr()
        assert rc == 0
        for run_id in (33461480444, 33700913846, 33765847479, 33792179013, 34176264474):
            assert str(run_id) in out
        assert "UNCLASSIFIED" in out


def _log(*lines: str) -> str:
    """Synthetic log in the real shape: every line carries a runner timestamp."""
    return "\n".join(f"2026-09-08T01:21:0{i % 10}.1234567Z {line}" for i, line in enumerate(lines))


class TestClassifyE2eLog:
    """classify_e2e_log(text) -> (signature, failing_specs, first_error)."""

    def test_segv_wins_even_when_a_spec_signature_also_matches(self) -> None:
        text = _log(
            "  1) [chromium] › tests/auth-login.spec.ts:78:1 › a session that dies mid-visit",
            "    Error: locator.click: Test timeout of 30000ms exceeded.",
            "    [pid=3062][err] Received signal 11 SEGV_MAPERR 0000000001b0",
        )
        assert classify_e2e_log(text)[0] == "chromium_segv"

    def test_a_passing_line_for_a_known_spec_does_not_match(self) -> None:
        """✓ lines name every spec that PASSED. Only failure headers count."""
        text = _log(
            "  ✓  12 [chromium] › tests/smoke.spec.ts:48:1 › review page loads (410ms)",
            "  1) [chromium] › tests/lesson-source.spec.ts:20:1 › something else",
            "    Error: expect(locator).toBeVisible() failed",
        )
        sig, specs, first_error = classify_e2e_log(text)
        assert sig == "UNCLASSIFIED"
        assert specs == ["lesson-source.spec.ts"]
        assert first_error == "Error: expect(locator).toBeVisible() failed"

    def test_a_spec_is_matched_by_exact_name_not_substring(self) -> None:
        text = _log("  1) [chromium] › tests/smoke-extra.spec.ts:48:1 › review page loads")
        assert classify_e2e_log(text)[0] == "UNCLASSIFIED"

    def test_a_title_constraint_reads_the_failing_header_not_the_log(self) -> None:
        """smoke.spec.ts failing on ANOTHER test, while "review page loads" appears elsewhere."""
        text = _log(
            "  ✓  3 [chromium] › tests/smoke.spec.ts:48:1 › review page loads (300ms)",
            "  1) [chromium] › tests/smoke.spec.ts:60:1 › home page loads",
        )
        assert classify_e2e_log(text)[0] == "UNCLASSIFIED"

    def test_ansi_escapes_and_timestamps_are_stripped_before_matching(self) -> None:
        text = _log(
            "\x1b[31m  1) [chromium] › tests/smoke.spec.ts:48:1 › review page loads \x1b[39m",
            "\x1b[2m    Error: expect(locator).toHaveText(expected) failed\x1b[22m",
        )
        sig, specs, first_error = classify_e2e_log(text)
        assert sig == "smoke_review_due"
        assert specs == ["smoke.spec.ts"]
        assert first_error == "Error: expect(locator).toHaveText(expected) failed"

    def test_the_post_58593ee_teardown_wording_is_a_cassette_miss_too(self) -> None:
        text = _log("Error: 1 LLM cassette miss during the e2e run. The app may have swallowed them")
        assert classify_e2e_log(text)[0] == "cassette_miss"

    def test_a_log_with_no_failure_header_is_unclassified_with_no_specs(self) -> None:
        assert classify_e2e_log(_log("Running 61 tests using 2 workers", "error: script exited with code 1")) == (
            "UNCLASSIFIED",
            [],
            None,
        )


@pytest.mark.parametrize(
    ("job", "step", "kind"),
    [
        ("e2e", "E2E tests", "TEST"),
        ("backend", "Test", "TEST"),
        ("backend-hostile-tz (Etc/GMT-3)", "Test at Etc/GMT-3", "TEST"),
        ("backend-hostile-hour", "Test at the rollover hour", "TEST"),
        ("frontend", "Unit tests", "TEST"),
        ("anki-gates", "Oracle parity gate", "TEST"),
        ("anki-gates", "Peer-sync gate", "TEST"),
        ("backend", "Lint", "CHECK"),
        ("frontend", "Format check", "CHECK"),
        ("frontend", "Type check", "CHECK"),
        ("frontend", "OpenAPI type check", "CHECK"),
        ("backend", "Mock boundary check", "CHECK"),
        ("backend", "Install ffmpeg", "INFRA"),
        ("e2e", "Install Playwright system dependencies", "INFRA"),
        ("e2e", "Upload Playwright report", "INFRA"),
        ("backend", "Build NST lexicon", "INFRA"),
        ("e2e", "Something added to ci.yml next month", "INFRA"),
    ],
)
def test_step_kind(job: str, step: str, kind: str) -> None:
    """TEST = a flake candidate. CHECK = deterministic (lint/format/type), a real red.
    INFRA = everything else, including steps that do not exist yet."""
    assert step_kind(job, step) == kind


class SyntheticGh:
    """One run, one attempt, one failed backend `Test` job with the given log."""

    def __init__(self, log: str, n_runs: int = 1) -> None:
        self.log = log
        self.n_runs = n_runs

    def __call__(self, endpoint: str) -> str:
        if endpoint.endswith("/logs"):  # first: `…/actions/jobs/77/logs` also contains "/jobs"
            return self.log
        if "/runs?" in endpoint:
            run = {
                "id": 1,
                "run_attempt": 1,
                "conclusion": "failure",
                "created_at": "2026-09-05T00:00:00Z",
                "head_sha": "abc1234",
                "head_branch": "main",
                "event": "push",
            }
            return "\n".join(json.dumps(run | {"id": i + 1}) for i in range(self.n_runs))
        if "/jobs" in endpoint:
            job = {
                "id": 77,
                "name": "backend",
                "conclusion": "failure",
                "head_sha": "abc1234",
                "steps": [{"name": "Test", "conclusion": "failure"}],
            }
            return json.dumps({"jobs": [job]})
        raise AssertionError(f"unexpected endpoint {endpoint}")


class TestAuditFindings:
    """Added by the orchestrator's audit of BP's delivery (1l26.10). Each one was
    a real hole in the first implementation, found by an adversarial probe."""

    def test_a_parametrized_pytest_id_containing_spaces_is_kept_whole(self) -> None:
        """Ids like `test_step_kind[e2e-E2E tests-TEST]` exist in this repo. The first
        version cut the id at the space, and the line then failed to match at all, so
        the failure silently vanished from the report."""
        log = _log(
            "FAILED tests/test_a.py::TestY::test_render_does_not_block_event_loop[a b] - boom",
            "FAILED tests/test_b.py::test_step_kind[e2e-E2E tests-TEST] - AssertionError",
        )
        r = build_report("2026-09-01", "2026-09-10", gh=SyntheticGh(log), is_ancestor=never_contains)
        (f,) = r["failures"]
        assert f["failing_tests"] == [
            "tests/test_a.py::TestY::test_render_does_not_block_event_loop[a b]",
            "tests/test_b.py::test_step_kind[e2e-E2E tests-TEST]",
        ]
        assert f["signature"] == "render_event_loop_ticker"
        assert f["first_error"] == "boom"

    def test_lines_that_merely_contain_failed_are_not_test_ids(self) -> None:
        log = _log(
            "tests/test_a.py::test_x FAILED [ 50%]",
            "FAILED (errors=1)",
            "FAILED tests/test_a.py::test_y - boom",
        )
        r = build_report("2026-09-01", "2026-09-10", gh=SyntheticGh(log), is_ancestor=never_contains)
        assert r["failures"][0]["failing_tests"] == ["tests/test_a.py::test_y"]

    def test_a_name_that_only_starts_with_a_registry_test_does_not_match(self) -> None:
        log = _log("FAILED tests/test_a.py::test_render_does_not_block_event_loop_extra - boom")
        r = build_report("2026-09-01", "2026-09-10", gh=SyntheticGh(log), is_ancestor=never_contains)
        assert r["failures"][0]["signature"] == "UNCLASSIFIED"

    @pytest.mark.parametrize(("since", "until"), [("2026-09-24", "2026-09-10"), ("2026-09-10", "2026-09-10")])
    def test_an_empty_or_inverted_window_is_an_error_not_an_empty_report(self, since, until, capsys) -> None:
        """A swapped --since/--until would otherwise report zero runs, which reads as "no flakes"."""
        assert main(["--since", since, "--until", until], gh=SyntheticGh(""), is_ancestor=never_contains) == 2
        assert capsys.readouterr().out == ""

    def test_hitting_githubs_1000_result_cap_is_an_error(self, capsys) -> None:
        """GitHub: "This endpoint will return up to 1,000 results for each search when
        using ... created". A capped listing is a truncated one, silently."""
        rc = main(
            ["--since", "2026-09-01", "--until", "2026-09-10"],
            gh=SyntheticGh("", n_runs=1000),
            is_ancestor=never_contains,
        )
        out, err = capsys.readouterr()
        assert rc == 2 and out == ""
        assert "1000" in err
