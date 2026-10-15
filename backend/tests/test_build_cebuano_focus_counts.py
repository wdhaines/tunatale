"""The Cebuano object-focus count builder (tunatale-ve4p.32).

Pins the builder's rules against tiny hand-written inputs, never the real
corpus (pyarrow and a 279 MB shard). The committed table is the oracle
exercised by ``tests/test_ceb_focus_sentences.py``.
"""

from __future__ import annotations

from scripts.build_cebuano_affix_counts import count_surfaces
from scripts.build_cebuano_focus_counts import focus_counts, object_forms, write_table

_ROWS = (
    "# header\n"
    "inom\tdrink\tdrank\tmoinom\tmiinom\timnon\tgiinom\ttubig=water\n"
    "kaon\teat\tate\tmokaon\tmikaon\tkan-on\tgikaon\tpan=bread\n"
)


def test_the_forms_counted_are_the_rows_own_gi_and_on_spellings(tmp_path):
    rows = tmp_path / "rows.tsv"
    rows.write_text(_ROWS, encoding="utf-8")
    assert object_forms(rows) == ["imnon", "giinom", "kan-on", "gikaon"]


def test_a_spelling_is_counted_as_written_with_hyphens_folded():
    """``kan-on`` and ``kanon`` are one form in news, as the affix counter has it;
    ``inomon``, which no rule here writes, does not count toward ``imnon``."""
    surfaces = count_surfaces(["Kan-on nimo ang pan. Kanon ko kini. Imnon ko ang tubig, dili inomon."])
    assert focus_counts(surfaces, ["kan-on", "imnon", "gikaon"]) == {"kan-on": 2, "imnon": 1, "gikaon": 0}


def test_the_table_is_sorted_and_keeps_a_zero(tmp_path):
    out = tmp_path / "counts.tsv"
    assert write_table({"imnon": 51, "gikaon": 0, "dad-on": 309}, out, source="x") == 3
    assert out.read_text(encoding="utf-8") == "#source=x\ndad-on\t309\ngikaon\t0\nimnon\t51\n"
