"""A per-test CPU budget, so a slow test fails the run the day it lands (tunatale-1l26.14).

Why: in October one fixture began building Wolff's verb tables for every Cebuano card
it added, and ~70 tests each gained about a second. Nothing failed; CI's backend job
crept from 3 to 5 minutes and it took weeks to notice.

What it measures: the worker's CPU time (``time.process_time``) across a test's setup,
call and teardown. CPU, not wall-clock, so the budget does not move when ``./test.sh``
runs its three groups at once or a CI runner is busy. A subprocess's CPU is not counted,
and neither is sleeping: this guards against work done in-process.

Machines: every budget is in REFERENCE-machine seconds. Each worker times a fixed
workload (:func:`_calibrate`) and scales what it measures, so a GitHub runner, slower per
core, gets proportionally more time and a test passes or fails the same in both places.

Cold caches: a process-wide cache (Wolff's lexicon, the affix decoder's per-root forms)
is filled by whichever test touches it first, and under xdist that test is random. So a
test over budget is run again at the end of its worker's session, warm, twice, and fails
only if the last of those runs is over budget too. A cold fill never fails the run; a
fixture that costs a second every time always does. The first, cold run has its own,
higher ceiling (:data:`COLD_BUDGET_S`), so one huge new test whose work is cached by its
own first run cannot pass on a cheap re-run.

A test that is slow on purpose says so: ``@pytest.mark.slow(reason="...")`` raises its
budget, warm and cold, to :data:`SLOW_BUDGET_S`. The reason is required.

On only with ``--cpu-budget=enforce`` (``./test.sh`` and CI's ``backend`` job) or
``=report`` (names offenders, never fails), so a single-file run pays nothing.
"""

from __future__ import annotations

import time

import pytest
from _pytest.runner import runtestprotocol

#: CPU seconds one test may use, in reference-machine seconds, measured under coverage
#: (which roughly doubles a test's CPU).
BUDGET_S = 1.0
#: The ceiling on a test's first run, cold caches included (filling Wolff's lexicon is
#: ~3 s). Measured once, never re-run.
COLD_BUDGET_S = 10.0
#: The budget of a test marked ``slow``, warm and cold.
SLOW_BUDGET_S = 20.0
#: :func:`_calibrate` on the reference machine (the owner's laptop, Apple Silicon,
#: 2026-11-05: 0.051-0.052 s alone, 0.053-0.060 s ten-wide).
REFERENCE_CALIBRATION_S = 0.052
#: The key a worker reports its offenders under, in xdist's ``workeroutput``.
_OUTPUT_KEY = "cpu_budget_over"

_CPU = pytest.StashKey[float]()


def _calibrate() -> float:
    """CPU seconds a fixed pure-Python workload takes here: the best of five."""
    best = float("inf")
    for _ in range(5):
        start = time.process_time()
        table: dict[int, int] = {}
        x = 0
        for i in range(400_000):
            table[i % 1024] = x
            x += (i * i) % 7 + len(str(i))
        best = min(best, time.process_time() - start)
    return best


def _budgets(item: pytest.Item) -> tuple[float, float]:
    """This test's (warm, cold) budgets in reference seconds."""
    marker = item.get_closest_marker("slow")
    if marker is None:
        return BUDGET_S, COLD_BUDGET_S
    if not marker.kwargs.get("reason"):
        raise pytest.UsageError(f"{item.nodeid}: @pytest.mark.slow needs reason='why it must be slow'")
    return SLOW_BUDGET_S, SLOW_BUDGET_S


class CpuBudget:
    def __init__(self, enforce: bool) -> None:
        self.enforce = enforce
        self.scale = 1.0  # this worker's CPU second, in reference seconds
        self.suspects: list[pytest.Item] = []  # over the warm budget on their first run
        self.cold_over: list[tuple[str, float, float]] = []  # over the cold ceiling: no re-run
        self.rerunning = False  # the warm re-runs report through the same hooks
        self.over: list[tuple[str, float, float]] = []  # (nodeid, warm CPU, budget): the verdict

    # ── worker side: measure ─────────────────────────────────────────────────────

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        self.scale = REFERENCE_CALIBRATION_S / _calibrate()

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_setup(self, item: pytest.Item):
        start = time.process_time()
        yield
        item.stash[_CPU] = item.stash.get(_CPU, 0.0) + time.process_time() - start

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_call(self, item: pytest.Item):
        start = time.process_time()
        yield
        item.stash[_CPU] = item.stash.get(_CPU, 0.0) + time.process_time() - start

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_teardown(self, item: pytest.Item):
        start = time.process_time()
        yield
        item.stash[_CPU] = item.stash.get(_CPU, 0.0) + time.process_time() - start

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_makereport(self, item: pytest.Item, call: pytest.CallInfo):
        yield
        if call.when == "teardown" and not self.rerunning:
            cpu = item.stash.get(_CPU, 0.0) * self.scale
            warm_budget, cold_budget = _budgets(item)
            if cpu > cold_budget:
                self.cold_over.append((f"{item.nodeid} (first run)", cpu, cold_budget))
            elif cpu > warm_budget:
                self.suspects.append(item)

    def _warm_cpu(self, item: pytest.Item) -> float:
        """The test's CPU on a warm run: run it once to warm, then twice more, unlogged,
        each priced against a calibration taken beside it; the cheaper of the two counts.

        Between the runs pytest tears down what the *next* item does not share. A
        sibling (same module or class) as the next item keeps the module- and
        session-scoped fixtures up, so the measured run pays only the test's own
        function-scoped fixtures, as it would mid-suite. The item itself as the next
        would keep those up too and hide a slow function fixture.
        """
        sibling = next((i for i in item.session.items if i is not item and i.parent is item.parent), None)
        runtestprotocol(item, log=False, nextitem=sibling)  # fills module/session fixtures
        best = float("inf")
        for _ in range(2):
            # Re-calibrated beside each run: on a loaded Apple Silicon machine a worker
            # can land on an efficiency core, where a CPU second does far less work, so a
            # scale taken once at startup misprices a run made later (2026-11-05: 0.8 s
            # alone read 1.6 s under ./test.sh's three parallel groups).
            scale = REFERENCE_CALIBRATION_S / _calibrate()
            item.stash[_CPU] = 0.0
            runtestprotocol(item, log=False, nextitem=sibling)
            best = min(best, item.stash[_CPU] * scale)
        return best

    @pytest.hookimpl(tryfirst=True)
    def pytest_sessionfinish(self, session: pytest.Session) -> None:
        # tryfirst: before xdist's own hook ships workeroutput to the controller.
        verdict = list(self.cold_over)
        self.rerunning = True
        for item in self.suspects:
            warm, (warm_budget, _) = self._warm_cpu(item), _budgets(item)
            if warm > warm_budget:
                verdict.append((item.nodeid, warm, warm_budget))
        self.rerunning = False
        self.suspects.clear()
        self.cold_over.clear()
        workeroutput = getattr(session.config, "workeroutput", None)
        if workeroutput is not None:  # an xdist worker: the controller judges
            workeroutput[_OUTPUT_KEY] = verdict
            return
        # The controller (or a run without xdist), after every worker's verdict is in.
        self.over.extend(verdict)
        if self.enforce and self.over and session.exitstatus == pytest.ExitCode.OK:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED

    # ── controller side: judge ───────────────────────────────────────────────────

    def pytest_testnodedown(self, node, error) -> None:
        self.over.extend(tuple(o) for o in node.workeroutput.get(_OUTPUT_KEY, []))

    def pytest_terminal_summary(self, terminalreporter) -> None:
        if not self.over:
            return
        terminalreporter.section("CPU budget", red=self.enforce, sep="=")
        for nodeid, cpu, budget in sorted(self.over, key=lambda o: -o[1]):
            terminalreporter.line(f"{cpu:6.2f}s CPU > {budget:g}s  {nodeid}")
        terminalreporter.line(
            "Reference-machine CPU seconds (warm re-run unless marked first run), the same in every environment. "
            "Make the test cheaper, or mark it @pytest.mark.slow(reason=...) if it must be slow "
            "(tests/_helpers/cpu_budget.py)."
        )
