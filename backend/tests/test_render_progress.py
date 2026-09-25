"""``RenderProgress``: a sliding-window rate, so the ETA is not a lie (tunatale-hbnd).

The window is the whole point. A render's cache hits all finish in its first
moments — on the Cebuano lesson that is ~150 of 171 clips — so a rate measured
from the start predicts "done" seconds after the first real network call. After
60 seconds they drop out of the window and the rate describes the throttled
clips the render actually has left.
"""

from app.generation.render_progress import RenderProgress

# The measured oracle: three completions at t=0 (the cache hits), then one each
# at 30, 40, 50 and 60 — one every 10 s, which is what Google's per-minute
# throttle turns out to be.
_SCRIPTED = [(3, 10, 0.0), (4, 10, 30.0), (5, 10, 40.0), (6, 10, 50.0), (7, 10, 60.0)]


def _scripted() -> RenderProgress:
    progress = RenderProgress(started_at=0.0)
    for done, total, now in _SCRIPTED:
        progress.update(done, total, now)
    return progress


def test_eta_is_withheld_until_the_render_has_run_30_seconds():
    assert _scripted().eta_seconds(20.0) is None


def test_eta_uses_every_completion_in_the_first_minute():
    # window [0, 60]: 7 completions / 60 s -> 3 left at 7/60 each = 25.7 s.
    assert _scripted().eta_seconds(60.0) == 26


def test_eta_forgets_the_cache_hits_once_they_leave_the_window():
    # window [30, 90]: 4 completions / 60 s -> 3 left = 45 s.
    assert _scripted().eta_seconds(90.0) == 45


def test_eta_is_withheld_when_the_window_has_starved():
    # window [70, 130]: nothing finished at all, and 3 is the floor below which
    # a rate is noise.
    assert _scripted().eta_seconds(130.0) is None


def test_eta_is_withheld_below_three_completions_in_the_window():
    progress = RenderProgress(started_at=0.0)
    progress.update(2, 10, 0.0)
    progress.update(3, 10, 40.0)
    # window [35, 95] holds one completion: too few to divide by.
    assert progress.eta_seconds(95.0) is None


def test_eta_is_none_once_every_clip_is_done():
    progress = _scripted()
    progress.update(10, 10, 60.0)
    assert progress.eta_seconds(60.0) is None


def test_a_jump_in_done_records_one_timestamp_per_clip():
    """A caller that misses intermediate updates still yields a real rate."""
    progress = RenderProgress(started_at=0.0)
    progress.update(2, 10, 0.0)
    progress.update(6, 10, 30.0)  # a jump of four, all finishing at t=30
    # window [0, 60]: 6 completions / span min(60, 60) = 60 -> 4 left = 40 s.
    assert progress.eta_seconds(60.0) == 40


def test_the_window_caps_at_a_minute_so_a_long_render_keeps_moving():
    """Beyond 60 s the rate is per-minute, not per-elapsed-time.

    Six completions over 10 minutes is 6/min, and 12 clips left is two minutes —
    averaging from the start would report 100 minutes and hide the stall.
    """
    progress = RenderProgress(started_at=0.0)
    progress.update(6, 18, 600.0)
    # window [540, 660]: 6 completions / 60 s -> 12 left = 120 s.
    assert progress.eta_seconds(660.0) == 120


def test_a_repeated_count_does_not_claim_a_completion():
    """``total`` rises on nearly every update; only ``done`` moves the rate."""
    progress = RenderProgress(started_at=0.0)
    for total in range(1, 10):
        progress.update(0, total, 0.0)
    for done in range(1, 4):
        progress.update(done, 10, 30.0)
    # window [0, 60] holds 3 completions, so an ETA is reportable at all...
    assert progress.eta_seconds(60.0) == round(7 / (3 / 60))
