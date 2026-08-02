"""Unit tests for the shared checker helpers (scripts/_checker_lib.py).

These helpers are used by check_mock_boundaries.py, check_language_literals.py,
and check_date_today.py; the allowlist tests used to be copy-pasted into the
mock-boundary and language-literal checker test files and now live here once.
"""
# ruff: noqa: I001 — import from scripts/ needs sys.path.insert before it

from __future__ import annotations

import sys
from pathlib import Path

# Allow importing from scripts/ one level up.
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

from _checker_lib import load_allowlist, matches_allowlist  # noqa: E402


# ── Allowlist ─────────────────────────────────────────────────────────────────


class TestAllowlist:
    def test_load_allowlist_skips_comments_and_blanks(self, tmp_path):
        allow = tmp_path / "allow.txt"
        allow.write_text("# this is a comment\n\napp.foo.*\n  # indented comment  \napp.bar.baz\n")
        patterns = load_allowlist(allow)
        assert patterns == ["app.foo.*", "app.bar.baz"]

    def test_load_allowlist_missing_file(self, tmp_path):
        missing = tmp_path / "nope.txt"
        assert load_allowlist(missing) == []

    def test_matches_allowlist_exact_pattern_without_a_wildcard(self):
        """A wildcard-free pattern must match by equality and nothing else.

        Distinct from the glob cases below: every other pattern here carries a
        `*`, so without this the simplest live input class would be untested.
        Both real allowlists contain wildcard-free entries.

        Also pins that the helper is **notation-agnostic** — it is shared by
        check_mock_boundaries (dotted mock targets) and check_language_literals
        (slash file paths), and `fnmatch` treats both as opaque strings.
        """
        assert matches_allowlist("app/config.py", ["app/config.py"]) is True
        assert matches_allowlist("app/api/srs.py", ["app/config.py"]) is False
        assert matches_allowlist("app.srs.fsrs.schedule", ["app.srs.fsrs.schedule"]) is True

    def test_matches_allowlist_glob(self):
        patterns = ["app.audio.edge_tts.edge_tts.*", "app.config.settings.*"]
        assert matches_allowlist("app.audio.edge_tts.edge_tts.Communicate", patterns) is True
        assert matches_allowlist("app.config.settings.anki_collection_path", patterns) is True
        assert matches_allowlist("app.plugins.anki_sync.sync.main", patterns) is False

    def test_matches_allowlist_star_dot_star(self):
        patterns = ["app.*.settings.*"]
        assert matches_allowlist("app.config.settings.foo", patterns) is True
        assert matches_allowlist("app.srs.queue_stats.settings.bar", patterns) is True
        assert matches_allowlist("app.config.notsettings.foo", patterns) is False
