"""The Cebuano affix-count builder (tunatale-ve4p.2).

Pins the builder's RULES against tiny hand-written inputs — never the real
corpus, which needs pyarrow and a 279 MB download. The committed table is the
oracle exercised by ``tests/test_ceb_affix_patterns.py``.
"""

from __future__ import annotations

import gzip

from app.srs.lemma_table import Reading
from scripts.build_cebuano_affix_counts import affix_counts, count_surfaces, is_verb_form_of, write_table

_TABLE = {
    "naglakaw": [Reading("VERB", "lakaw", True)],
    "maglakaw": [Reading("VERB", "lakaw", True)],
    "mag-ampo": [Reading("VERB", "ampo", True)],
    # A homograph: the everyday word is the adverb, the verb reading is the template's.
    "mahimo": [Reading("ADV", "mahimo", True), Reading("VERB", "himo", False)],
    # The table's own lemma is wrong (mopalit as its own root) but it is a verb.
    "mopalit": [Reading("VERB", "mopalit", True), Reading("VERB", "palit", False)],
    "naa": [Reading("VERB", "a", False), Reading("VERB", "naa", True)],
}


def _readings(surface: str) -> list[Reading]:
    return _TABLE.get(surface, [])


def test_surfaces_are_counted_lowercased_with_hyphens_dropped():
    counts = count_surfaces(["Naglakaw si Paul. Mag-ampo ta, magampo ta!", "naglakaw 2026"])
    assert counts["naglakaw"] == 2
    assert counts["magampo"] == 2  # the hyphenated and the run-together spelling are one form
    assert "2026" not in counts
    assert "." not in counts


def test_a_surface_counts_for_a_root_when_the_table_reads_it_as_that_roots_verb():
    assert is_verb_form_of("naglakaw", "lakaw", _readings)
    assert is_verb_form_of("mag-ampo", "ampo", _readings)


def test_a_wrong_default_lemma_does_not_hide_a_real_verb_form():
    """``mopalit`` is filed under itself by default; it is still palit's mo- form."""
    assert is_verb_form_of("mopalit", "palit", _readings)


def test_a_homograph_whose_everyday_reading_is_not_a_verb_is_refused():
    """``mahimo`` is overwhelmingly the adverb 'can'; its count says nothing about ma- on himo."""
    assert not is_verb_form_of("mahimo", "himo", _readings)


def test_a_surface_the_table_does_not_tie_to_the_root_is_refused():
    assert not is_verb_form_of("naglakaw", "lakat", _readings)
    assert not is_verb_form_of("unknownword", "lakaw", _readings)


def test_affix_counts_keeps_only_vouched_pairs():
    surfaces = count_surfaces(
        ["naglakaw naglakaw maglakaw", "mag-ampo", "mahimo mahimo mahimo", "naa naa naa naa", "mopalit"]
    )
    got = affix_counts(surfaces, ["lakaw", "ampo", "himo", "palit", "a", "tulog"], readings=_readings)
    assert got == {
        ("lakaw", "nag"): 2,
        ("lakaw", "mag"): 1,
        ("ampo", "mag"): 1,
        ("palit", "mo"): 1,
        # himo: the adverb. a: too short to be a root (na + a is the word naa).
        # tulog: never seen.
    }


def test_the_table_is_written_sorted_filtered_and_reproducibly(tmp_path):
    counts = {("lakaw", "nag"): 254, ("lakaw", "mag"): 49, ("ampo", "mo"): 4, ("ampo", "mag"): 107}
    out = tmp_path / "data" / "counts.tsv.gz"
    assert write_table(counts, out, source="test sha256=abc", min_count=5) == 3
    with gzip.open(out, "rt", encoding="utf-8") as fh:
        assert fh.read() == "#source=test sha256=abc\nampo\tmag\t107\nlakaw\tmag\t49\nlakaw\tnag\t254\n"
    first = out.read_bytes()
    write_table(counts, out, source="test sha256=abc", min_count=5)
    assert out.read_bytes() == first  # mtime=0: an unchanged rebuild leaves git clean
