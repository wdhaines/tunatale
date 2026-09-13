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


class TestUsageSplit:
    """Token split observability (bead 6zzu2 Stage 1).

    File format is ``<ts> <total_tokens>`` (legacy; split unknown) or
    ``<ts> <total_tokens> <prompt_tokens> <completion_tokens>
    <reasoning_tokens> <call_site>``. Field 1 stays ``total_tokens`` forever,
    so old and new lines are read by identical code. A 2-field line means
    "total known, split unknown" — surfaced as ``None``, never as a guessed 0.
    """

    def test_mixed_widths_all_count_toward_budget(self, tmp_path):
        """2-field and 6-field lines interleave; every total counts toward the budget."""
        path = tmp_path / "usage.log"
        # Same timestamp → no inter-entry drain, so the bucket is a plain sum.
        path.write_text(f"{T0} 100\n{T0} 50 30 20 5 -\n{T0} 200\n{T0} 75 40 35 10 -\n")
        ledger = UsageLedger(path)
        # Budget arithmetic includes ALL totals, regardless of line width.
        assert ledger.tokens_used(TOKEN_LIMIT, now=T0) == 425
        # Only the 6-field entries carry a split; the 2-field ones are unknown.
        split = ledger.split_used(now=T0)
        assert split is not None
        assert split.prompt_tokens == 70  # 30 + 40
        assert split.completion_tokens == 55  # 20 + 35
        assert split.reasoning_tokens == 15  # 5 + 10
        assert split.prompt_tokens + split.completion_tokens == 125
        assert split.reasoning_tokens <= split.completion_tokens

    def test_two_field_only_ledger_reports_unknown_split(self, tmp_path):
        """A pure-2-field log has no split data: None, never a guessed 0."""
        ledger = UsageLedger(tmp_path / "usage.log")
        ledger.record(500, now=T0)
        assert ledger.tokens_used(TOKEN_LIMIT, now=T0) == 500
        assert ledger.split_used(now=T0) is None

    @pytest.mark.parametrize(
        ("total", "prompt", "completion", "reasoning"),
        [
            # Oracle 1 — measured live 2026-09-13 against openai/gpt-oss-120b
            (98, 78, 20, 10),  # reasoning_effort="low" (the production pin)
            (599, 99, 500, 498),  # reasoning_effort="high"
            (132, 78, 54, 44),  # no reasoning parameter at all
        ],
    )
    def test_oracle_rows_satisfy_split_invariants(self, tmp_path, total, prompt, completion, reasoning):
        """prompt + completion == total; reasoning is a subset of completion."""
        ledger = UsageLedger(tmp_path / "usage.log")
        ledger.record(
            total,
            prompt_tokens=prompt,
            completion_tokens=completion,
            reasoning_tokens=reasoning,
            now=T0,
        )
        split = ledger.split_used(now=T0)
        assert split is not None
        assert split.prompt_tokens == prompt
        assert split.completion_tokens == completion
        assert split.reasoning_tokens == reasoning
        assert split.prompt_tokens + split.completion_tokens == total
        assert split.reasoning_tokens <= split.completion_tokens

    def test_missing_completion_tokens_details_records_total_and_unknown_split(self, tmp_path):
        """A usage object with no completion_tokens_details: reasoning=None, total still recorded.

        Mirrors client.py's degrade-to-None pattern: missing or non-int fields
        become None, never 0.
        """
        ledger = UsageLedger(tmp_path / "usage.log")
        last_usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        prompt_tokens = last_usage.get("prompt_tokens")
        completion_tokens = last_usage.get("completion_tokens")
        usage_details = last_usage.get("completion_tokens_details") or {}
        reasoning_tokens = usage_details.get("reasoning_tokens")
        ledger.record(
            15,
            prompt_tokens=prompt_tokens if isinstance(prompt_tokens, int) else None,
            completion_tokens=completion_tokens if isinstance(completion_tokens, int) else None,
            reasoning_tokens=reasoning_tokens if isinstance(reasoning_tokens, int) else None,
            now=T0,
        )
        assert ledger.tokens_used(TOKEN_LIMIT, now=T0) == 15
        assert ledger.split_used(now=T0) is None

    def test_split_persists_across_instances(self, tmp_path):
        path = tmp_path / "usage.log"
        UsageLedger(path).record(98, prompt_tokens=78, completion_tokens=20, reasoning_tokens=10, now=T0)
        split = UsageLedger(path).split_used(now=T0)
        assert split is not None
        assert split.prompt_tokens == 78
        assert split.completion_tokens == 20
        assert split.reasoning_tokens == 10

    def test_split_survives_prune_rewrite(self, tmp_path):
        """The max_entries rewrite (file write-back) keeps a 6-field split."""
        path = tmp_path / "usage.log"
        ledger = UsageLedger(path, max_entries=3)
        ledger.record(100, prompt_tokens=80, completion_tokens=20, reasoning_tokens=10, now=T0)
        ledger.record(200, now=T0 + 1)  # 2-field entry
        ledger.record(300, prompt_tokens=120, completion_tokens=180, reasoning_tokens=170, now=T0 + 2)
        # Beyond the DAY_S window: the three earlier entries are pruned and the
        # rewrite must keep this entry's split.
        ledger.record(400, prompt_tokens=100, completion_tokens=300, reasoning_tokens=290, now=T0 + DAY_S + 100)
        fresh = UsageLedger(path)
        split = fresh.split_used(now=T0 + DAY_S + 100)
        assert split is not None
        assert split.prompt_tokens == 100
        assert split.completion_tokens == 300
        assert split.reasoning_tokens == 290

    def test_two_field_entry_survives_prune_rewrite_unchanged(self, tmp_path):
        """A rewritten file does not invent a split (no 'None' literals) for 2-field entries."""
        path = tmp_path / "usage.log"
        ledger = UsageLedger(path, max_entries=2)
        ledger.record(100, now=T0)
        ledger.record(200, now=T0 + 1)
        ledger.record(300, now=T0 + 100)  # exceeds max_entries → prune rewrite
        widths = [len(line.split()) for line in path.read_text().strip().splitlines()]
        assert widths == [2, 2, 2]
        assert UsageLedger(path).split_used(now=T0 + 100) is None

    def test_malformed_six_field_split_counts_total_as_unknown(self, tmp_path):
        """A 6-field line with an unparseable split still counts its total; split stays unknown."""
        path = tmp_path / "usage.log"
        path.write_text(f"{T0} 100 not-a-number not-a-number not-a-number -\n")
        ledger = UsageLedger(path)
        assert ledger.tokens_used(TOKEN_LIMIT, now=T0) == 100
        assert ledger.split_used(now=T0) is None

    def test_split_ignores_entries_older_than_the_day_window(self, tmp_path):
        """split_used sums the last DAY_S only.

        ⚠️ NOT "like the leaky bucket" — it is a flat rolling sum that does
        not drain, so it disagrees with tokens_used by design. See
        UsageLedger.split_used.
        """
        path = tmp_path / "usage.log"
        path.write_text(f"{T0} 50 40 10 5 -\n{T0 + DAY_S + 10} 20 10 10 10 -\n")
        split = UsageLedger(path).split_used(now=T0 + DAY_S + 10)
        assert split is not None
        assert split.prompt_tokens == 10  # only the in-window entry
        assert split.completion_tokens == 10
        assert split.reasoning_tokens == 10
