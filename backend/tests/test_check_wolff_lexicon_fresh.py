"""Oracles for the Wolff lexicon freshness check (tunatale-h0q3.36)."""

from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

from scripts.check_wolff_lexicon_fresh import check, main


def _write_lexicon(path: Path, *, entries: list[dict], source_sha256: str) -> None:
    payload = json.dumps(
        {"meta": {"source_sha256": source_sha256, "entries": len(entries)}, "entries": entries},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    with gzip.GzipFile(path, "wb", mtime=0) as fh:
        fh.write(payload.encode("utf-8"))


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_missing_dictionary_is_skipped(tmp_path, capsys):
    dictionary = tmp_path / "missing-dict.txt"
    en_sounds = tmp_path / "en_cmu.tsv"
    en_sounds.write_text("", encoding="utf-8")
    lexicon = tmp_path / "lex.json.gz"
    _write_lexicon(lexicon, entries=[], source_sha256="x")

    rc = check(lexicon, dictionary, en_sounds, build=lambda *_: [])

    out = capsys.readouterr().out
    assert rc == 0
    assert "SKIPPED" in out
    assert str(dictionary) in out


def test_missing_en_sounds_is_skipped(tmp_path, capsys):
    dictionary = tmp_path / "dict.txt"
    dictionary.write_text("x", encoding="utf-8")
    en_sounds = tmp_path / "missing-en_cmu.tsv"
    lexicon = tmp_path / "lex.json.gz"
    _write_lexicon(lexicon, entries=[], source_sha256=_sha("x"))

    rc = check(lexicon, dictionary, en_sounds, build=lambda *_: [])

    out = capsys.readouterr().out
    assert rc == 0
    assert "SKIPPED" in out
    assert str(en_sounds) in out


def test_committed_matches_rebuild(tmp_path, capsys):
    dictionary = tmp_path / "dict.txt"
    dictionary.write_text("source", encoding="utf-8")
    en_sounds = tmp_path / "en_cmu.tsv"
    en_sounds.write_text("", encoding="utf-8")
    entries = [{"id": "a", "v": 1}, {"id": "b", "v": 2}]
    lexicon = tmp_path / "lex.json.gz"
    _write_lexicon(lexicon, entries=entries, source_sha256=_sha("source"))

    rc = check(lexicon, dictionary, en_sounds, build=lambda *_: list(entries))

    out = capsys.readouterr().out
    assert rc == 0
    assert "OK, 2 entries" in out


def test_differing_entry_is_stale(tmp_path, capsys):
    dictionary = tmp_path / "dict.txt"
    dictionary.write_text("source", encoding="utf-8")
    en_sounds = tmp_path / "en_cmu.tsv"
    en_sounds.write_text("", encoding="utf-8")
    committed = [{"id": "a", "v": 1}]
    rebuilt = [{"id": "a", "v": 2}]
    lexicon = tmp_path / "lex.json.gz"
    _write_lexicon(lexicon, entries=committed, source_sha256=_sha("source"))

    rc = check(lexicon, dictionary, en_sounds, build=lambda *_: rebuilt)

    out = capsys.readouterr().out
    assert rc == 1
    assert "STALE" in out
    assert "a" in out


def test_id_only_in_rebuild_is_stale(tmp_path, capsys):
    dictionary = tmp_path / "dict.txt"
    dictionary.write_text("source", encoding="utf-8")
    en_sounds = tmp_path / "en_cmu.tsv"
    en_sounds.write_text("", encoding="utf-8")
    committed = [{"id": "a", "v": 1}]
    rebuilt = [{"id": "a", "v": 1}, {"id": "b", "v": 2}]
    lexicon = tmp_path / "lex.json.gz"
    _write_lexicon(lexicon, entries=committed, source_sha256=_sha("source"))

    rc = check(lexicon, dictionary, en_sounds, build=lambda *_: rebuilt)

    out = capsys.readouterr().out
    assert rc == 1
    assert "STALE" in out
    assert "only in the rebuild" in out
    assert "1" in out


def test_id_only_in_committed_is_stale(tmp_path, capsys):
    dictionary = tmp_path / "dict.txt"
    dictionary.write_text("source", encoding="utf-8")
    en_sounds = tmp_path / "en_cmu.tsv"
    en_sounds.write_text("", encoding="utf-8")
    committed = [{"id": "a", "v": 1}, {"id": "b", "v": 2}]
    rebuilt = [{"id": "a", "v": 1}]
    lexicon = tmp_path / "lex.json.gz"
    _write_lexicon(lexicon, entries=committed, source_sha256=_sha("source"))

    rc = check(lexicon, dictionary, en_sounds, build=lambda *_: rebuilt)

    out = capsys.readouterr().out
    assert rc == 1
    assert "STALE" in out
    assert "only in the committed file" in out


def test_sha_mismatch_with_identical_entries_is_stale(tmp_path, capsys):
    dictionary = tmp_path / "dict.txt"
    dictionary.write_text("source", encoding="utf-8")
    en_sounds = tmp_path / "en_cmu.tsv"
    en_sounds.write_text("", encoding="utf-8")
    entries = [{"id": "a", "v": 1}]
    lexicon = tmp_path / "lex.json.gz"
    _write_lexicon(lexicon, entries=entries, source_sha256="wrongsha")

    rc = check(lexicon, dictionary, en_sounds, build=lambda *_: list(entries))

    out = capsys.readouterr().out
    assert rc == 1
    assert "STALE" in out
    assert "sha" in out.lower() or "source_sha256" in out


def test_twelve_differing_ids_lists_first_ten_sorted(tmp_path, capsys):
    dictionary = tmp_path / "dict.txt"
    dictionary.write_text("source", encoding="utf-8")
    en_sounds = tmp_path / "en_cmu.tsv"
    en_sounds.write_text("", encoding="utf-8")
    ids = [f"id-{i:02d}" for i in range(12)]  # id-00..id-11
    committed = [{"id": i, "v": 1} for i in ids]
    rebuilt = [{"id": i, "v": 2} for i in ids]
    lexicon = tmp_path / "lex.json.gz"
    _write_lexicon(lexicon, entries=committed, source_sha256=_sha("source"))

    rc = check(lexicon, dictionary, en_sounds, build=lambda *_: rebuilt)

    out = capsys.readouterr().out
    assert rc == 1
    listed = [i for i in ids if i in out]
    assert listed == ids[:10]
    assert ids[10] not in out or out.count(ids[10]) == 0  # not listed among the 10
    assert ids[11] not in out or out.count(ids[11]) == 0


def test_main_cli_wiring_with_missing_paths(tmp_path):
    lexicon = tmp_path / "lex.json.gz"
    dictionary = tmp_path / "missing-dict.txt"
    en_sounds = tmp_path / "missing-en.tsv"
    _write_lexicon(lexicon, entries=[], source_sha256="x")

    rc = main(
        [
            "--lexicon",
            str(lexicon),
            "--dictionary",
            str(dictionary),
            "--en-sounds",
            str(en_sounds),
        ]
    )

    assert rc == 0


def _inputs(tmp_path):
    dictionary = tmp_path / "dict.txt"
    dictionary.write_text("x", encoding="utf-8")
    en_sounds = tmp_path / "en_cmu.tsv"
    en_sounds.write_text("", encoding="utf-8")
    return dictionary, en_sounds


def test_a_change_under_a_duplicated_id_is_stale(tmp_path, capsys):
    """Wolff's lexicon repeats ids (21,740 entries, 21,372 ids on 2026-11-25): keying the
    comparison by id kept only the last entry of each, so the first could change unseen."""
    dictionary, en_sounds = _inputs(tmp_path)
    lexicon = tmp_path / "lex.json.gz"
    committed = [{"id": "ab-ab", "text": "first"}, {"id": "ab-ab", "text": "second"}]
    _write_lexicon(lexicon, entries=committed, source_sha256=_sha("x"))
    rebuilt = [{"id": "ab-ab", "text": "CHANGED"}, {"id": "ab-ab", "text": "second"}]

    rc = check(lexicon, dictionary, en_sounds, build=lambda *_: rebuilt)

    out = capsys.readouterr().out
    assert rc == 1
    assert "STALE" in out
    assert "ab-ab" in out


def test_a_reordering_is_stale(tmp_path, capsys):
    """The cards read the file in order, so a rebuild that reorders entries is a different file."""
    dictionary, en_sounds = _inputs(tmp_path)
    lexicon = tmp_path / "lex.json.gz"
    a, b = {"id": "a", "text": "1"}, {"id": "b", "text": "2"}
    _write_lexicon(lexicon, entries=[a, b], source_sha256=_sha("x"))

    assert check(lexicon, dictionary, en_sounds, build=lambda *_: [b, a]) == 1
