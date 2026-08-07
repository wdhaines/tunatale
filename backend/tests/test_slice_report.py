"""Unit tests for the per-chunk slicing diagnostic (scripts/slice_report.py).

The tool exists because two attempts to shorten the chunk tail were rejected by
ear with no measurement to argue with. Every number it prints must therefore
come from the production code path (``app.audio.slicing.tail_length``), never
from a re-implementation of the formula — that is what these tests pin.
"""
# ruff: noqa: I001 — import from scripts/ needs sys.path.insert before it

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

from app.audio.slicing import SlicedWord

# Allow importing from scripts/ one level up.
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

from slice_report import _print_table, build_rows, main  # noqa: E402

_RATE = 16000
_VOWELS = frozenset("aeiouyæøå")


def _ms(milliseconds: float) -> int:
    return int(milliseconds / 1000.0 * _RATE)


def _word(syllables: list[str], *, headroom_ms: float) -> SlicedWord:
    """A 2-syllable word whose measured vowel headroom is exactly *headroom_ms*."""
    boundary, total = _ms(400.0), _ms(1000.0)
    return SlicedWord(
        word="".join(syllables),
        syllables=syllables,
        samples=np.zeros(total, dtype=np.float32),
        rate=_RATE,
        bounds=[0, boundary, total],
        onset_ends=[boundary + _ms(headroom_ms)],
    )


class TestBuildRows:
    def test_one_row_per_interior_chunk(self):
        """The final chunk has no tail, so it is not a row."""
        sw = _word(["opp", "klart"], headroom_ms=60.0)
        rows = build_rows(sw, _VOWELS, tail_pad=_ms(80.0))
        assert len(rows) == 1
        assert (rows[0].syllable, rows[0].next_syllable) == ("opp", "klart")

    def test_onset_is_the_leading_consonants_of_the_next_syllable(self):
        """Leading, not "every consonant" — the ``t`` of ``et`` is a coda, not an onset."""
        rows = build_rows(_word(["opp", "klart"], headroom_ms=60.0), _VOWELS, tail_pad=_ms(80.0))
        assert rows[0].next_onset == "kl"
        rows = build_rows(_word(["team", "et"], headroom_ms=0.0), _VOWELS, tail_pad=_ms(80.0))
        assert rows[0].next_onset == ""

    def test_overshoot_is_how_far_the_tail_runs_past_the_next_vowel(self):
        """60 ms of headroom + the 40 ms vowel overlap = 40 ms inside the next vowel."""
        rows = build_rows(_word(["had", "de"], headroom_ms=60.0), _VOWELS, tail_pad=_ms(80.0))
        row = rows[0]
        assert row.to_vowel_ms == 60.0
        assert row.tail_ms == 100.0
        assert row.overshoot_ms == 40.0

    def test_flags_a_tail_pinned_at_the_floor(self):
        """The floor overruling a real measurement is the thing worth seeing."""
        rows = build_rows(_word(["no", "en"], headroom_ms=0.0), _VOWELS, tail_pad=_ms(80.0))
        row = rows[0]
        assert row.at_floor is True
        assert row.tail_ms == 80.0
        assert row.overshoot_ms == 80.0

    def test_flags_a_measurement_that_is_zero_by_construction(self):
        """onset_ends lands ON the cut when the next syllable is vowel-initial.

        That is not "the vowel starts immediately", it is "no measurement was
        taken" — and a reader who cannot tell them apart will tune against noise.
        """
        degenerate = build_rows(_word(["no", "en"], headroom_ms=0.0), _VOWELS, tail_pad=_ms(80.0))
        measured = build_rows(_word(["ha", "gen"], headroom_ms=100.0), _VOWELS, tail_pad=_ms(80.0))
        assert degenerate[0].degenerate is True
        assert measured[0].degenerate is False

    def test_tail_ms_comes_from_the_production_formula(self):
        """The 100 ms headroom cap is applied — proof the row is not re-deriving it."""
        rows = build_rows(_word(["ha", "gen"], headroom_ms=150.0), _VOWELS, tail_pad=_ms(80.0))
        assert rows[0].tail_ms == 140.0


# (word, cut, expected geminate, expected predicted_band) — 25 rows.
_ORACLE = [
    ("aldri", "al|dri", False, "bad"),
    ("bilde", "bil|de", False, "bad"),
    ("endte", "end|te", False, "bad"),
    ("fordi", "for|di", False, "bad"),
    ("forklare", "for|kla", False, "bad"),
    ("fulgte", "fulg|te", False, "bad"),
    ("hagen", "ha|gen", False, "bad"),
    ("huset", "hu|set", False, "bad"),
    ("hvorfor", "hvor|for", False, "bad"),
    ("ingen", "in|gen", False, "bad"),
    ("nederst", "ne|derst", False, "bad"),
    ("oppklart", "opp|klart", False, "bad"),
    ("plaget", "pla|get", False, "bad"),
    ("snømann", "snø|mann", False, "bad"),
    ("sporet", "spo|ret", False, "bad"),
    ("dekket", "dek|ket", True, "mild"),
    ("hadde", "had|de", True, "mild"),
    ("ikke", "ik|ke", True, "mild"),
    ("kunne", "kun|ne", True, "mild"),
    ("mappen", "map|pen", True, "mild"),
    ("skuffen", "skuf|fen", True, "mild"),
    ("snudde", "snud|de", True, "mild"),
    ("noe", "no|e", False, "worst"),
    ("noen", "no|en", False, "worst"),
    ("snøen", "snø|en", False, "worst"),
]


class TestPredictedBand:
    def test_ear_verdict_oracle(self):
        """These are the user's recorded ear verdicts, 25 chunks judged by ear
        across two lessons with predictions recorded before each batch.

        Every row is a live value from the user's alignment cache; a failure here
        means the classifier broke — not that a fixture drifted. The synthetic
        syllables are cut on the ``|`` so ``build_rows`` derives the same
        ``next_onset`` the real record yields.
        """
        for word, cut, geminate, band in _ORACLE:
            syl1, syl2 = cut.split("|")
            row = build_rows(_word([syl1, syl2], headroom_ms=60.0), _VOWELS, tail_pad=_ms(80.0))[0]
            assert row.geminate is geminate, (word, row.geminate)
            assert row.predicted_band == band, (word, row.predicted_band)

    def test_geminate_check_is_case_insensitive(self):
        """``Ik|ke`` is mild; an uppercase final character matches too."""
        assert (
            build_rows(_word(["Ik", "ke"], headroom_ms=60.0), _VOWELS, tail_pad=_ms(80.0))[0].predicted_band == "mild"
        )
        assert (
            build_rows(_word(["iK", "ke"], headroom_ms=60.0), _VOWELS, tail_pad=_ms(80.0))[0].predicted_band == "mild"
        )

    def test_one_character_chunk_whose_sole_character_is_the_geminate(self):
        """``d|de``: the whole chunk is the geminate — still mild."""
        row = build_rows(_word(["d", "de"], headroom_ms=60.0), _VOWELS, tail_pad=_ms(80.0))[0]
        assert row.geminate is True
        assert row.predicted_band == "mild"

    def test_worst_wins_when_onset_is_empty(self):
        """A vowel-initial next syllable is worst regardless of geminate."""
        row = build_rows(_word(["no", "e"], headroom_ms=0.0), _VOWELS, tail_pad=_ms(80.0))[0]
        assert row.next_onset == ""
        assert row.predicted_band == "worst"

    def test_json_output_contains_both_new_keys(self, tmp_path, capsys):
        """The --json path serialises the dataclass via asdict — verify, don't assume."""
        (tmp_path / "one.json").write_text(
            json.dumps(
                {
                    "syllables": ["ik", "ke"],
                    "n_samples": 24000,
                    "bounds": [0, 9600, 24000],
                    "onset_ends": [11040],
                }
            ),
            encoding="utf-8",
        )
        assert main(["--cache-dir", str(tmp_path), "--language", "no", "--json"]) == 0
        data = json.loads(capsys.readouterr().out)
        assert data[0]["geminate"] is True
        assert data[0]["predicted_band"] == "mild"

    def test_print_table_has_gem_and_band_headers(self, capsys):
        """The table names both new columns."""
        rows = build_rows(_word(["had", "de"], headroom_ms=60.0), _VOWELS, tail_pad=_ms(80.0))
        _print_table(rows)
        out = capsys.readouterr().out
        assert "gem" in out
        assert "band" in out
