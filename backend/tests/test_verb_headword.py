"""The Tagalog actor-focus infinitive rule (tunatale-w4m7.6, Part 2).

Unit-pins ``verb_headword()``'s decision branches with a stubbed frequency
table (``wordfreq.zipf_frequency``): the measured golden in
``tests/test_tagalog_lemma_table.py`` DISPLAY (41 roots) runs the REAL
frequencies end-to-end; these tests force each branch deterministically,
including the near-misses a 40/41 measurement cannot pin alone.
"""

from __future__ import annotations

import wordfreq

from app.plugins.languages.tl.verb_headword import (
    _mag,
    _overrides,
    _um,
    verb_headword,
)


def stub_zipf(monkeypatch, table: dict[str, float]) -> None:
    """Replace wordfreq's Filipino frequency lookup with *table* (absent → 0.0)."""

    def fake(form: str, lang: str) -> float:
        assert lang == "fil"
        return table.get(form, 0.0)

    monkeypatch.setattr(wordfreq, "zipf_frequency", fake)


# ── the two focus-infinitive shapes ───────────────────────────────────────────


def test_um_infixes_after_the_first_consonant():
    assert _um("kain") == "kumain"
    assert _um("balik") == "bumalik"
    assert _um("sayaw") == "sumayaw"


def test_um_prefixes_vowel_initial_roots():
    assert _um("alis") == "umalis"
    assert _um("inom") == "uminom"


def test_mag_prefixes_regular_roots():
    assert _mag("hanap") == "maghanap"
    assert _mag("sabi") == "magsabi"
    assert _mag("lakad") == "maglakad"


def test_mag_keeps_the_hyphen_before_vowel_initial_roots():
    assert _mag("aral") == "mag-aral"


# ── the lazy override table ───────────────────────────────────────────────────


def test_the_override_file_loads_lazily_with_comments_and_blanks_skipped():
    overrides = _overrides()
    assert overrides == {"kita": "makita"}
    assert "kita" in overrides  # the ONE row is the homograph root


def test_an_overridden_root_ignores_the_frequency_rule(monkeypatch):
    stub_zipf(monkeypatch, {})  # nothing attested → no frequency could pick makita
    assert verb_headword("kita") == "makita"


# ── the decision branches ─────────────────────────────────────────────────────


def test_the_um_form_wins_when_it_is_more_frequent(monkeypatch):
    stub_zipf(monkeypatch, {"kumain": 5.0, "magkain": 4.0, "makain": 1.0})
    assert verb_headword("kain") == "kumain"


def test_the_mag_form_wins_when_it_is_more_frequent(monkeypatch):
    # sabi: um(umsabi) is real ("to say to each other") but rare next to magsabi.
    stub_zipf(monkeypatch, {"umsabi": 2.0, "magsabi": 5.0, "masabi": 1.0})
    assert verb_headword("sabi") == "magsabi"


def test_a_tie_between_um_and_mag_prefers_the_um_form(monkeypatch):
    stub_zipf(monkeypatch, {"kumain": 5.0, "magkain": 5.0, "makain": 1.0})
    assert verb_headword("kain") == "kumain"


def test_the_ma_form_is_only_tried_when_neither_um_nor_mag_clears_the_threshold(monkeypatch):
    stub_zipf(monkeypatch, {"tumulog": 2.0, "magtulog": 2.0, "matulog": 5.0})
    assert verb_headword("tulog") == "matulog"


def test_a_root_with_no_attested_infinitive_returns_unchanged(monkeypatch):
    # Cannot tell → no guess, the registry's standing convention.
    stub_zipf(monkeypatch, {})
    assert verb_headword("zzqx") == "zzqx"
    # Vowel-initial root takes the same no-guess path (um form "umupo" unattested).
    assert verb_headword("upo") == "upo"


def test_a_ma_form_below_the_threshold_is_not_guessed(monkeypatch):
    stub_zipf(monkeypatch, {"lumiko": 2.0, "magliko": 2.0, "maliko": 2.9})
    assert verb_headword("liko") == "liko"


def test_an_empty_lemma_comes_back_empty_instead_of_raising():
    # Orchestrator audit probe, 2026-09-23: `_um("")` indexed an empty string.
    assert verb_headword("") == ""
