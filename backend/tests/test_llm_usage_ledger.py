"""Tests for the file-backed LLM token-usage ledger.

Groq's free-tier daily token cap (~100k TPD for gpt-oss-120b) is the binding
limit but appears in NO response header — the only way to show "how we're doing
vs. the day budget" is to count what we spent ourselves. The ledger persists to
a file so the count survives uvicorn --reload restarts (which happen on every
code edit in dev).
"""

from app.llm.usage_ledger import UsageLedger


class TestUsageLedger:
    def test_record_and_sum(self, tmp_path):
        ledger = UsageLedger(tmp_path / "usage.log")
        ledger.record(100, now=1_000.0)
        ledger.record(50, now=2_000.0)
        assert ledger.tokens_used_last_24h(now=2_000.0) == 150

    def test_entries_older_than_24h_excluded(self, tmp_path):
        ledger = UsageLedger(tmp_path / "usage.log")
        ledger.record(100, now=1_000.0)
        ledger.record(50, now=1_000.0 + 86_401)
        assert ledger.tokens_used_last_24h(now=1_000.0 + 86_401) == 50

    def test_persists_across_instances(self, tmp_path):
        path = tmp_path / "usage.log"
        UsageLedger(path).record(100, now=1_000.0)
        assert UsageLedger(path).tokens_used_last_24h(now=1_000.0) == 100

    def test_missing_file_sums_to_zero(self, tmp_path):
        ledger = UsageLedger(tmp_path / "does-not-exist.log")
        assert ledger.tokens_used_last_24h(now=1_000.0) == 0

    def test_creates_parent_directory(self, tmp_path):
        ledger = UsageLedger(tmp_path / "nested" / "dir" / "usage.log")
        ledger.record(10, now=1_000.0)
        assert UsageLedger(tmp_path / "nested" / "dir" / "usage.log").tokens_used_last_24h(now=1_000.0) == 10

    def test_corrupt_lines_skipped(self, tmp_path):
        path = tmp_path / "usage.log"
        path.write_text("garbage\n1000.0 not-a-number\n\n1000.0 25\n")
        ledger = UsageLedger(path)
        assert ledger.tokens_used_last_24h(now=1_000.0) == 25

    def test_prune_drops_stale_entries_and_rewrites_file(self, tmp_path):
        path = tmp_path / "usage.log"
        ledger = UsageLedger(path, max_entries=3)
        ledger.record(1, now=0.0)
        ledger.record(2, now=1.0)
        ledger.record(3, now=90_000.0)
        ledger.record(4, now=90_001.0)  # 4th entry exceeds max_entries → prune
        # Entries at t=0/t=1 are outside the 24h window of t=90_001 and get dropped
        # from the rewritten file; a fresh instance sees only the recent ones.
        fresh = UsageLedger(path)
        assert fresh.tokens_used_last_24h(now=90_001.0) == 7
        assert len(path.read_text().strip().splitlines()) == 2

    def test_defaults_now_to_wall_clock(self, tmp_path):
        ledger = UsageLedger(tmp_path / "usage.log")
        ledger.record(42)
        assert ledger.tokens_used_last_24h() == 42
