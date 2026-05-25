"""Unit tests for the FSRS load-balancer port (Layer 53).

These run WITHOUT Anki (CI runs `pytest` without `--run-oracle`), so they cover
every line/branch of `app/srs/load_balancer.py` and the balancer hooks in
`fsrs.py`. The bit-exact-vs-Anki guarantee is pinned separately by the oracle
harness (`test_parity_load_balancer.py`, opt-in via `--run-oracle`); the golden
values here were captured from that Anki-validated implementation (24/24 + 6/6
bit-exact in the 2026-05 sweep) and guard against regression.
"""

from __future__ import annotations

from app.srs.fsrs import (
    DEFAULT_FSRS5_PARAMS,
    _graduation_intervals_with_fuzz,
    _next_interval_raw,
    _passing_intervals_with_fuzz,
    _quantize_stability,
)
from app.srs.load_balancer import (
    _EASY_MINIMUM,
    _EASY_NORMAL,
    _EASY_REDUCED,
    LOAD_BALANCE_DAYS,
    LoadBalancer,
    _calculate_easy_days_modifiers,
    _calculate_sibling_modifiers,
    _easy_day_from_percentage,
    _f32_powi3,
    _interval_to_weekday,
    _LoadBalancerDay,
    _LoadBalancerInterval,
    _select_weighted_interval,
)


class TestEasyDayFromPercentage:
    def test_normal(self):
        assert _easy_day_from_percentage(1.0) == _EASY_NORMAL

    def test_minimum(self):
        assert _easy_day_from_percentage(0.0) == _EASY_MINIMUM

    def test_reduced(self):
        assert _easy_day_from_percentage(0.5) == _EASY_REDUCED


class TestLoadBalancerDay:
    def test_add_and_has_sibling(self):
        day = _LoadBalancerDay()
        day.add(1, 100)
        day.add(2, 100)  # same note → sibling
        assert day.has_sibling(100)
        assert not day.has_sibling(999)
        assert len(day.cards) == 2

    def test_remove_keeps_note_while_sibling_present(self):
        day = _LoadBalancerDay()
        day.add(1, 100)
        day.add(2, 100)
        day.remove(1)
        assert day.has_sibling(100)  # card 2 still holds note 100

    def test_remove_last_card_drops_note(self):
        day = _LoadBalancerDay()
        day.add(1, 100)
        day.remove(1)
        assert not day.has_sibling(100)

    def test_remove_missing_card_is_noop(self):
        day = _LoadBalancerDay()
        day.add(1, 100)
        day.remove(42)
        assert len(day.cards) == 1


class TestIntervalToWeekday:
    def test_returns_weekday_in_range(self):
        for interval in (1, 7, 30, 90):
            wd = _interval_to_weekday(interval, 1779534000)
            assert 0 <= wd <= 6


class TestEasyDaysModifiers:
    def test_all_normal_yields_all_ones(self):
        easy = [_EASY_NORMAL] * 7
        weekdays = [0, 1, 2]
        counts = [5, 10, 0]
        assert _calculate_easy_days_modifiers(easy, weekdays, counts) == [1.0, 1.0, 1.0]

    def test_reduced_day_under_threshold_stays_normal(self):
        # One Reduced day (weekday 0) with a low count relative to the others
        # falls under the reduced threshold → treated as Normal.
        easy = [_EASY_REDUCED] + [_EASY_NORMAL] * 6
        weekdays = [0, 1, 2]
        counts = [1, 50, 50]
        out = _calculate_easy_days_modifiers(easy, weekdays, counts)
        assert out[0] == _EASY_NORMAL

    def test_reduced_day_over_threshold_becomes_minimum(self):
        # A Reduced day carrying most of the load exceeds the threshold → Minimum.
        easy = [_EASY_REDUCED] + [_EASY_NORMAL] * 6
        weekdays = [0, 1, 2]
        counts = [100, 1, 1]
        out = _calculate_easy_days_modifiers(easy, weekdays, counts)
        assert out[0] == _EASY_MINIMUM


class TestSiblingModifiers:
    def test_no_note_id_yields_all_ones(self):
        days = [_LoadBalancerDay() for _ in range(LOAD_BALANCE_DAYS)]
        assert _calculate_sibling_modifiers(days, 10, 14, None) == [1.0] * 5

    def test_sibling_day_pulls_neighbours_down(self):
        days = [_LoadBalancerDay() for _ in range(LOAD_BALANCE_DAYS)]
        days[12].add(1, 100)  # a sibling of note 100 sits on day 12
        mods = _calculate_sibling_modifiers(days, 10, 14, 100)
        # day 12 (index 2 in [10..14]) is the sibling day → ~0 weight; neighbours reduced.
        assert mods[2] < 0.001
        assert mods[1] < 1.0 and mods[3] < 1.0
        assert mods[0] < 1.0 and mods[4] < 1.0


class TestF32Powi3:
    def test_cube(self):
        # (1/4)^3 == 1/64 in f32
        assert abs(_f32_powi3(0.25) - 0.015625) < 1e-9


class TestSelectWeightedInterval:
    def test_zero_count_uniform_pick_in_range(self):
        intervals = [_LoadBalancerInterval(t, 0, 1.0, 1.0) for t in range(20, 25)]
        pick = _select_weighted_interval(intervals, 12345)
        assert pick in range(20, 25)

    def test_heavy_load_avoided(self):
        # All days heavily loaded except day 22 (empty) → balancer strongly prefers 22.
        intervals = []
        for t in range(20, 25):
            intervals.append(_LoadBalancerInterval(t, 0 if t == 22 else 500, 1.0, 1.0))
        # Across many seeds the empty day dominates.
        picks = [_select_weighted_interval(intervals, s) for s in range(200)]
        assert picks.count(22) > 150


class TestLoadBalancerHistogram:
    def test_add_card_out_of_range_ignored(self):
        lb = LoadBalancer(None, 1779534000)
        lb.add_card(1, 100, LOAD_BALANCE_DAYS)  # >= 99 → ignored
        lb.add_card(2, 100, -1)  # negative → ignored
        assert all(len(d.cards) == 0 for d in lb.days)

    def test_add_and_remove_card(self):
        lb = LoadBalancer(None, 1779534000)
        lb.add_card(1, 100, 5)
        assert len(lb.days[5].cards) == 1
        lb.remove_card(1)
        assert len(lb.days[5].cards) == 0

    def test_easy_days_percentages_parsed(self):
        lb = LoadBalancer([1.0, 0.0, 0.5, 1.0, 1.0, 1.0, 1.0], 1779534000)
        assert lb.easy_days == [
            _EASY_NORMAL,
            _EASY_MINIMUM,
            _EASY_REDUCED,
            _EASY_NORMAL,
            _EASY_NORMAL,
            _EASY_NORMAL,
            _EASY_NORMAL,
        ]

    def test_empty_easy_days_defaults_normal(self):
        lb = LoadBalancer(None, 1779534000)
        assert lb.easy_days == [_EASY_NORMAL] * 7


class TestFindInterval:
    def test_far_future_interval_skips_balancing(self):
        lb = LoadBalancer(None, 1779534000)
        assert lb.find_interval(91.0, 1, 36500, 12345, None) is None

    def test_high_minimum_skips_balancing(self):
        lb = LoadBalancer(None, 1779534000)
        assert lb.find_interval(50.0, 91, 36500, 12345, None) is None

    def test_empty_histogram_returns_in_range(self):
        # Golden: validated via the Anki oracle sweep.
        lb = LoadBalancer(None, 1779534000)
        assert lb.find_interval(60.0, 33, 36500, 1775264031881, None) == 55

    def test_picks_valley_day(self):
        # Days 55..69 loaded except the valley at 60 → balancer lands on 60.
        lb = LoadBalancer(None, 1779534000)
        for off in range(55, 70):
            if off != 60:
                for k in range(50):
                    lb.add_card(900000 + off * 100 + k, 800000 + off * 100 + k, off)
        assert lb.find_interval(64.0, 33, 36500, 1775264031881, None) == 60


class TestBuryReviewsGating:
    """Anki only feeds note_id to the balancer when bury_reviews is enabled
    (answering/mod.rs:247, `.then_some(note_id)`). With bury_reviews off the
    sibling modifier must be inert — find_interval(note_id=N) == find_interval(None).
    """

    _SEED = 1775264031881
    _SIBLING_NOTE = 999

    def _loaded(self, *, bury_reviews: bool) -> LoadBalancer:
        # Same valley-at-60 histogram as test_picks_valley_day, plus a lone
        # sibling of note 999 sitting on the valley day.
        lb = LoadBalancer(None, 1779534000, bury_reviews=bury_reviews)
        for off in range(55, 70):
            if off != 60:
                for k in range(50):
                    lb.add_card(900000 + off * 100 + k, 800000 + off * 100 + k, off)
        lb.add_card(123456, self._SIBLING_NOTE, 60)
        return lb

    def test_default_bury_reviews_is_true(self):
        assert LoadBalancer(None, 1779534000).bury_reviews is True

    def test_bury_off_ignores_note_id(self):
        lb = self._loaded(bury_reviews=False)
        with_note = lb.find_interval(64.0, 33, 36500, self._SEED, self._SIBLING_NOTE)
        without_note = lb.find_interval(64.0, 33, 36500, self._SEED, None)
        assert with_note == without_note

    def test_bury_on_uses_note_id(self):
        # Guards against the gating test above being vacuous: when bury is on the
        # sibling on day 60 steers the pick off the valley.
        lb = self._loaded(bury_reviews=True)
        with_note = lb.find_interval(64.0, 33, 36500, self._SEED, self._SIBLING_NOTE)
        without_note = lb.find_interval(64.0, 33, 36500, self._SEED, None)
        assert with_note != without_note


class TestFuzzPipelineHooks:
    """The balancer is threaded into both fuzz pipelines in fsrs.py."""

    def _raws(self):
        p = DEFAULT_FSRS5_PARAMS
        s_h = _quantize_stability(10.0)
        s_g = _quantize_stability(20.0)
        s_e = _quantize_stability(40.0)
        return (
            _next_interval_raw(s_h, p.desired_retention, -p.decay),
            _next_interval_raw(s_g, p.desired_retention, -p.decay),
            _next_interval_raw(s_e, p.desired_retention, -p.decay),
        )

    def test_passing_uses_balancer_when_in_range(self):
        rh, rg, re = self._raws()
        lb = LoadBalancer(None, 1779534000)
        # Heavy load everywhere in a band except a valley, so the balanced pick
        # differs from the pure-fuzz pick for at least one rating.
        for off in range(1, 99):
            for k in range(40):
                lb.add_card(700000 + off * 100 + k, 600000 + off * 100 + k, off)
        with_lb = _passing_intervals_with_fuzz(rh, rg, re, 5, 1775264031881, 21, 36500, load_balancer=lb, note_id=None)
        without = _passing_intervals_with_fuzz(rh, rg, re, 5, 1775264031881, 21, 36500)
        assert all(isinstance(x, int) for x in with_lb)
        # Balanced result stays within the same fuzz window as pure fuzz, but the
        # load steers the pick, so the tuples differ.
        assert with_lb != without

    def test_passing_falls_back_to_fuzz_when_out_of_range(self):
        # raw intervals far beyond MAX_LOAD_BALANCE_INTERVAL → find_interval None → pure fuzz.
        lb = LoadBalancer(None, 1779534000)
        with_lb = _passing_intervals_with_fuzz(200.0, 400.0, 800.0, 50, 1775264031881, 21, 36500, load_balancer=lb)
        without = _passing_intervals_with_fuzz(200.0, 400.0, 800.0, 50, 1775264031881, 21, 36500)
        assert with_lb == without

    def test_graduation_uses_balancer_when_in_range(self):
        rh, rg, re = self._raws()
        lb = LoadBalancer(None, 1779534000)
        for off in range(1, 99):
            for k in range(40):
                lb.add_card(500000 + off * 100 + k, 400000 + off * 100 + k, off)
        with_lb = _graduation_intervals_with_fuzz(rh, rg, re, 1775264031881, 5, 36500, load_balancer=lb, note_id=None)
        without = _graduation_intervals_with_fuzz(rh, rg, re, 1775264031881, 5, 36500)
        assert all(isinstance(x, int) for x in with_lb)
        assert with_lb != without

    def test_graduation_falls_back_when_out_of_range(self):
        lb = LoadBalancer(None, 1779534000)
        with_lb = _graduation_intervals_with_fuzz(200.0, 400.0, 800.0, 1775264031881, 5, 36500, load_balancer=lb)
        without = _graduation_intervals_with_fuzz(200.0, 400.0, 800.0, 1775264031881, 5, 36500)
        assert with_lb == without
