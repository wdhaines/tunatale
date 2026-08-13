"""Tests for the file-backed LLM usage ledger.

Groq's free-tier daily token cap (200k TPD for gpt-oss-120b) is the binding
limit but appears in NO response header — the only way to show "how we're doing
vs. the day budget" is to count what we spent ourselves. The ledger persists to
a file so the count survives uvicorn --reload restarts (which happen on every
code edit in dev).

The model is a LEAKY BUCKET, not a rolling sum and not a calendar day — see the
module docstring for the header measurement that settled it. Every timestamp
below is an ABSOLUTE constant: a ledger seeded at "now − 1h" passes or fails
depending on the hour the suite runs.
"""

import pytest

from app.llm.usage_ledger import DAY_S, UsageLedger

# 2023-11-14T22:13:20Z. Any fixed instant works; what matters is that it is not
# derived from time.time().
T0 = 1_700_000_000.0

# Chosen so the arithmetic is exact and readable: at a 200k/day limit the bucket
# refills 200_000/86_400 = 2.3148…/s, i.e. a 100k spend drains in exactly half a
# day and 50k of it comes back in exactly a quarter.
TOKEN_LIMIT = 200_000
REQUEST_LIMIT = 1_000


class TestUsageLedger:
    def test_record_and_sum(self, tmp_path):
        ledger = UsageLedger(tmp_path / "usage.log")
        ledger.record(100, now=T0)
        ledger.record(50, now=T0)
        assert ledger.tokens_used(TOKEN_LIMIT, now=T0) == 150

    def test_persists_across_instances(self, tmp_path):
        path = tmp_path / "usage.log"
        UsageLedger(path).record(100, now=T0)
        assert UsageLedger(path).tokens_used(TOKEN_LIMIT, now=T0) == 100

    def test_missing_file_sums_to_zero(self, tmp_path):
        ledger = UsageLedger(tmp_path / "does-not-exist.log")
        assert ledger.tokens_used(TOKEN_LIMIT, now=T0) == 0

    def test_creates_parent_directory(self, tmp_path):
        ledger = UsageLedger(tmp_path / "nested" / "dir" / "usage.log")
        ledger.record(10, now=T0)
        assert UsageLedger(tmp_path / "nested" / "dir" / "usage.log").tokens_used(TOKEN_LIMIT, now=T0) == 10

    def test_corrupt_lines_skipped(self, tmp_path):
        path = tmp_path / "usage.log"
        path.write_text("garbage\n1700000000.0 not-a-number\n\n1700000000.0 25\n")
        ledger = UsageLedger(path)
        assert ledger.tokens_used(TOKEN_LIMIT, now=T0) == 25

    def test_defaults_now_to_wall_clock(self, tmp_path):
        ledger = UsageLedger(tmp_path / "usage.log")
        ledger.record(42)
        assert ledger.tokens_used(TOKEN_LIMIT) == 42


class TestLeakyBucketDrain:
    """Groq refills continuously; spend does not sit still for 24h and then vanish.

    Measured against the live API 2026-08-13 — see the module docstring. These
    are the tests that discriminate a leaky bucket from BOTH the rolling-24h sum
    that was here before AND the fixed midnight boundary the bead assumed.
    """

    def test_spend_drains_continuously_not_in_a_step(self, tmp_path):
        ledger = UsageLedger(tmp_path / "usage.log")
        ledger.record(100_000, now=T0)
        # A rolling-24h sum would still report the full 100_000 at every point
        # below; a midnight boundary would report either 100_000 or 0 depending
        # on which side of the rollover T0 fell.
        assert ledger.tokens_used(TOKEN_LIMIT, now=T0) == 100_000
        assert ledger.tokens_used(TOKEN_LIMIT, now=T0 + DAY_S / 8) == 75_000
        assert ledger.tokens_used(TOKEN_LIMIT, now=T0 + DAY_S / 4) == 50_000
        assert ledger.tokens_used(TOKEN_LIMIT, now=T0 + DAY_S / 2) == 0

    def test_refill_rate_matches_the_measured_header(self, tmp_path):
        """One request costs exactly 86.4s of RPD recovery at a 1000/day limit."""
        ledger = UsageLedger(tmp_path / "usage.log")
        ledger.record(0, now=T0)
        assert ledger.requests_used(REQUEST_LIMIT, now=T0) == 1
        assert ledger.requests_used(REQUEST_LIMIT, now=T0 + 86.3) == 1
        assert ledger.requests_used(REQUEST_LIMIT, now=T0 + 86.4) == 0

    def test_drained_budget_does_not_bank_credit(self, tmp_path):
        """An idle week does not buy a 2x day. The bucket floors at empty."""
        ledger = UsageLedger(tmp_path / "usage.log")
        ledger.record(10_000, now=T0)
        ledger.record(10_000, now=T0 + 7 * DAY_S)
        assert ledger.tokens_used(TOKEN_LIMIT, now=T0 + 7 * DAY_S) == 10_000

    def test_partial_drain_between_spends_accumulates(self, tmp_path):
        ledger = UsageLedger(tmp_path / "usage.log")
        ledger.record(100_000, now=T0)
        # Quarter-day later 50k has come back, leaving 50k consumed; +100k = 150k.
        ledger.record(100_000, now=T0 + DAY_S / 4)
        assert ledger.tokens_used(TOKEN_LIMIT, now=T0 + DAY_S / 4) == 150_000

    def test_out_of_order_file_entries_are_sorted(self, tmp_path):
        """A hand-edited or interleaved log must not drain backwards."""
        path = tmp_path / "usage.log"
        path.write_text(f"{T0 + DAY_S / 4} 100000\n{T0} 100000\n")
        assert UsageLedger(path).tokens_used(TOKEN_LIMIT, now=T0 + DAY_S / 4) == 150_000

    def test_requests_counted_per_entry_regardless_of_tokens(self, tmp_path):
        """A refused/failed call still spends RPD — recorded as a 0-token entry."""
        ledger = UsageLedger(tmp_path / "usage.log")
        ledger.record(0, now=T0)
        ledger.record(0, now=T0)
        ledger.record(5_000, now=T0)
        assert ledger.requests_used(REQUEST_LIMIT, now=T0) == 3
        assert ledger.tokens_used(TOKEN_LIMIT, now=T0) == 5_000

    def test_reset_eta_is_time_until_the_bucket_is_full(self, tmp_path):
        """Same meaning as Groq's own x-ratelimit-reset-* headers."""
        ledger = UsageLedger(tmp_path / "usage.log")
        ledger.record(100_000, now=T0)
        assert ledger.tokens_reset_in_s(TOKEN_LIMIT, now=T0) == pytest.approx(DAY_S / 2)
        assert ledger.tokens_reset_in_s(TOKEN_LIMIT, now=T0 + DAY_S / 4) == pytest.approx(DAY_S / 4)
        assert ledger.tokens_reset_in_s(TOKEN_LIMIT, now=T0 + DAY_S) == 0.0

    def test_requests_reset_eta(self, tmp_path):
        ledger = UsageLedger(tmp_path / "usage.log")
        ledger.record(0, now=T0)
        ledger.record(0, now=T0)
        assert ledger.requests_reset_in_s(REQUEST_LIMIT, now=T0) == pytest.approx(172.8)

    def test_prune_drops_fully_drained_entries_and_rewrites_file(self, tmp_path):
        path = tmp_path / "usage.log"
        ledger = UsageLedger(path, max_entries=3)
        ledger.record(1, now=T0)
        ledger.record(2, now=T0 + 1)
        ledger.record(3, now=T0 + DAY_S + 1)
        ledger.record(4, now=T0 + DAY_S + 2)  # 4th entry exceeds max_entries → prune
        # A single completion is capped at TPM (8k), which fully drains in ~1h at
        # the 200k/day rate, so an entry older than a full day contributes
        # nothing and is safe to drop.
        fresh = UsageLedger(path)
        assert len(path.read_text().strip().splitlines()) == 2
        # The two survivors are 1s apart, so the first has already drained by
        # 1 × 200_000/86_400 when the second lands: 3 − 2.3148 + 4 = 4.685 → 5.
        # NB 7 would be the plain in-window SUM — i.e. the rolling-window answer
        # this model replaces. Asserting 7 here is how the old test read, and it
        # is the wrong oracle for a bucket.
        assert fresh.tokens_used(TOKEN_LIMIT, now=T0 + DAY_S + 2) == 5


class TestBudgetStatus:
    """The single call the status endpoint and the client refusal both use."""

    def test_reports_both_dimensions(self, tmp_path):
        ledger = UsageLedger(tmp_path / "usage.log")
        ledger.record(5_000, now=T0)
        status = ledger.budget(tokens_limit=TOKEN_LIMIT, requests_limit=REQUEST_LIMIT, now=T0)
        assert status.tokens_used == 5_000
        assert status.tokens_limit == TOKEN_LIMIT
        assert status.requests_used == 1
        assert status.requests_limit == REQUEST_LIMIT
        assert status.exceeded is None

    def test_token_ceiling_alone_trips_it(self, tmp_path):
        ledger = UsageLedger(tmp_path / "usage.log")
        ledger.record(TOKEN_LIMIT, now=T0)
        status = ledger.budget(tokens_limit=TOKEN_LIMIT, requests_limit=REQUEST_LIMIT, now=T0)
        assert status.requests_used == 1  # nowhere near the request ceiling
        assert status.exceeded == "tokens per day"
        assert status.reset_in_s == pytest.approx(DAY_S)

    def test_request_ceiling_alone_trips_it(self, tmp_path):
        """Many tiny completions: the token budget still reads healthy."""
        ledger = UsageLedger(tmp_path / "usage.log")
        for _ in range(REQUEST_LIMIT):
            ledger.record(1, now=T0)
        status = ledger.budget(tokens_limit=TOKEN_LIMIT, requests_limit=REQUEST_LIMIT, now=T0)
        assert status.tokens_used == 1_000  # 0.5% of the token budget
        assert status.exceeded == "requests per day"
        assert status.reset_in_s == pytest.approx(DAY_S)

    def test_tokens_named_first_when_both_are_blown(self, tmp_path):
        ledger = UsageLedger(tmp_path / "usage.log")
        for _ in range(REQUEST_LIMIT):
            ledger.record(TOKEN_LIMIT, now=T0)
        status = ledger.budget(tokens_limit=TOKEN_LIMIT, requests_limit=REQUEST_LIMIT, now=T0)
        assert status.exceeded == "tokens per day"

    def test_not_exceeded_has_no_reset_eta(self, tmp_path):
        ledger = UsageLedger(tmp_path / "usage.log")
        status = ledger.budget(tokens_limit=TOKEN_LIMIT, requests_limit=REQUEST_LIMIT, now=T0)
        assert status.exceeded is None
        assert status.reset_in_s == 0.0
