"""The order_base_list CLI: dry run, --apply rewrites the rows in frequency order, a second run is a no-op."""

from __future__ import annotations

import gzip

import pytest

from scripts.order_base_list import main

LIST = """# a base list, with a comment the rewrite must keep
#rank_offset=40
category\tenglish\tcebuano\tstatus\textra
animal\tdog\tiro\tdictionary\tx1
animal\tcat\tiring\tdictionary\tx2
food\tbread\tpan\taccepted\tx3
clothing\tdress\tbestida\tunconfirmed\tx4
"""
ORDERED = """# a base list, with a comment the rewrite must keep
#rank_offset=40
category\tenglish\tcebuano\tstatus\textra
food\tbread\tpan\taccepted\tx3
animal\tdog\tiro\tdictionary\tx1
clothing\tdress\tbestida\tunconfirmed\tx4
animal\tcat\tiring\tdictionary\tx2
"""


@pytest.fixture
def files(tmp_path):
    list_path = tmp_path / "base.tsv"
    list_path.write_text(LIST, encoding="utf-8")
    frequency = tmp_path / "frequency.tsv.gz"
    with gzip.open(frequency, "wt", encoding="utf-8") as fh:
        fh.write("#source=test\npan\t90\niro\t50\niring\t40\n")
    return list_path, frequency


def test_a_dry_run_reports_and_writes_nothing(files, capsys):
    list_path, frequency = files
    assert main(["--list", str(list_path), "--frequency", str(frequency)]) == 0
    out = capsys.readouterr().out
    assert "4 rows, 4 move" in out
    assert "same-theme neighbours: 1 -> 0" in out
    assert "not in the corpus: 1 (bestida)" in out
    assert "Dry run" in out
    assert list_path.read_text(encoding="utf-8") == LIST


def test_apply_reorders_the_rows_and_keeps_every_other_line(files, capsys):
    list_path, frequency = files
    assert main(["--list", str(list_path), "--frequency", str(frequency), "--apply"]) == 0
    assert list_path.read_text(encoding="utf-8") == ORDERED
    assert "Dry run" not in capsys.readouterr().out


def test_a_second_run_moves_nothing(files, capsys):
    list_path, frequency = files
    main(["--list", str(list_path), "--frequency", str(frequency), "--apply"])
    capsys.readouterr()
    assert main(["--list", str(list_path), "--frequency", str(frequency), "--apply"]) == 0
    assert "4 rows, 0 move" in capsys.readouterr().out
    assert list_path.read_text(encoding="utf-8") == ORDERED


def test_a_comment_among_the_rows_is_refused(files, capsys):
    """Rows are matched to lines by position, so a line that is not a row would shift them."""
    list_path, frequency = files
    list_path.write_text(LIST + "# a stray note\nhome\thouse\tbalay\tdictionary\tx5\n", encoding="utf-8")
    assert main(["--list", str(list_path), "--frequency", str(frequency), "--apply"]) == 2
    assert "comment among the rows" in capsys.readouterr().err
    assert list_path.read_text(encoding="utf-8").startswith(LIST)
