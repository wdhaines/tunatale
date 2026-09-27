"""The Cebuano frequency-list builder (tunatale-u8nz.6).

Pins the builder's RULES against tiny hand-written inputs — never the real
corpus, which needs pyarrow and a 279 MB download. The committed table is the
oracle exercised by ``tests/test_frequency_table.py``.
"""

from __future__ import annotations

import gzip

import pytest

from app.srs.lemma_table import Reading
from scripts.build_cebuano_frequency import count_lemmas, is_native_host, write_table

# ── is_native_host: the corpus is native news only ───────────────────────────


@pytest.mark.parametrize(
    "url",
    [
        "https://www.sunstar.com.ph/article/1",
        "https://m.sunstar.com.ph/article/1",
        "https://archives.pia.gov.ph/news/2",
        "https://rmn.ph/x",
        "https://www.rpnradio.com/y",
    ],
)
def test_native_news_hosts_and_their_subdomains_are_kept(url):
    assert is_native_host(url)


@pytest.mark.parametrize(
    "url",
    [
        # Machine-translated sites and Wikipedia are the corpus's two traps.
        "https://ceb.eturbonews.com/x",
        "https://ceb.wikipedia.org/wiki/Davao",
        # A suffix match on the bare string would accept these near-misses.
        "https://notsunstar.com.ph/x",
        "https://sunstar.com.ph.evil.example/x",
        "not a url",
    ],
)
def test_everything_else_is_dropped(url):
    assert not is_native_host(url)


# ── count_lemmas: root keys, and English runs dropped ─────────────────────────

_TABLE = {
    "naglakaw": [Reading("VERB", "lakaw", True)],
    "lakaw": [Reading("VERB", "lakaw", True)],
    "kita": [Reading("VERB", "kita", False), Reading("PRON", "kita", True)],
    "tawo": [Reading("NOUN", "tawo", True)],
}
# Hand-set English zipf: the rule's only other input.
_EN = {"the": 7.7, "police": 5.2, "station": 5.0, "ka": 3.4, "joel": 3.9, "city": 5.6}


def _count(*texts):
    return count_lemmas(texts, readings=lambda s: _TABLE.get(s, []), en_zipf=lambda s: _EN.get(s, 0.0))


def test_a_known_form_counts_under_its_default_readings_lemma():
    assert _count("Naglakaw ang tawo. Lakaw kita.") == {"lakaw": 2, "ang": 1, "tawo": 1, "kita": 1}


def test_an_unknown_token_counts_under_its_lowercase_surface():
    """The runtime lemmatizer passes an unknown surface through lowercased, so
    the list must key it the same way or a lookup would never hit."""
    assert _count("Mga tawo")["mga"] == 1


def test_an_english_run_is_dropped():
    counts = _count("the police station")
    assert counts == {}


def test_an_isolated_english_looking_token_between_cebuano_is_kept():
    """`ka` ("you") has an English zipf above the bar; alone among Cebuano it
    is Cebuano, which is what the context rule is for."""
    counts = _count("tawo ka tawo")
    assert counts["ka"] == 1


def test_a_capitalized_name_does_not_make_its_neighbour_english():
    """`Joel` clears the lowercase bar (3.9 >= 3.0) but not the capitalized one
    (< 4.5), so `ka` beside it is not in an English run."""
    assert _count("ka Joel")["ka"] == 1


def test_a_lowercase_name_like_token_does_make_a_run():
    """The near-miss to the case above: the same word lowercased IS eligible."""
    assert "ka" not in _count("ka joel")


def test_a_known_cebuano_word_never_starts_a_run():
    """`tawo` is in the table, so it is not English whatever wordfreq says."""
    counts = count_lemmas(
        ["tawo police"],
        readings=lambda s: _TABLE.get(s, []),
        en_zipf=lambda s: 9.0,
    )
    assert counts == {"tawo": 1, "police": 1}


def test_punctuation_and_numbers_are_not_tokens():
    assert _count("tawo, 2024 — tawo!") == {"tawo": 2}


# ── write_table ───────────────────────────────────────────────────────────────


def _lines(path) -> list[str]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return fh.read().splitlines()


def test_write_table_records_the_full_total_but_drops_rare_rows(tmp_path):
    out = tmp_path / "freq.tsv.gz"
    written = write_table({"sa": 10, "tawo": 5, "rare": 1}, out, source="test-source", min_count=2)
    assert written == 2
    lines = _lines(out)
    assert lines[:2] == ["#source=test-source", "#total_tokens=16"]
    assert lines[2:] == ["sa\t10", "tawo\t5"]


def test_write_table_orders_ties_alphabetically(tmp_path):
    out = tmp_path / "freq.tsv.gz"
    write_table({"b": 3, "a": 3, "c": 4}, out, source="s", min_count=1)
    rows = _lines(out)[2:]
    assert rows == ["c\t4", "a\t3", "b\t3"]
