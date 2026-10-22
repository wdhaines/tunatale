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

import pytest

from scripts.build_cebuano_lemma_table import (
    _assimilate_mang,
    _is_vowel_initial,
    apply_wolff_roots,
    assign_defaults,
    base_list_headwords,
    build_from_path,
    extract,
    extract_attested,
    extract_form_of,
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


# ── rule 2b: headwords Wiktionary defines only as a form of another word ──────


def form_of_entry(word: str, *targets: str, pos: str = "verb") -> dict:
    """An entry with one sense per target, each "<some form> of <target>"."""
    return {"word": word, "pos": pos, "senses": [{"form_of": [{"word": t}]} for t in targets]}


def derived_entry(word: str, template: str, *args: str) -> dict:
    """A verb entry whose etymology is one template: ``{{template|ceb|*args}}``."""
    positional = {str(i): value for i, value in enumerate(("ceb", *args), start=1)}
    return {
        "word": word,
        "pos": "verb",
        "senses": [{"glosses": ["x"]}],
        "etymology_templates": [{"name": template, "args": positional}],
    }


def built_rows(tmp_path, entries: list[dict]) -> set[tuple[str, str, str, int]]:
    out = tmp_path / "out.tsv.gz"
    build_from_path(write_fixture(tmp_path, entries), out)
    with gzip.open(out, "rt", encoding="utf-8") as fh:
        lines = [line.rstrip("\n").split("\t") for line in fh if not line.startswith("#")]
    return {(s, u, lem, int(d)) for s, u, lem, d in lines}


def test_a_headword_defined_only_as_a_form_names_the_word_it_is_a_form_of():
    assert extract_form_of([form_of_entry("andama", "andam")]) == {"andama": {"andam"}}


def test_a_headword_with_a_sense_of_its_own_is_not_a_form():
    # One "form of" sense beside a real one: the word means something itself.
    mixed = {"word": "tipi", "pos": "verb", "senses": [{"form_of": [{"word": "tip"}]}, {"glosses": ["to kill"]}]}
    assert extract_form_of([mixed]) == {}
    # The same split across two entries of the word (Wiktionary's etymology 1 / 2).
    assert extract_form_of([form_of_entry("tipi", "tip"), entry("tipi", "verb")]) == {}
    # An entry with no senses at all says nothing about being a form.
    assert extract_form_of([entry("lakaw", "verb")]) == {}


def test_a_form_of_target_is_normalized_and_must_be_another_single_word():
    assert extract_form_of([form_of_entry("andama", "Andám")]) == {"andama": {"andam"}}
    assert extract_form_of([form_of_entry("andama", "andama")]) == {}  # a form of itself names nothing
    assert extract_form_of([form_of_entry("andama", "andam na")]) == {}  # not one word
    # Every sense must name a usable target, or the headword keeps itself.
    assert extract_form_of([form_of_entry("andama", "andam", "andam na")]) == {}
    assert extract_form_of([form_of_entry("gikan", "kan", "gikan")]) == {}


def test_two_senses_can_name_two_words():
    assert extract_form_of([form_of_entry("panawa", "tawa", "sawa")]) == {"panawa": {"tawa", "sawa"}}


def test_only_verb_entries_are_read_for_form_of():
    # A noun "plural of" entry stays a noun headword (rule 1); rule 2b is about verbs.
    assert extract_form_of([form_of_entry("mga-bata", "bata", pos="noun")]) == {}
    assert extract_form_of([form_of_entry("-a", "andam")]) == {}  # not a word


def test_a_form_of_headword_takes_the_root_and_is_no_root_itself(tmp_path):
    rows = built_rows(tmp_path, [entry("andam", "verb"), form_of_entry("andama", "andam")])
    assert ("andama", "VERB", "andam", 1) in rows
    # No reading of its own: one would be a second VERB reading no tag could tell apart.
    assert not any(row[:3] == ("andama", "VERB", "andama") for row in rows)
    # Nothing in the table is filed under it either.
    assert not any(row[2] == "andama" for row in rows)


def test_forms_built_on_a_form_of_stem_take_the_stems_root(tmp_path):
    # bas-a is "imperative of basa", syncopated, so ma- + basa never produces
    # mabas-a: only the stem does. The form belongs to basa.
    rows = built_rows(tmp_path, [entry("basa", "verb"), form_of_entry("bas-a", "basa")])
    assert ("mabas-a", "VERB", "basa", 1) in rows
    assert ("nagbas-a", "VERB", "basa", 1) in rows
    assert ("mabasa", "VERB", "basa", 1) in rows  # the plain root's own form, untouched
    assert not any(row[2] == "bas-a" for row in rows)


def test_a_form_a_conjugation_table_already_lists_is_not_a_stem(tmp_path):
    # balia is in bali's own table AND has a form-of entry. The table carries
    # the paradigm, so nothing more is built on it (no `mobalia`, no `baliaa`).
    forms = [{"form": "ceb-infl-mo", "tags": ["inflection-template"]}, {"form": "bali"}, {"form": "balia"}]
    rows = built_rows(tmp_path, [inflected_entry("bali", forms), form_of_entry("balia", "bali")])
    assert ("balia", "VERB", "bali", 1) in rows
    surfaces = {row[0] for row in rows}
    assert "mobalia" not in surfaces
    assert "baliaa" not in surfaces


def test_a_stem_of_two_words_gives_its_forms_both_roots(tmp_path):
    rows = built_rows(tmp_path, [entry("tawa", "verb"), entry("sawa", "verb"), form_of_entry("panawa", "tawa", "sawa")])
    assert {row[2] for row in rows if row[0] == "mopanawa"} == {"tawa", "sawa"}


def test_a_form_of_headword_keeps_a_non_verb_reading_as_its_default(tmp_path):
    # A noun headword that is also "imperative of kaon": rule 5a still wins. The
    # form is syncopated (kan-a, where the generator makes kaona), so the VERB
    # reading can only have come from the form-of sense.
    rows = built_rows(tmp_path, [entry("kaon", "verb"), entry("kan-a", "noun"), form_of_entry("kan-a", "kaon")])
    assert ("kan-a", "NOUN", "kan-a", 1) in rows
    assert ("kan-a", "VERB", "kaon", 0) in rows


def test_a_headword_the_generator_reads_differently_stays_itself(tmp_path):
    # tipi "to kill" is also tip + -i. Nothing in Wiktionary calls it a form of
    # tip, so it keeps itself and the generated reading stays a non-default.
    rows = built_rows(tmp_path, [entry("tip", "verb"), entry("tipi", "verb")])
    assert ("tipi", "VERB", "tipi", 1) in rows
    assert ("tipi", "VERB", "tip", 0) in rows


def test_a_derived_headword_keeps_itself_whatever_its_etymology_says(tmp_path):
    # mogawas "to go out" has its own senses and an etymology of mo- + gawas.
    # That is a word DERIVED from gawas, not a form of it, and the rule that
    # would file it under its root was measured and rejected (2026-10-09): the
    # same shape is natawo "born" from tawo "person" and managat "to fish" from
    # dagat "sea". The ones that are plain inflection go in closed_class.tsv.
    rows = built_rows(tmp_path, [entry("gawas", "verb"), derived_entry("mogawas", "prefix", "mo", "gawas")])
    assert ("mogawas", "VERB", "mogawas", 1) in rows
    assert ("mogawas", "VERB", "gawas", 0) in rows


def test_the_report_counts_form_of_headwords(tmp_path):
    entries = [entry("andam", "verb"), form_of_entry("andama", "andam"), entry("gawas", "verb")]
    stats = build_from_path(write_fixture(tmp_path, entries), tmp_path / "out.tsv.gz")
    assert stats["form_of_count"] == 1


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


# ── rule 5b: Wolff roots (tunatale-tofg.3) ─────────────────────────────────────


def wolff_entry(head: str, *senses: tuple[str, str | None, str], subs: tuple = ()) -> dict:
    """A Wolff lexicon entry (``wolff_lexicon.json``'s shape): (pos, class formula, gloss) root
    senses, and sub-entries as (label, the same sense triples)."""

    def form(label, rows):
        senses = [{"pos": p, "n": None, "cls": c, "gloss": g, "phrase": None, "ex": None} for p, c, g in rows]
        return {"label": label, "wolff": label, "hb": label, "short": [], "xref": None, "senses": senses}

    return {"id": head, "heads": [head], "forms": [form(head, senses), *(form(lab, rows) for lab, rows in subs)]}


def wolff(*entries: dict):
    from app.plugins.languages.ceb.affixes import lexicon

    return lexicon(entries)


BILAR = wolff_entry("bilar", ("n", None, "vigil"), ("v", "A; b", "keep a wake"))
SABUT = wolff_entry("sabut", ("v", "A2", "understand"))


def test_an_unknown_inflected_word_gets_its_wolff_root():
    """mobilar has no row (Wiktionary heads bilar as a noun only); Wolff's bilar is a
    verb at its root that takes mu-, and bilar is a word of the table."""
    rows = {("bilar", "NOUN", "bilar", 1)}
    assert apply_wolff_roots(rows, ["mobilar"], wolff(BILAR)) == rows | {("mobilar", "VERB", "bilar", 1)}


def test_a_word_read_as_its_own_adjective_moves_to_its_root():
    """kasabot was its own adjective, so a lesson's kasabot never reached the sabot card
    (the owner moved the live card by hand, 2026-10-22). Its own reading stays, as a non-default."""
    rows = {("kasabot", "ADJ", "kasabot", 1), ("sabot", "VERB", "sabot", 1)}
    assert apply_wolff_roots(rows, ["kasabot"], wolff(SABUT)) == {
        ("kasabot", "ADJ", "sabot", 1),
        ("kasabot", "ADJ", "kasabot", 0),
        ("sabot", "VERB", "sabot", 1),
    }


def test_a_root_that_is_a_noun_at_wolffs_root_takes_no_forms():
    """A root whose own block in Wolff is all noun, its verbs only in sub-entries, is not
    a verb root; the table's non-default VERB reading of the word does not count."""
    root = wolff_entry("dagat", ("n", None, "sea"), subs=(("paN-", [("v", "A; a", "fish")]),))
    rows = {("dagat", "NOUN", "dagat", 1), ("dagat", "VERB", "dagat", 0)}
    assert apply_wolff_roots(rows, ["nadagat"], wolff(root)) == rows


def test_a_noun_reading_is_never_moved():
    """pagkaon 'food' is a word in its own right; the corpus diff showed nouns are where
    the decoder goes wrong (barangay as bárang + -ay)."""
    rows = {("pagkaon", "NOUN", "pagkaon", 1), ("kaon", "VERB", "kaon", 1)}
    assert apply_wolff_roots(rows, ["pagkaon"], wolff(wolff_entry("káun", ("v", "A; a", "eat")))) == rows


def test_an_affix_the_root_does_not_take_moves_nothing():
    rows = {("bilar", "NOUN", "bilar", 1)}
    unclassed = wolff_entry("bilar", ("v", None, "keep a wake"))
    assert apply_wolff_roots(rows, ["mobilar"], wolff(unclassed)) == rows


@pytest.mark.parametrize(
    ("word", "root", "table_root"),
    [
        # ka-an also makes nouns that have drifted from the root
        ("kalibotan", "líbut", "libot"),  # 'world', from 'go around'
        # §6.2's paN- and pag- are derivations, and where Cebuano lexicalizes most
        ("pangulo", "úlu", "ulo"),  # 'president', from 'head'
        ("pagtuo", "túu", "tuo"),  # 'belief', from 'believe'
    ],
)
def test_a_derivational_affix_moves_nothing(word, root, table_root):
    """Only the chart's inflections, and ma-un, move a word: the owner keeps the lexical
    derivations their own words (pagkaon, kamatayon, paagi; 2026-10-22)."""
    rows = {(table_root, "VERB", table_root, 1)}
    lex = wolff(wolff_entry(root, ("v", "AN; a", "a verb that takes paN-, mag- and -an")))
    assert apply_wolff_roots(rows, [word], lex) == rows


def test_a_word_under_five_letters_moves_nothing():
    """naka, maka: too short to tell from a function word (the corpus diff's ka + na-)."""
    rows = {("ulo", "VERB", "ulo", 1)}
    lex = wolff(wolff_entry("ulu", ("v", "A; a", "head")))
    assert apply_wolff_roots(rows, ["uloa"], lex) == rows
    assert ("uloha", "VERB", "ulo", 1) in apply_wolff_roots(rows, ["uloha"], lex)


def test_the_e_chooses_between_two_table_words_wolff_spells_alike():
    """Wolff's bílin is the table's bilin or its belen; ibilin has no e, so bilin."""
    rows = {("bilin", "VERB", "bilin", 1), ("belen", "NOUN", "belen", 1)}
    lex = wolff(wolff_entry("bílin", ("v", "A; c", "leave behind")))
    assert ("ibilin", "VERB", "bilin", 1) in apply_wolff_roots(rows, ["ibilin"], lex)


def test_a_root_two_table_words_spell_moves_nothing():
    """o and u are one vowel to Wolff: with kuyog and kuyug both words of the table, no row."""
    rows = {("kuyog", "VERB", "kuyog", 1), ("kuyug", "NOUN", "kuyug", 1)}
    assert apply_wolff_roots(rows, ["mikuyog"], wolff(wolff_entry("kúyug", ("v", "A; a", "go along")))) == rows


def test_a_hand_listed_word_is_left_to_its_hand_row():
    """natawo 'born' is listed in closed_class.tsv to keep itself; Wolff's táwu root block
    has 'be born' among its senses, so only the list keeps it from tawo 'person'."""
    tawu = wolff_entry("táwu", ("n", None, "person"), ("v", "B", "be born"))
    rows = {("tawo", "NOUN", "tawo", 1), ("natawo", "VERB", "natawo", 1)}
    assert ("natawo", "VERB", "tawo", 1) in apply_wolff_roots(rows, ["natawo"], wolff(tawu))
    assert apply_wolff_roots(rows, ["natawo"], wolff(tawu), keep={"natawo"}) == rows


def test_a_word_another_rule_already_rooted_is_untouched():
    rows = {("mipalit", "VERB", "palit", 1), ("palit", "VERB", "palit", 1)}
    assert apply_wolff_roots(rows, ["mipalit"], wolff(wolff_entry("palit", ("v", "A; a", "buy")))) == rows


def test_build_from_path_applies_wolff_roots_to_the_corpus_words(tmp_path):
    frequency = tmp_path / "freq.tsv.gz"
    with gzip.open(frequency, "wt", encoding="utf-8") as fh:
        fh.write("#total_tokens=9\nmobilar\t5\nbilar\t4\n")
    out = tmp_path / "out.tsv.gz"
    stats = build_from_path(
        write_fixture(tmp_path, [entry("bilar", "noun")]), out, frequency=frequency, wolff=wolff(BILAR)
    )
    assert stats["wolff_roots"] == 1
    with gzip.open(out, "rt", encoding="utf-8") as fh:
        assert "mobilar\tVERB\tbilar\t1" in fh.read().splitlines()


def test_wolffs_i_meets_the_tables_e_only_in_a_word_spelled_with_e():
    """Wolff writes Spanish e as i, so his bílin spells the table's belen (nativity scene)
    as well as bilin: gibilin went to belen. A root with e needs a word with e."""
    rows = {("depensa", "NOUN", "depensa", 1)}
    lex = wolff(wolff_entry("dipinsa", ("v", "A; b", "defend")))
    assert ("gidepensahan", "VERB", "depensa", 1) in apply_wolff_roots(rows, ["gidepensahan"], lex)
    assert apply_wolff_roots(rows, ["gidipinsahan"], lex) == rows


def test_a_linker_form_of_a_word_is_not_a_root_of_its_own():
    """nakitang is nakita + the linker -ng, not na- + a root kitang."""
    rows = {("kitang", "VERB", "kitang", 1), ("nakita", "VERB", "kita", 1)}
    lex = wolff(wolff_entry("kitang", ("v", "A; a", "a made-up root")))
    assert apply_wolff_roots(rows, ["nakitang"], lex) == rows
    without = {("kitang", "VERB", "kitang", 1)}
    assert ("nakitang", "VERB", "kitang", 1) in apply_wolff_roots(without, ["nakitang"], lex)


def test_a_root_of_two_letters_moves_nothing():
    """magda (a name) as mag- + dá."""
    rows = {("da", "VERB", "da", 1)}
    assert apply_wolff_roots(rows, ["magda"], wolff(wolff_entry("dá", ("v", "A; a", "take")))) == rows


def test_only_a_content_word_is_a_root():
    """napud is na + the particle pud; liboan 'thousands' stays its own word (the owner,
    2026-10-22: the -an tens are words of their own)."""
    lex = wolff(wolff_entry("pud", ("v", "A; a", "a made-up verb")), wolff_entry("libu", ("v", "A; b", "count")))
    rows = {("pud", "PART", "pud", 1), ("libo", "NUM", "libo", 1), ("libo", "NOUN", "libo", 0)}
    assert apply_wolff_roots(rows, ["napud", "liboan"], lex) == rows
    # with the bare words in the corpus too: a word the table excludes is not respelled from it
    assert apply_wolff_roots(rows, ["napud", "liboan", "pud", "libo"], lex) == rows


def test_a_pronouns_homograph_is_a_root():
    """kita is 'we' by default and Wiktionary may have no verb reading at all; Wolff's kítà
    'see' is a verb at its root, and nakit-an is kita + na-an."""
    rows = {("kita", "PRON", "kita", 1)}
    lex = wolff(wolff_entry("kítà", ("v", "A; b", "see")))
    assert ("nakit-an", "VERB", "kita", 1) in apply_wolff_roots(rows, ["nakit-an"], lex)


def test_a_root_wiktionary_defaults_to_a_determiner_is_a_root_by_its_other_readings():
    """hurot 'used up' is a DET by Wiktionary's default, and a verb besides."""
    rows = {("hurot", "DET", "hurot", 1), ("hurot", "VERB", "hurot", 0)}
    lex = wolff(wolff_entry("hurut", ("v", "B; a", "be used up")))
    assert ("nahurot", "VERB", "hurot", 1) in apply_wolff_roots(rows, ["nahurot"], lex)


def test_a_root_the_table_lacks_is_spelled_as_the_corpus_spells_it():
    """apil 'join' is not in Wiktionary, so moapil had no table word to root to. The bare
    root in the corpus gives the spelling, the commonest first (apil before apel)."""
    lex = wolff(wolff_entry("apil", ("v", "A; a", "join")))
    assert apply_wolff_roots(set(), ["moapil", "apil", "apel"], lex) == {("moapil", "VERB", "apil", 1)}


def test_a_root_neither_the_table_nor_the_corpus_spells_moves_nothing():
    lex = wolff(wolff_entry("apil", ("v", "A; a", "join")))
    assert apply_wolff_roots(set(), ["moapil"], lex) == set()
