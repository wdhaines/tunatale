"""The Tagalog pronunciation-table builder (tunatale-w4m7.16).

Pins the builder's rules against a tiny hand-written JSONL fixture, never the
124 MB extract. The committed table is exercised by
``tests/test_tagalog_pronunciation.py``.
"""

from __future__ import annotations

import gzip
import json

from scripts.build_kaikki_pronunciation_table import build_from_path, extract_readings, table_rows


def entry(word: str, pos: str, *ipas: str) -> dict:
    return {"word": word, "pos": pos, "sounds": [{"ipa": ipa} for ipa in ipas]}


def test_only_narrow_readings_are_kept():
    # Broad /…/ readings carry no vowel length, and length is the stress cue the
    # key-phrase voice honours; a narrow [...] reading is what the table is for.
    readings = extract_readings([entry("salamat", "noun", "/saˈlamat/", "[sɐˈlaː.mɐt̪̚]")])
    assert readings == {("salamat", "NOUN"): ["sɐˈlaː.mɐt̪̚"]}


def test_reading_order_is_preserved_and_duplicates_dropped():
    # Wiktionary lists alternatives in order; the runtime chooser breaks ties by it.
    readings = extract_readings([entry("kailan", "pron", "[k̠ɐ.ʔɪˈlan̪]", "[k̠aɪ̯ˈlan̪]", "[k̠ɐ.ʔɪˈlan̪]", "[k̠eɪ̯ˈlan̪]")])
    assert readings[("kailan", "PRON")] == ["k̠ɐ.ʔɪˈlan̪", "k̠aɪ̯ˈlan̪", "k̠eɪ̯ˈlan̪"]


def test_two_entries_with_the_same_pos_pool_their_readings():
    readings = extract_readings([entry("siko", "noun", "[sɪˈx̠o]"), entry("siko", "noun", "[ˈsiː.x̠o]")])
    assert readings[("siko", "NOUN")] == ["sɪˈx̠o", "ˈsiː.x̠o"]


def test_unmapped_pos_multiword_and_spaced_readings_are_dropped():
    readings = extract_readings(
        [
            entry("ng", "character", "[ˌʔɛn̪ ˈd͡ʒɪ]"),  # a letter name, not the particle
            entry("magandang umaga", "phrase", "[mɐɡɐn̪ˈd̪aŋ ʔʊˈmaː.ɡɐ]"),
            entry("ng", "particle", "[n̪ɐŋ]"),
            entry("ewan", "adv", "[ʔɛ ˈwan]"),  # a spaced reading is two words
        ]
    )
    assert readings == {("ng", "PART"): ["n̪ɐŋ"]}


def test_words_are_keyed_lowercase():
    readings = extract_readings([entry("Linggo", "name", "[lɪŋˈɡo]")])
    assert readings == {("linggo", "PROPN"): ["lɪŋˈɡo"]}


def test_default_rows_follow_the_lemma_tables_default_upos():
    # ako is a pronoun first, and the noun homograph [ˈʔaː.x̠oʔ] must not win.
    readings = {("ako", "PRON"): ["ʔɐˈx̠o"], ("ako", "NOUN"): ["ˈʔaː.x̠oʔ"]}
    rows = table_rows(readings, {"ako": "PRON"})
    assert rows == [("ako", "PRON", 0, "ʔɐˈx̠o", 1), ("ako", "NOUN", 0, "ˈʔaː.x̠oʔ", 0)]


def test_a_word_without_a_default_upos_has_no_default_row():
    rows = table_rows({("xyz", "NOUN"): ["ˈksiːs"]}, {})
    assert rows == [("xyz", "NOUN", 0, "ˈksiːs", 0)]


def test_build_writes_a_deterministic_gzip_with_a_source_header(tmp_path):
    source = tmp_path / "fixture.jsonl"
    source.write_text(
        "".join(json.dumps(e) + "\n" for e in [entry("ako", "pron", "[ʔɐˈx̠o]"), entry("ako", "noun", "[ˈʔaː.x̠oʔ]")]),
        encoding="utf-8",
    )
    lemma_table = tmp_path / "lemmas.tsv.gz"
    with gzip.open(lemma_table, "wt", encoding="utf-8") as fh:
        fh.write("#source_version=x\nako\tNOUN\tako\t0\nako\tPRON\tako\t1\n")

    out = tmp_path / "out.tsv.gz"
    stats = build_from_path(source, lemma_table, out)
    first = out.read_bytes()
    build_from_path(source, lemma_table, out)

    assert out.read_bytes() == first  # mtime=0 gzip: rebuilding changes nothing
    lines = gzip.decompress(first).decode("utf-8").splitlines()
    assert lines[0].startswith("#source_version=kaikki-")
    assert lines[1:] == ["ako\tPRON\t0\tʔɐˈx̠o\t1", "ako\tNOUN\t0\tˈʔaː.x̠oʔ\t0"]
    assert stats["words"] == 1
    assert stats["rows"] == 2
