"""Tests for the stress-as-compound-oracle gate (tunatale-9yd0).

The NST lexicon's ``%`` (secondary-stress) mark is Norwegian's own compound
signal: a prosodic compound carries secondary stress on its second element, a
simplex word does not. ``segment_compound`` asks ``lexicon_has_secondary_stress``
and, for a word the lexicon KNOWS whose readings carry no ``%``, refuses the
heuristic over-split — a known simplex word is not a compound.

These tests pin the gate's tri-state, the ten acceptance pairs, and the five
words whose ``%`` sits only on a careful (non-finalist) reading. The tri-state
uses synthetic lexicons; everything through ``segment_compound`` uses the
committed database, exactly like ``test_norwegian_breakdown.py``.
"""

from __future__ import annotations

import gzip
from pathlib import Path

import pytest

from app.plugins.languages.no.lexicon import NstLexicon, build_lexicon_db
from app.plugins.languages.no.lexicon_syllables import lexicon_has_secondary_stress
from app.plugins.languages.no.norwegian_breakdown import (
    _is_lexicalized_whole,
    flat_syllables,
    segment_compound,
    slow_norwegian_word,
)

# Real-format rows for the synthetic tri-state lexicon: simplex has no ``%``,
# selskap carries ``%``.
_TRI_STATE_ROWS = [
    ("simplex", "NN", '"ta:l', 1),
    ("selskap", "NN", '"sel:%skA:p', 2),
]


def _make_db(tmp_path: Path, rows: list[tuple[str, str, str, int]]) -> Path:
    gz = tmp_path / "fixture.tsv.gz"
    payload = "".join(f"{w}\t{p}\t{s}\t{c}\n" for w, p, s, c in rows)
    gz.write_bytes(gzip.compress(payload.encode("utf-8"), mtime=0))
    db = tmp_path / "lexicon.sqlite3"
    build_lexicon_db(gz, db)
    return db


class TestTriState:
    def test_known_with_percent_is_true(self, tmp_path: Path) -> None:
        db = _make_db(tmp_path, _TRI_STATE_ROWS)
        assert lexicon_has_secondary_stress("selskap", db) is True

    def test_known_without_percent_is_false(self, tmp_path: Path) -> None:
        db = _make_db(tmp_path, _TRI_STATE_ROWS)
        assert lexicon_has_secondary_stress("simplex", db) is False

    def test_absent_word_is_none(self, tmp_path: Path) -> None:
        db = _make_db(tmp_path, _TRI_STATE_ROWS)
        assert lexicon_has_secondary_stress("fremmedord", db) is None

    def test_uninstalled_lexicon_is_none(self, tmp_path: Path) -> None:
        assert lexicon_has_secondary_stress("selskap", tmp_path / "missing.sqlite3") is None


# Acceptance pairs, exactly as measured 2026-09-05 (brief
# brief-bp-stress-oracle-part2-tests-2026-09-05.md Task A).
_ACCEPTANCE_PAIRS: dict[str, list[str]] = {
    "forstå": ["forstå"],
    "kalles": ["kalles"],
    "spilles": ["spilles"],
    "ekspert": ["ekspert"],
    "nettverk": ["nett", "verk"],
    "tyskland": ["tysk", "land"],
    "stortinget": ["stor", "ting", "et"],
    "samarbeid": ["sam", "arbeid"],
    "dessuten": ["dess", "ute", "n"],
    "russland": ["russ", "land"],
}


class TestAcceptancePairs:
    @pytest.mark.parametrize(("word", "expected"), sorted(_ACCEPTANCE_PAIRS.items()))
    def test_segment_compound(self, word: str, expected: list[str]) -> None:
        assert segment_compound(word) == expected


# The words rescued by reading ALL readings (tunatale-9yd0): their ``%`` sits
# only on the careful reading, which ``candidate_transcriptions`` (the certainty
# finalists) would throw away.
#
# ``arbeidende`` was a fifth entry until the ``-ende`` participle fix, which
# merges it to a single part — so the accessor no longer decides it. Removed
# rather than re-pinned: a control word that can no longer discriminate is
# decoration, and leaving it here would suggest the control is wider than it is.
_CONTROL_WORDS: dict[str, list[str]] = {
    "motstander": ["mot", "stand", "er"],
    "nedlagt": ["ned", "lag", "t"],
    "allmenne": ["all", "menn", "e"],
    "husholdninger": ["hus", "holdning", "er"],
}


class TestCarefulReadingPercent:
    @pytest.mark.parametrize(("word", "expected"), sorted(_CONTROL_WORDS.items()))
    def test_split_kept_and_depends_on_all_readings(self, word: str, expected: list[str]) -> None:
        """The gate reads ALL readings, not the finalists — the control.

        ``candidate_transcriptions`` for these words contains no ``%`` while
        ``all_transcriptions`` does. A regression that swaps the accessor back
        makes each look simplex, collapses its split, and fails this test only.
        """
        lex = NstLexicon()
        assert not any("%" in transcription for transcription in lex.candidate_transcriptions(word))
        assert any("%" in transcription for transcription in lex.all_transcriptions(word))
        assert segment_compound(word) == expected


class TestAllTranscriptionsFallback:
    def test_capitalized_word_falls_back_to_lowercase(self, tmp_path: Path) -> None:
        """The accessor retries lowercased when the exact form has no rows."""
        db = _make_db(tmp_path, _TRI_STATE_ROWS)
        lex = NstLexicon(db)
        assert lex.all_transcriptions("Selskap") == frozenset({'"sel:%skA:p'})


class TestLexicalizedWholeOverrideSurvivesTheGate:
    def test_override_short_circuits_before_rank_lookup(self) -> None:
        """tunatale-9yd0 retired the public path to this branch: ``forstand``
        and ``forbrytelsens`` are known words with no ``%``, so the gate
        returns them whole before the override is consulted — pin it directly
        so a false-merge of either word still trips this test.
        """
        assert _is_lexicalized_whole("forstand", [], {})


class TestDownstreamEffects:
    def test_slow_word_kultur_stays_whole(self) -> None:
        assert slow_norwegian_word("kultur") == "kultur"

    def test_slow_word_forsta_stays_whole(self) -> None:
        assert slow_norwegian_word("forstå") == "forstå"

    def test_flat_syllables_use_whole_word_transcription(self) -> None:
        """SECOND-ORDER effect: once a word is no longer a compound, its
        syllables come from the whole-word transcription rather than per-part
        resolution, so the retroflex cut Norwegian actually makes (``fo|rstår``,
        ``hve|ran|dre``) surfaces — tunatale-aoeu's rule 1 finally reaching
        these words.
        """
        assert flat_syllables("forstår") == ["fo", "rstår"]
        assert flat_syllables("hverandre") == ["hve", "ran", "dre"]
