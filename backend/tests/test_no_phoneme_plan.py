"""Norwegian phrase planner (stage 2c): per-token IPA mapping for whole phrases.

Tests the PhonemePlanner Protocol, the NorwegianPhonemePlanner implementation,
and the get_phoneme_planner registry accessor. All fixtures are tiny in-test
lexicons — the committed extract and built database are never touched.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol, runtime_checkable

import pytest

from app.languages import (
    get_phoneme_planner,
)
from app.plugins.languages.no.lexicon import (
    NstLexicon,
    build_lexicon_db,
)
from app.plugins.languages.no.phoneme_plan import NorwegianPhonemePlanner

# Real-format fixture rows (from test_no_lexicon.py).
FIXTURE_ROWS = [
    ("hei", "IN", '"h@I', 1),
    ("hagen", "NN", '""hA:$g@n', 1),
    ("snømann", "NN", '""sn2:$%mAn', 2),
    ("galt", "NN", '"gAl', 2),
    ("galt", "VB", '"gAl', 1),
    ("huset", "NN", '"hu:s@t', 2),
    ("huset", "VB", '"hy:s@', 1),
    ("testord", "NN", '"tA:', 1),
    ("testord", "VB", '"te:s', 1),
]


def _make_db(tmp_path: Path, rows: list[tuple[str, str, str, int]] = FIXTURE_ROWS) -> Path:
    gz = tmp_path / "fixture.tsv.gz"
    payload = "".join(f"{w}\t{p}\t{s}\t{c}\n" for w, p, s, c in rows)
    gz.write_bytes(__import__("gzip").compress(payload.encode("utf-8"), mtime=0))
    db = tmp_path / "lexicon.sqlite3"
    build_lexicon_db(gz, db)
    return db


def _lex(tmp_path: Path, rows: list[tuple[str, str, str, int]] = FIXTURE_ROWS) -> NstLexicon:
    return NstLexicon(_make_db(tmp_path, rows))


def _planner(tmp_path: Path, rows: list[tuple[str, str, str, int]] = FIXTURE_ROWS) -> NorwegianPhonemePlanner:
    db = _make_db(tmp_path, rows)
    return NorwegianPhonemePlanner(db)


class TestPhonemePlannerProtocol:
    """The Protocol is satisfied by NorwegianPhonemePlanner."""

    @runtime_checkable
    class _PhonemePlanner(Protocol):
        def plan(self, text: str) -> Mapping[str, str] | None: ...

    def test_satisfies_protocol(self, tmp_path: Path) -> None:
        p = _planner(tmp_path)
        assert isinstance(p, self._PhonemePlanner)


class TestPlanResolves:
    """plan() returns the mapping for an all-resolvable phrase."""

    def test_single_word(self, tmp_path: Path) -> None:
        p = _planner(tmp_path)
        result = p.plan("hagen")
        assert result is not None
        assert result == {"hagen": "hɑː.gən"}

    def test_multi_word_all_resolved(self, tmp_path: Path) -> None:
        rows = [
            ("hei", "IN", '"h@I', 1),
            ("hagen", "NN", '""hA:$g@n', 1),
        ]
        p = _planner(tmp_path, rows)
        result = p.plan("hei hagen")
        assert result is not None
        assert result == {"hei": "ˈhəɪ", "hagen": "hɑː.gən"}


class TestAllOrNothing:
    """Any non-RESOLVED outcome sinks the whole phrase."""

    def test_ambiguous_no_pos_sinks(self, tmp_path: Path) -> None:
        """galt: AMBIGUOUS_NO_POS (2 entries, 2 readings, no POS)."""
        p = _planner(tmp_path)
        assert p.plan("hagen og galt") is None

    def test_absent_sinks(self, tmp_path: Path) -> None:
        p = _planner(tmp_path)
        assert p.plan("hagen og zzqqxx") is None


class TestUnknownSegmentError:
    """UnknownSegmentError from sampa_to_ipa sinks the phrase."""

    def test_unknown_segment_sinks(self, tmp_path: Path) -> None:
        rows = [("badword", "NN", '"xXxX', 1)]
        p = _planner(tmp_path, rows)
        assert p.plan("badword") is None


class TestToneStripping:
    """Tone marks are stripped; primary/secondary stress survive."""

    def test_tone2_stripped(self, tmp_path: Path) -> None:
        """snømann has tone-2 mark ("); it must not appear in IPA."""
        p = _planner(tmp_path)
        result = p.plan("snømann")
        assert result is not None
        ipa = result["snømann"]
        assert '"' not in ipa

    def test_primary_stress_survives(self, tmp_path: Path) -> None:
        rows = [("test", "NN", '"t"A:', 1)]
        p = _planner(tmp_path, rows)
        result = p.plan("test")
        assert result is not None
        assert "ˈ" in result["test"]

    def test_secondary_stress_survives(self, tmp_path: Path) -> None:
        rows = [("test", "NN", 't"A:%', 1)]
        p = _planner(tmp_path, rows)
        result = p.plan("test")
        assert result is not None
        assert "ˌ" in result["test"]


class TestTokenisation:
    """Tokeniser ignores digits, punctuation, underscores."""

    def test_digits_ignored(self, tmp_path: Path) -> None:
        p = _planner(tmp_path)
        # "hagen 123" → tokens: ["hagen"]
        result = p.plan("hagen 123")
        assert result is not None
        assert "123" not in result
        assert result == {"hagen": "hɑː.gən"}

    def test_punctuation_ignored(self, tmp_path: Path) -> None:
        p = _planner(tmp_path)
        result = p.plan("hei, hagen!")
        assert result is not None
        assert result == {"hei": "ˈhəɪ", "hagen": "hɑː.gən"}

    def test_underscores_ignored(self, tmp_path: Path) -> None:
        p = _planner(tmp_path)
        result = p.plan("hei_hagen")
        assert result is not None
        assert result == {"hei": "ˈhəɪ", "hagen": "hɑː.gən"}


class TestRepeatedToken:
    """A repeated token appears once in the mapping."""

    def test_dedup(self, tmp_path: Path) -> None:
        p = _planner(tmp_path)
        result = p.plan("hagen hagen")
        assert result is not None
        assert result == {"hagen": "hɑː.gən"}
        assert len(result) == 1


class TestCaseInsensitive:
    """Lookup is case-insensitive; mapping is keyed lowercase."""

    def test_capitalised_input(self, tmp_path: Path) -> None:
        p = _planner(tmp_path)
        result = p.plan("Hagen")
        assert result is not None
        assert result == {"hagen": "hɑː.gən"}

    def test_mixed_case(self, tmp_path: Path) -> None:
        p = _planner(tmp_path)
        result = p.plan("HAGEN")
        assert result is not None
        assert "hagen" in result


class TestEmptyInput:
    """Empty or whitespace-only input returns None."""

    def test_empty_string(self, tmp_path: Path) -> None:
        p = _planner(tmp_path)
        assert p.plan("") is None

    def test_whitespace_only(self, tmp_path: Path) -> None:
        p = _planner(tmp_path)
        assert p.plan("   ") is None


class TestRegistry:
    """get_phoneme_planner returns None for languages without a planner."""

    def test_no_planner_for_en(self) -> None:
        assert get_phoneme_planner("en") is None

    def test_no_planner_for_sl(self) -> None:
        assert get_phoneme_planner("sl") is None

    def test_no_planner_for_unknown(self) -> None:
        assert get_phoneme_planner("xx") is None

    def test_returns_planner_for_no(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Norwegian returns a planner when the lexicon is available."""
        db = _make_db(tmp_path)
        import app.plugins.languages.no.lexicon as lex_mod

        monkeypatch.setattr(lex_mod, "DB_PATH", db)
        planner = get_phoneme_planner("no")
        assert planner is not None
        assert isinstance(planner, NorwegianPhonemePlanner)


class TestLexiconIsOpenedOnce:
    """One lexicon per planner — ``plan`` is called once per phrase."""

    def test_second_plan_reuses_the_same_lexicon(self, tmp_path: Path) -> None:
        """A lesson plans hundreds of phrases; each must not reopen the database.

        Asserted by object identity rather than by counting constructor calls,
        because patching ``NstLexicon`` would be a mock of app code.
        """
        p = _planner(tmp_path)
        assert p.plan("hagen") == {"hagen": "h\u0251\u02d0.g\u0259n"}
        first = p._lexicon
        assert first is not None

        assert p.plan("hei") is not None
        assert p._lexicon is first, "the planner reopened the lexicon on the second phrase"


class TestUnbuiltLexicon:
    """An unbuilt lexicon makes plan return None and warn, never raise."""

    def test_unbuilt_returns_none(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        missing_db = tmp_path / "nonexistent.sqlite3"
        p = NorwegianPhonemePlanner(missing_db)
        with caplog.at_level(logging.WARNING):
            result = p.plan("hagen")
        assert result is None
        assert any(
            "not built" in rec.message.lower() or "missing" in rec.message.lower() or "nst" in rec.message.lower()
            for rec in caplog.records
        )

    def test_plan_returns_none_not_raise(self, tmp_path: Path) -> None:
        missing_db = tmp_path / "nonexistent.sqlite3"
        p = NorwegianPhonemePlanner(missing_db)
        # Must not raise — all calls return None
        assert p.plan("anything") is None
        assert p.plan("something else") is None
