"""Corpus frequency for a language wordfreq does not cover (tunatale-u8nz.6)."""

from __future__ import annotations

import gzip

import pytest

from app.languages import get_frequency_table_path
from app.srs.frequency_table import FrequencyTable, load_frequency_table


def _write(path, text: str) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        fh.write(text)


def test_zipf_is_log10_of_occurrences_per_billion():
    table = FrequencyTable({"sa": 1000, "tawo": 10}, total_tokens=1_000_000)
    assert table.zipf("sa") == 6.0
    assert table.zipf("tawo") == 4.0


def test_an_absent_lemma_has_zipf_zero_like_wordfreq():
    assert FrequencyTable({"sa": 1}, total_tokens=1).zipf("wala") == 0.0


def test_lookup_is_case_insensitive():
    table = FrequencyTable({"sugbo": 100}, total_tokens=1_000_000)
    assert table.zipf("Sugbo") == table.zipf("sugbo") > 0


def test_load_reads_the_header_total_and_rows(tmp_path):
    path = tmp_path / "f.tsv.gz"
    _write(path, "#source=x\n#total_tokens=1000000\nsa\t1000\ntawo\t10\n")
    table = load_frequency_table(path)
    assert table.zipf("sa") == 6.0
    assert table.zipf("tawo") == 4.0


def test_load_refuses_a_file_without_a_total(tmp_path):
    """Loud, not a silent zipf of 0 for every word."""
    path = tmp_path / "f.tsv.gz"
    _write(path, "#source=x\nsa\t1000\n")
    with pytest.raises(ValueError, match="total_tokens"):
        load_frequency_table(path)


def test_load_is_cached_per_path(tmp_path):
    path = tmp_path / "f.tsv.gz"
    _write(path, "#total_tokens=10\nsa\t1\n")
    assert load_frequency_table(path) is load_frequency_table(path)


def test_a_language_with_wordfreq_ships_no_table():
    assert get_frequency_table_path("no") is None
    assert get_frequency_table_path("xx") is None


# ── the committed Cebuano table (built from FineWeb-2 native news) ───────────


@pytest.fixture(scope="module")
def ceb():
    path = get_frequency_table_path("ceb")
    assert path is not None and path.exists()
    return load_frequency_table(path)


def test_the_commonest_cebuano_words_rank_highest(ceb):
    assert ceb.zipf("sa") > ceb.zipf("mga") > ceb.zipf("tawo") > 5.0


def test_affixed_forms_were_counted_under_their_root(ceb):
    """Root-keyed through the lemma table: a surface that only ever appears
    affixed is absent, its root present."""
    assert ceb.zipf("lakaw") > 0.0
    assert ceb.zipf("naglakaw") == 0.0


def test_english_code_switching_was_filtered(ceb):
    """`the` is ~35k raw tokens in this corpus, above `tawo` (~10k); the
    English-run filter is what puts it back below."""
    assert ceb.zipf("the") < ceb.zipf("tawo")
    assert ceb.zipf("of") < ceb.zipf("tawo")
