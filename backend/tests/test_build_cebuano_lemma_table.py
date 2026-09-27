"""The Cebuano kaikki lemma-table builder (tunatale-u8nz.5).

Pins the builder's RULES against a tiny hand-written JSONL fixture — never the
real extract. The committed table itself is the oracle exercised by
``tests/test_cebuano_lemma_table.py``; these tests pin the shape of the rules
that produce it, probing near-misses BETWEEN the golden cases the way the
delegation audit demands.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

from scripts.build_cebuano_lemma_table import (
    _assimilate_mang,
    _is_vowel_initial,
    assign_defaults,
    base_list_headwords,
    build_from_path,
    extract,
    extract_attested,
    generate_forms,
    linker_rows,
)

# ── fixture helpers ───────────────────────────────────────────────────────────


def entry(word: str, pos: str, forms: tuple = ()) -> dict:
    e: dict = {"word": word, "pos": pos}
    if forms:
        e["forms"] = [{"form": f} for f in forms]
    return e


def inflected_entry(word: str, forms: list[dict]) -> dict:
    """Entry with a conjugation table (inflection-template tag)."""
    return {"word": word, "pos": "verb", "forms": forms}


def write_fixture(tmp_path, entries: list[dict]) -> Path:
    path = tmp_path / "fixture.jsonl"
    path.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")
    return path


# ── extract: POS map, headwords ───────────────────────────────────────────────


def test_every_mapped_pos_produces_a_self_headword_row():
    entries = [
        entry(w, p)
        for w, p in [
            ("balay", "noun"),
            ("lakaw", "verb"),
            ("maayo", "adj"),
            ("bihira", "adv"),
            ("ako", "pron"),
            ("ang", "det"),
            ("usah", "num"),
            ("sa", "prep"),
            ("ug", "conj"),
            ("aba", "intj"),
            ("ba", "particle"),
            ("Juan", "name"),
        ]
    ]
    verb_surfaces, nonverb, counts = extract(entries)
    assert verb_surfaces == {"lakaw": {"lakaw"}}
    assert set(nonverb) == {"balay", "maayo", "bihira", "ako", "ang", "usah", "sa", "ug", "aba", "ba", "juan"}
    assert nonverb["ug"] == {"CCONJ"}
    assert nonverb["juan"] == {"PROPN"}
    assert counts["verb"] == 1


def test_an_unmapped_pos_is_dropped_entirely():
    verb_surfaces, nonverb, counts = extract([entry("xyz", "symbol"), entry("abc", "affixy")])
    assert verb_surfaces == {} and nonverb == {}
    assert counts["symbol"] == 1 and counts["affixy"] == 1


def test_text_is_lowercased_and_accent_stripped():
    verb_surfaces, nonverb, _ = extract([entry("Iyót", "verb"), entry("MAAYO", "adj")])
    assert "iyot" in verb_surfaces
    assert "maayo" in nonverb


def test_a_word_failing_the_word_regex_is_dropped():
    verb_surfaces, nonverb, _ = extract([entry("123", "verb"), entry("a b", "noun")])
    assert verb_surfaces == {}
    assert nonverb == {}


# ── extract_attested: conjugation tables ───────────────────────────────────────


def test_inflection_template_opens_a_block_and_first_word_is_root():
    forms = [
        {"form": "ceb-infl-mo", "tags": ["inflection-template"]},
        {"form": "kaon"},
        {"form": "mokaon"},
        {"form": "mikaon"},
    ]
    verb_surfaces, root_form_count = extract_attested([inflected_entry("kaon", forms)])
    assert verb_surfaces == {"mokaon": {"kaon"}, "mikaon": {"kaon"}}
    # Counts attested FORMS (rule 5's tie-break), not blocks.
    assert root_form_count == {"kaon": 2}


def test_multiple_inflection_blocks_in_one_entry():
    forms = [
        {"form": "ceb-infl-mo", "tags": ["inflection-template"]},
        {"form": "kaon"},
        {"form": "mokaon"},
        {"form": "ceb-infl-mag", "tags": ["inflection-template"]},
        {"form": "kaon"},
        {"form": "magkaon"},
    ]
    verb_surfaces, root_form_count = extract_attested([inflected_entry("kaon", forms)])
    assert verb_surfaces == {"mokaon": {"kaon"}, "magkaon": {"kaon"}}
    assert root_form_count == {"kaon": 2}


def test_table_tags_and_canonical_forms_are_skipped():
    forms = [
        {"form": "ceb-infl-mo", "tags": ["inflection-template"]},
        {"form": "kaon"},
        {"form": "actor", "tags": ["table-tags"]},
        {"form": "canonical", "tags": ["canonical"]},
        {"form": "mokaon"},
    ]
    verb_surfaces, _ = extract_attested([inflected_entry("kaon", forms)])
    assert verb_surfaces == {"mokaon": {"kaon"}}


def test_affix_markers_are_skipped():
    forms = [
        {"form": "ceb-infl-mo", "tags": ["inflection-template"]},
        {"form": "kaon"},
        {"form": "-on"},
        {"form": "mag-"},
        {"form": "mokaon"},
    ]
    verb_surfaces, _ = extract_attested([inflected_entry("kaon", forms)])
    assert verb_surfaces == {"mokaon": {"kaon"}}


def test_forms_with_spaces_or_braces_are_skipped():
    forms = [
        {"form": "ceb-infl-mo", "tags": ["inflection-template"]},
        {"form": "kaon"},
        {"form": "ma kaon"},
        {"form": "{mokaon}"},
        {"form": "mokaon"},
    ]
    verb_surfaces, _ = extract_attested([inflected_entry("kaon", forms)])
    assert verb_surfaces == {"mokaon": {"kaon"}}


def test_table_labels_are_skipped():
    forms = [
        {"form": "ceb-infl-mo", "tags": ["inflection-template"]},
        {"form": "kaon"},
        {"form": "actor"},
        {"form": "obj"},
        {"form": "mokaon"},
    ]
    verb_surfaces, _ = extract_attested([inflected_entry("kaon", forms)])
    assert verb_surfaces == {"mokaon": {"kaon"}}


def test_alternative_and_obsolete_forms_are_skipped():
    forms = [
        {"form": "ceb-infl-mo", "tags": ["inflection-template"]},
        {"form": "kaon"},
        {"form": "mokaon", "tags": ["alternative"]},
        {"form": "mikaon", "tags": ["obsolete"]},
        {"form": "nikaon"},
    ]
    verb_surfaces, _ = extract_attested([inflected_entry("kaon", forms)])
    assert verb_surfaces == {"nikaon": {"kaon"}}


# ── generate_forms: affix rules ───────────────────────────────────────────────


def test_simple_prefixes():
    roots = {"kaon", "lakaw"}
    headword_upos = {r: {"VERB"} for r in roots}
    generated = generate_forms(roots, headword_upos)
    assert "mokaon" in generated
    assert "mikaon" in generated
    assert "nikaon" in generated
    assert "makakaon" in generated
    assert "nakakaon" in generated
    assert "makaon" in generated
    assert "nakaon" in generated
    assert "gikaon" in generated
    assert "ikaon" in generated
    assert "kakaon" in generated


def test_mag_nag_pag_takes_hyphen_before_vowel():
    roots = {"ampo", "lakaw"}
    headword_upos = {"ampo": {"VERB"}, "lakaw": {"VERB"}}
    generated = generate_forms(roots, headword_upos)
    assert "mag-ampo" in generated
    assert "nag-ampo" in generated
    assert "pag-ampo" in generated
    assert "maglakaw" in generated  # consonant-initial, no hyphen
    assert "naglakaw" in generated


def test_mang_assimilation():
    roots = {"batayan", "dag-om", "kaon", "itlog"}
    headword_upos = {r: {"VERB"} for r in roots}
    generated = generate_forms(roots, headword_upos)
    # p/b → drop and use m
    assert "mamatayan" in generated
    assert "namatayan" in generated
    assert "pamatayan" in generated
    # t/d/s → n
    assert "manag-om" in generated
    assert "nanag-om" in generated
    assert "panag-om" in generated
    # k → drop k
    assert "mangaon" in generated
    assert "nangaon" in generated
    assert "pangaon" in generated
    # other → plain mang
    assert "mangitlog" in generated
    assert "nangitlog" in generated
    assert "pangitlog" in generated


def test_assimilate_mang_helper():
    assert _assimilate_mang("mang", "batayan") == "mamatayan"  # p/b: drop p/b, add m
    assert _assimilate_mang("mang", "dag-om") == "manag-om"  # t/d/s: use n
    assert _assimilate_mang("mang", "kaon") == "mangaon"  # k: drop k
    assert _assimilate_mang("mang", "itlog") == "mangitlog"  # other: plain mang


def test_suffixes():
    roots = {"kaon", "lakaw"}
    headword_upos = {r: {"VERB"} for r in roots}
    generated = generate_forms(roots, headword_upos)
    assert "kaonon" in generated
    assert "kaonan" in generated
    assert "kaona" in generated
    assert "kaoni" in generated
    assert "gikaonan" in generated


def test_one_letter_roots_are_skipped():
    roots = {"a", "ka"}
    headword_upos = {r: {"VERB"} for r in roots}
    generated = generate_forms(roots, headword_upos)
    assert "moka" in generated  # "ka" is 2 letters
    assert "moa" not in generated  # "a" is 1 letter


def test_generated_forms_only_for_verb_and_adj_roots():
    # balay is noun-only, should get no generated forms
    roots = {"balay", "kaon"}
    headword_upos = {"balay": {"NOUN"}, "kaon": {"VERB"}}
    generated = generate_forms(roots, headword_upos)
    assert "mobalay" not in generated
    assert "nagbalay" not in generated
    assert "mokaon" in generated


def test_is_vowel_initial():
    assert _is_vowel_initial("ampo") is True
    assert _is_vowel_initial("kaon") is False
    assert _is_vowel_initial("") is False


# ── linker_rows ───────────────────────────────────────────────────────────────


def test_linker_rows_are_emitted_for_n_and_vowel_ending_headwords():
    nonverb = {"ako": {"PRON"}, "karon": {"NOUN"}, "sila": {"PRON"}}
    assert linker_rows(nonverb, set()) == {
        ("akong", "PRON", "ako"),
        ("karong", "NOUN", "karon"),
        ("silang", "PRON", "sila"),
    }


def test_a_candidate_already_a_surface_gets_no_linker_row():
    nonverb = {"ako": {"PRON"}}
    base = {"akong"}
    assert linker_rows(nonverb, base) == set()


def test_the_n_plus_g_source_wins_over_the_vowel_plus_ng_one():
    nonverb = {"karon": {"NOUN"}, "karo": {"NOUN"}}
    assert linker_rows(nonverb, set()) == {("karong", "NOUN", "karon")}


def test_a_consonant_final_headword_makes_no_linker():
    nonverb = {"tulog": {"ADJ"}}
    assert linker_rows(nonverb, set()) == set()


# ── assign_defaults ───────────────────────────────────────────────────────────


def test_a_non_verb_headword_keeps_its_default_reading():
    verb_surfaces = {}
    nonverb = {"kita": {"PRON", "VERB"}}
    attested = {}
    root_form_count = {}
    headword_upos = {"kita": {"PRON", "VERB"}}
    linkers = set()
    rows = assign_defaults(verb_surfaces, nonverb, attested, root_form_count, headword_upos, linkers)
    by = {(s, u, lem): d for s, u, lem, d in rows}
    assert by[("kita", "PRON", "kita")] == 1
    assert by[("kita", "VERB", "kita")] == 0


def test_an_intj_reading_never_wins_over_other_readings():
    verb_surfaces = {}
    nonverb = {"aray": {"INTJ", "NOUN"}}
    attested = {}
    root_form_count = {}
    headword_upos = {"aray": {"INTJ", "NOUN"}}
    linkers = set()
    rows = assign_defaults(verb_surfaces, nonverb, attested, root_form_count, headword_upos, linkers)
    by = {(s, u, lem): d for s, u, lem, d in rows}
    assert by[("aray", "NOUN", "aray")] == 1
    assert by[("aray", "INTJ", "aray")] == 0


def test_attested_verb_reading_beats_generated():
    verb_surfaces = {"mokaon": {"kaon", "kaon2"}}
    nonverb = {}
    attested = {"mokaon": {"kaon"}}
    root_form_count = {"kaon": 5, "kaon2": 1}
    headword_upos = {}
    linkers = set()
    rows = assign_defaults(verb_surfaces, nonverb, attested, root_form_count, headword_upos, linkers)
    by = {(s, u, lem): d for s, u, lem, d in rows}
    assert by[("mokaon", "VERB", "kaon")] == 1
    # kaon2 is not in attested, so it should not appear in the rows at all
    # because attested beats generated and only attested roots are kept
    assert ("mokaon", "VERB", "kaon2") not in by


def test_verb_headword_is_its_own_lemma():
    verb_surfaces = {"lakaw": {"lakaw"}}
    nonverb = {}
    attested = {}
    root_form_count = {}
    headword_upos = {"lakaw": {"VERB"}}
    linkers = set()
    rows = assign_defaults(verb_surfaces, nonverb, attested, root_form_count, headword_upos, linkers)
    by = {(s, u, lem): d for s, u, lem, d in rows}
    assert by[("lakaw", "VERB", "lakaw")] == 1


def test_a_verb_headword_beats_a_generated_reading_of_another_root():
    # `tipi` is a verb headword AND `tip` + -i. The headword keeps itself; the
    # generated reading stays as a non-default row. (The single-reading test
    # above cannot tell these apart: with one reading, any rule picks it.)
    verb_surfaces = {"tipi": {"tipi", "tip"}, "tip": {"tip"}}
    headword_upos = {"tipi": {"VERB"}, "tip": {"VERB"}}
    rows = assign_defaults(verb_surfaces, {}, {}, {}, headword_upos, set())
    by = {(s, u, lem): d for s, u, lem, d in rows}
    assert by[("tipi", "VERB", "tipi")] == 1
    assert by[("tipi", "VERB", "tip")] == 0


def test_attested_forms_are_not_roots_of_their_own(tmp_path):
    # Roots are HEADWORDS. An attested form (`mokaon`) must not grow forms of
    # its own (`momokaon`, `mokaonon`).
    forms = [
        {"form": "ceb-infl-mo", "tags": ["inflection-template"]},
        {"form": "kaon"},
        {"form": "mokaon"},
    ]
    # Wiktionary ALSO lists `mokaon` as a verb headword ("to eat"), which is the
    # shape that leaked in the real extract.
    source = write_fixture(tmp_path, [inflected_entry("kaon", forms), entry("mokaon", "verb")])
    output = tmp_path / "out.tsv.gz"
    build_from_path(source, output)
    with gzip.open(output, "rt") as fh:
        surfaces = {line.split("\t")[0] for line in fh if not line.startswith("#")}
    assert "mokaon" in surfaces
    assert "momokaon" not in surfaces
    assert "mokaonon" not in surfaces


def test_generated_prefers_verb_root_over_adj_only():
    verb_surfaces = {"matulog": {"tulog"}}
    nonverb = {}
    attested = {}
    root_form_count = {}
    headword_upos = {"tulog": {"ADJ"}}
    linkers = set()
    rows = assign_defaults(verb_surfaces, nonverb, attested, root_form_count, headword_upos, linkers)
    by = {(s, u, lem): d for s, u, lem, d in rows}
    assert by[("matulog", "VERB", "tulog")] == 1


def test_generated_prefers_shorter_root():
    verb_surfaces = {"form": {"longroot", "short"}}
    nonverb = {}
    attested = {}
    root_form_count = {}
    headword_upos = {"longroot": {"VERB"}, "short": {"VERB"}}
    linkers = set()
    rows = assign_defaults(verb_surfaces, nonverb, attested, root_form_count, headword_upos, linkers)
    by = {(s, u, lem): d for s, u, lem, d in rows}
    # short is shorter than longroot
    assert by[("form", "VERB", "short")] == 1


def test_every_surface_gets_exactly_one_default_row():
    verb_surfaces = {"mokaon": {"kaon"}}
    nonverb = {"ako": {"PRON"}}
    attested = {}
    root_form_count = {}
    headword_upos = {"kaon": {"VERB"}}
    linkers = {("akong", "PRON", "ako")}
    rows = assign_defaults(verb_surfaces, nonverb, attested, root_form_count, headword_upos, linkers)
    from collections import Counter

    assert all(v == 1 for v in Counter(s for s, _u, _l, d in rows if d).values())
    assert len({s for s, _u, _l, d in rows if d}) == len({s for s, _u, _l, _d in rows})


# ── end-to-end: the file the builder writes ───────────────────────────────────


# ── rule 1b: the plugin's own 625 list ────────────────────────────────────────

_BASE_HEADER = "category\tenglish\tcebuano\tstatus\n"


def write_base_list(tmp_path, rows: str) -> Path:
    path = tmp_path / "base.tsv"
    path.write_text("# comment\n" + _BASE_HEADER + rows, encoding="utf-8")
    return path


def test_a_base_list_word_wiktionary_lacks_stays_itself(tmp_path):
    # `orasan` "clock" is not in the fixture's dictionary, and oras is a verb
    # root, so without rule 1b it would default to oras + -an.
    source = write_fixture(tmp_path, [entry("oras", "verb")])
    base = write_base_list(tmp_path, "electronics\tclock\torasan\taccepted\n")
    output = tmp_path / "out.tsv.gz"
    stats = build_from_path(source, output, base)
    with gzip.open(output, "rt") as fh:
        rows = [line.rstrip("\n").split("\t") for line in fh if not line.startswith("#")]
    assert ["orasan", "NOUN", "orasan", "1"] in rows
    assert ["orasan", "VERB", "oras", "0"] in rows
    assert stats["base_list_added"] == 1


def test_base_list_categories_and_statuses(tmp_path):
    base = write_base_list(
        tmp_path,
        "verb\tsearch\tpangita\taccepted\n"
        "adjective\thot\tkainit\taccepted\n"
        "animal\tdog\tiro\tdictionary\n"
        "food\tpink\tzzq\tunconfirmed\n"  # not mintable: skipped
        "place\ttrain station\testasyon sa tren\taccepted\n",  # not one word: skipped
    )
    verbs, nonverb = base_list_headwords(base)
    assert verbs == {"pangita"}
    assert nonverb == {"kainit": {"ADJ"}, "iro": {"NOUN"}}


def test_a_word_wiktionary_has_keeps_wiktionarys_part_of_speech(tmp_path):
    source = write_fixture(tmp_path, [entry("init", "adj")])
    base = write_base_list(tmp_path, "animal\tthing\tinit\tdictionary\n")
    output = tmp_path / "out.tsv.gz"
    stats = build_from_path(source, output, base)
    with gzip.open(output, "rt") as fh:
        rows = [line.rstrip("\n").split("\t") for line in fh if not line.startswith("#")]
    assert ["init", "ADJ", "init", "1"] in rows
    assert not any(r[0] == "init" and r[1] == "NOUN" for r in rows)
    assert stats["base_list_added"] == 0


def test_no_base_list_adds_nothing(tmp_path):
    assert base_list_headwords(None) == (set(), {})
    assert base_list_headwords(tmp_path / "missing.tsv") == (set(), {})


def test_build_from_path_writes_a_valid_table(tmp_path):
    entries = [
        entry("kaon", "verb"),
        entry("ako", "pron"),
        entry("maayo", "adj"),
    ]
    src = write_fixture(tmp_path, entries)
    out = tmp_path / "out.tsv.gz"
    stats = build_from_path(src, out)
    with gzip.open(out, "rt", encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    assert lines[0] == f"#source_version=kaikki-{stats['sha256'][:12]}"
    rows = set(lines[1:])
    assert ("kaon\tVERB\tkaon\t1") in rows
    assert ("ako\tPRON\tako\t1") in rows
    assert ("maayo\tADJ\tmaayo\t1") in rows
    assert ("akong\tPRON\tako\t1") in rows


def test_writing_the_same_table_twice_is_reproducible(tmp_path):
    entries = [entry("kaon", "verb"), entry("ako", "pron")]
    src = write_fixture(tmp_path, entries)
    out1, out2 = tmp_path / "a.tsv.gz", tmp_path / "b.tsv.gz"
    build_from_path(src, out1)
    build_from_path(src, out2)
    assert out1.read_bytes() == out2.read_bytes()


def test_the_table_contains_all_four_sources(tmp_path):
    # Headword, attested, generated, linker
    forms = [
        {"form": "ceb-infl-mo", "tags": ["inflection-template"]},
        {"form": "kaon"},
        {"form": "mokaon"},
    ]
    entries = [
        inflected_entry("kaon", forms),
        entry("ako", "pron"),
        entry("lakaw", "verb"),  # Add a verb headword for generation
    ]
    src = write_fixture(tmp_path, entries)
    out = tmp_path / "out.tsv.gz"
    stats = build_from_path(src, out)
    with gzip.open(out, "rt", encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    rows = set(lines[1:])
    # Headword
    assert ("kaon\tVERB\tkaon\t1") in rows
    # Attested
    assert ("mokaon\tVERB\tkaon\t1") in rows
    # Generated (from lakaw)
    assert ("molakaw\tVERB\tlakaw\t1") in rows
    # Linker
    assert ("akong\tPRON\tako\t1") in rows
    # Check that generated count is > 0
    assert stats["generated_count"] > 0
