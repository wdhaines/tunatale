"""The per-test CPU budget (tests/_helpers/cpu_budget.py) fails a run on a slow test."""

from __future__ import annotations

import pytest

from tests._helpers import cpu_budget

pytest_plugins = ["pytester"]

#: An inner conftest that turns the budget on, as ``--cpu-budget`` does for the suite.
_CONFTEST = """
from tests._helpers.cpu_budget import CpuBudget

def pytest_configure(config):
    config.addinivalue_line("markers", "slow(reason): slow on purpose")
    config.pluginmanager.register(CpuBudget(enforce={enforce}), "cpu-budget")
"""

_TESTS = """
import time
import pytest

def burn(seconds):
    end = time.process_time() + seconds
    while time.process_time() < end:
        pass

def test_quick():
    pass

def test_burns_in_its_fixture(heavy):
    pass

@pytest.fixture
def heavy():
    burn(0.06)

def test_burns_in_its_body():
    burn(0.06)

@pytest.mark.slow(reason="a sweep of every form")
def test_marked():
    burn(0.06)
"""


@pytest.fixture
def small_budget(monkeypatch):
    """A 0.02 s budget and this machine as the reference, so an inner run
    costs a fraction of a second (these tests are themselves under the budget)."""
    monkeypatch.setattr(cpu_budget, "BUDGET_S", 0.02)
    monkeypatch.setattr(cpu_budget, "COLD_BUDGET_S", 1.0)
    monkeypatch.setattr(cpu_budget, "SLOW_BUDGET_S", 5.0)
    monkeypatch.setattr(cpu_budget, "_calibrate", lambda: 1.0)
    monkeypatch.setattr(cpu_budget, "REFERENCE_CALIBRATION_S", 1.0)


def _run(pytester, enforce: bool, tests: str = _TESTS):
    pytester.makeconftest(_CONFTEST.format(enforce=enforce))
    pytester.makepyfile(tests)
    return pytester.runpytest_inprocess("-p", "no:cacheprovider", "-p", "no:randomly", "-p", "no:asyncio")


def test_a_test_over_budget_fails_the_run_and_is_named(pytester, small_budget):
    result = _run(pytester, enforce=True)
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    result.assert_outcomes(passed=4)  # every test passes; the budget is what fails the run
    out = result.stdout.str()
    assert "test_burns_in_its_body" in out
    assert "test_burns_in_its_fixture" in out  # setup counts, not only the call


def test_a_quick_test_and_a_marked_slow_one_are_within_budget(pytester, small_budget):
    out = _run(pytester, enforce=True).stdout.str()
    section = out.split("CPU budget", 1)[1]
    assert "test_quick" not in section
    assert "test_marked" not in section


def test_report_mode_names_the_slow_test_without_failing(pytester, small_budget):
    result = _run(pytester, enforce=False)
    assert result.ret == pytest.ExitCode.OK
    assert "test_burns_in_its_body" in result.stdout.str()


def test_a_run_within_budget_prints_no_section(pytester, small_budget):
    result = _run(pytester, enforce=True, tests="def test_quick():\n    pass\n")
    assert result.ret == pytest.ExitCode.OK
    assert "CPU budget" not in result.stdout.str()


def test_a_slow_marker_without_a_reason_is_refused(pytester, small_budget):
    tests = "import pytest\n\n@pytest.mark.slow\ndef test_unexplained():\n    pass\n"
    result = _run(pytester, enforce=True, tests=tests)
    assert result.ret != pytest.ExitCode.OK
    assert "needs reason=" in result.stdout.str() + result.stderr.str()


def test_a_slower_machine_gets_proportionally_more_time(pytester, small_budget, monkeypatch):
    # This machine's workload takes 20x the reference's, so 0.06 s here is 0.003 s there.
    monkeypatch.setattr(cpu_budget, "_calibrate", lambda: 20.0)
    result = _run(pytester, enforce=True)
    assert result.ret == pytest.ExitCode.OK


def test_the_calibration_workload_takes_measurable_time():
    assert 0 < cpu_budget._calibrate() < 5


def test_a_test_slow_only_the_first_time_does_not_fail_the_run(pytester, small_budget):
    # A cold cache: the first run fills it, every later run is quick.
    tests = """
import time

_FILLED = []

def test_fills_a_cache():
    if not _FILLED:
        end = time.process_time() + 0.06
        while time.process_time() < end:
            pass
        _FILLED.append(True)
"""
    result = _run(pytester, enforce=True, tests=tests)
    assert result.ret == pytest.ExitCode.OK
    assert "CPU budget" not in result.stdout.str()


def test_the_verdict_is_the_warm_runs_cost(pytester, small_budget):
    out = _run(pytester, enforce=True).stdout.str()
    line = next(row for row in out.splitlines() if row.endswith("test_burns_in_its_body"))
    assert 0.04 < float(line.split("s CPU")[0]) < 0.11  # one 0.06 s burn, not two or three summed


def test_a_huge_first_run_fails_even_when_its_re_run_is_cheap(pytester, small_budget, monkeypatch):
    # Its own first run caches the work, so only the cold ceiling can see it.
    tests = """
import time

_DONE = []

def test_caches_its_own_work():
    if not _DONE:
        end = time.process_time() + 0.06
        while time.process_time() < end:
            pass
        _DONE.append(True)
"""
    monkeypatch.setattr(cpu_budget, "COLD_BUDGET_S", 0.03)
    result = _run(pytester, enforce=True, tests=tests)
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    assert "test_caches_its_own_work (first run)" in result.stdout.str()
