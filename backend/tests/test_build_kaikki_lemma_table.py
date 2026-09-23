"""The Tagalog kaikki lemma-table builder (tunatale-w4m7.6).

Pins the builder's RULES against a tiny hand-written JSONL fixture — never the
124 MB extract. The committed table itself is the oracle exercised by
``tests/test_tagalog_lemma_table.py``; these tests pin the shape of the rules
that produce it, probing near-misses BETWEEN the golden cases the way the
delegation audit demands.
"""

from __future__ import annotations

import gzip
import json

from scripts.build_kaikki_lemma_table import (
    apply_shorter_root,
    assign_defaults,
    build_from_path,
    extract,
    linker_rows,
    read_golden_matches,
)

# ── fixture helpers ───────────────────────────────────────────────────────────


def entry(word: str, pos: str, forms: tuple[str, ...] = (), ets: tuple[tuple[str, dict], ...] = ()) -> dict:
    e: dict = {"word": word, "pos": pos, "forms": [{"form": f} for f in forms]}
    if ets:
        e["etymology_templates"] = [{"name": name, "args": args} for name, args in ets]
    return e


def write_fixture(tmp_path, entries: list[dict]):
    path = tmp_path / "fixture.jsonl"
    path.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")
    return path


AFFIX = ("infix", {"1": "tl", "2": "kain", "3": "um"})
ETY_AF = ("ety", {"1": "tl", "2": ":af", "3": "mag-", "4": "sabi", "text": "+"})
PREFIX = ("prefix", {"1": "tl", "2": "mag-", "3": "hanap"})


# ── extract: POS map, roots, surfaces ─────────────────────────────────────────


def test_every_mapped_pos_produces_a_self_headword_row():
    entries = [
        entry(w, p)
        for w, p in [
            ("bahay", "noun"),
            ("kain", "verb"),
            ("maganda", "adj"),
            ("bihira", "adv"),
            ("ako", "pron"),
            ("ang", "det"),
            ("isa", "num"),
            ("sa", "prep"),
            ("at", "conj"),
            ("aba", "intj"),
            ("ba", "particle"),
            ("Juan", "name"),
        ]
    ]
    verb_surfaces, nonverb, counts = extract(entries)
    assert verb_surfaces == {}
    assert set(nonverb) == {"bahay", "maganda", "bihira", "ako", "ang", "isa", "sa", "at", "aba", "ba", "Juan"}
    assert nonverb["at"] == {"CCONJ"}
    assert nonverb["Juan"] == {"PROPN"}
    assert counts["verb"] == 1  # a verb with no root and no forms contributes no rows, but is counted


def test_an_unmapped_pos_is_dropped_entirely():
    verb_surfaces, nonverb, counts = extract([entry("xyz", "symbol"), entry("abc", "affixy")])
    assert verb_surfaces == {} and nonverb == {}
    assert counts["symbol"] == 1 and counts["affixy"] == 1


def test_affix_template_root_comes_from_second_arg_onward():
    verb_surfaces, _, _ = extract([entry("kumain", "verb", ("kumain", "kumakain"), (AFFIX,))])
    assert verb_surfaces == {"kumain": {"kain"}, "kumakain": {"kain"}}


def test_prefix_affix_is_skipped_and_third_arg_is_the_root():
    verb_surfaces, _, _ = extract([entry("maghanap", "verb", ("maghanap",), (PREFIX,))])
    assert verb_surfaces == {"maghanap": {"hanap"}}


def test_ety_colon_af_template_root_comes_from_third_arg_onward():
    verb_surfaces, _, _ = extract([entry("magsabi", "verb", ("magsabi", "nagsabi"), (ETY_AF,))])
    assert verb_surfaces == {"magsabi": {"sabi"}, "nagsabi": {"sabi"}}


def test_a_verb_with_no_root_is_skipped():
    verb_surfaces, _, _ = extract(
        [entry("kainin", "verb", ("kainin",), (("inh", {"1": "tl", "2": "poz-pro", "3": "*kain"}),))]
    )
    assert verb_surfaces == {}


def test_surfaces_with_spaces_or_empty_are_dropped_but_the_word_stays():
    verb_surfaces, _, _ = extract([entry("kumain", "verb", ("kumakain", "ba ka", "", "kain"), (AFFIX,))])
    assert verb_surfaces == {"kumain": {"kain"}, "kumakain": {"kain"}, "kain": {"kain"}}


def test_a_root_candidate_that_is_an_affix_itself_is_skipped():
    # -um- / -in- style infixes inside the template args: only the first
    # non-affix arg counts, and later affix-looking args never become roots.
    ets = (("infix", {"1": "tl", "2": "bigay", "3": "in"}),)
    verb_surfaces, _, _ = extract([entry("binigay", "verb", ("binigay",), ets)])
    assert verb_surfaces == {"binigay": {"bigay"}}


# ── root extraction against the shapes the REAL extract uses ──────────────────
# Orchestrator audit, 2026-09-23. The fixtures above hyphen-mark every affix; the
# extract does not, and the first build keyed 6,167 verb rows on `baon<t:bury>`
# style strings and 722 on a bare affix (`aandar` → `um`, `pahingi` → `pa`),
# while every golden test stayed green. Each case below is copied from the
# extract's own template args.


def roots_of(*ets: tuple[str, dict]) -> set[str]:
    verb_surfaces, _, _ = extract([entry("x", "verb", (), ets)])
    return verb_surfaces.get("x", set())


def test_a_prefix_template_gives_its_affix_bare_so_the_root_is_the_last_arg():
    assert roots_of(("prefix", {"1": "tl", "2": "pa", "3": "hingi"})) == {"hingi"}
    assert roots_of(("prefix", {"1": "tl", "2": "in", "3": "antok"})) == {"antok"}
    # The discriminating case: an affix LONGER than its root, where a
    # longest-word rule would pick the affix.
    assert roots_of(("prefix", {"1": "tl", "2": "pinaka", "3": "una"})) == {"una"}


def test_infix_and_suffix_templates_give_the_root_first():
    assert roots_of(("infix", {"1": "tl", "2": "andar", "3": "um"})) == {"andar"}
    assert roots_of(("suffix", {"1": "tl", "2": "dala", "3": "hin"})) == {"dala"}
    # Equal length, where the later-arg tie-break would pick the suffix.
    assert roots_of(("suffix", {"1": "tl", "2": "isa", "3": "han"})) == {"isa"}


def test_a_one_arg_affix_template_names_no_root():
    # {{prefix|tl|mag}} beside a separate compound template (magdalawang-isip).
    assert roots_of(("prefix", {"1": "tl", "2": "mag"})) == set()


def test_inline_modifiers_are_stripped_before_the_affix_test():
    assert roots_of(("ety", {"1": "tl", "2": ":af", "3": "ma-<id:stative prefix>", "4": "buhay"})) == {"buhay"}
    assert roots_of(("af", {"1": "tl", "2": "basa<id:wet>", "3": "-in"})) == {"basa"}
    assert roots_of(("ety", {"1": "tl", "2": ":af", "3": "man-", "4": "tirahan<id:residence>"})) == {"tirahan"}
    assert roots_of(("af", {"1": "tl", "2": "bati<t:whisk; beat><id:beat>", "3": "-in"})) == {"bati"}


def test_a_modifier_on_an_affix_does_not_turn_it_into_a_root():
    # `bulahaw-<t:garrulity>` is still hyphen-marked once the modifier goes.
    assert roots_of(("af", {"1": "tl", "2": "bulahaw-<t:garrulity>", "3": "-in"})) == set()


def test_an_affix_typed_without_its_hyphen_loses_to_the_longer_root():
    assert roots_of(("ety", {"1": "tl", "2": ":af", "3": "i", "4": "halili"})) == {"halili"}


def test_an_equal_length_tie_goes_to_the_later_arg():
    # A hyphenless affix is a prefix, so it comes first: mang + amoy → amoy.
    assert roots_of(("ety", {"1": "tl", "2": ":af", "3": "mang", "4": "amoy"})) == {"amoy"}


def test_table_metadata_in_forms_is_not_a_surface():
    forms = [
        {"form": "kumakain"},
        {"form": "nag-aaral"},
        {"form": "actor", "tags": ["error-unrecognized-form"]},
        {"form": "-", "tags": ["error-unrecognized-form"]},
        {"form": "-in", "tags": ["error-unrecognized-form"]},
        {"form": "mag-"},
        {"form": "-um-"},
        {"form": "⁠—"},
        {"form": "ᜃᜓᜋᜁᜈ᜔", "tags": ["Baybayin"]},
        {"form": "tl-infl-um", "tags": ["inflection-template"]},
        {"form": "no-table-tags", "tags": ["table-tags"]},
    ]
    e = {"word": "kumain", "pos": "verb", "forms": forms, "etymology_templates": [{"name": AFFIX[0], "args": AFFIX[1]}]}
    verb_surfaces, _, _ = extract([e])
    assert set(verb_surfaces) == {"kumain", "kumakain", "nag-aaral"}


# ── shorter-root rule ─────────────────────────────────────────────────────────


def test_the_shorter_root_wins_between_affixed_candidates():
    # ibigay is itself i- + bigay; the property drops the affixed candidate.
    surfaces = {"binigay": {"ibigay", "bigay"}, "ibinigay": {"ibigay"}}
    assert apply_shorter_root(surfaces)["binigay"] == {"bigay"}
    # ... and the shorter-root rule is the ONLY thing that fixes ibinigay: its
    # own entry yields ibigay, but bigay must win through the ibigay entry's
    # forms. The property drops ibigay the moment bigay is a candidate.
    assert apply_shorter_root({"ibinigay": {"ibigay", "bigay"}})["ibinigay"] == {"bigay"}


def test_unrelated_roots_survive_the_shorter_rule():
    # Near-miss: same length, or neither a substring of the other → both kept.
    assert apply_shorter_root({"sabi": {"sabi", "tahi"}})["sabi"] == {"sabi", "tahi"}
    assert apply_shorter_root({"sabi": {"sabi", "sabiin"}})["sabi"] == {"sabi"}


# ── is_default ────────────────────────────────────────────────────────────────


def test_a_surface_that_is_itself_a_non_verb_headword_keeps_that_default():
    entries = [
        entry("sabi", "noun"),
        entry("magsabi", "verb", ("sabi",), (ETY_AF,)),
    ]
    verb_surfaces, nonverb, _ = extract(entries)
    rows = assign_defaults(verb_surfaces, nonverb, linker_rows(nonverb, set()))
    by = {(s, u, lem): d for s, u, lem, d in rows}
    assert by[("sabi", "NOUN", "sabi")] == 1
    assert by[("sabi", "VERB", "sabi")] == 0


def test_an_interjection_reading_never_outranks_the_verb_reading():
    # makita is BOTH an INTJ headword ("let's see; we'll see") and a VERB form
    # of the root kita. An interjection is a quoted verbal phrase, not an
    # independent headword, so the VERB reading is the default — the golden's
    # `makita` → `kita`. The INTJ row stays as the resolver's alternative.
    entries = [
        entry("makita", "intj"),
        entry(
            "makita",
            "verb",
            ("makita", "nakita", "makikita"),
            (("ety", {"1": "tl", "2": ":af", "3": "ma-", "4": "kita"}),),
        ),
    ]
    verb_surfaces, nonverb, _ = extract(entries)
    rows = assign_defaults(verb_surfaces, nonverb, linker_rows(nonverb, set()))
    by = {(s, u, lem): d for s, u, lem, d in rows}
    assert by[("makita", "VERB", "kita")] == 1
    assert by[("makita", "INTJ", "makita")] == 0


def test_an_interjection_only_surface_keeps_its_own_default():
    # No other reading behind it: the INTJ row alone must still default to
    # itself — excluding INTJ from rule 6a must not drop the row's default.
    # (aray results in no `-ng` linker surface: consonant-final.)
    entries = [entry("aray", "intj")]
    verb_surfaces, nonverb, _ = extract(entries)
    rows = assign_defaults(verb_surfaces, nonverb, linker_rows(nonverb, set()))
    assert rows == {("aray", "INTJ", "aray", 1)}


def test_a_verb_surface_defaults_to_the_root_with_the_most_forms():
    entries = [
        entry("sabi", "verb", ("sabihin", "sinabi", "sasabihin"), (ETY_AF,)),
        entry("tahi", "verb", ("sabihin",), (("af", {"1": "tl", "2": "tahi", "3": "-in"}),)),
    ]
    verb_surfaces, nonverb, _ = extract(entries)
    rows = assign_defaults(verb_surfaces, nonverb, linker_rows(nonverb, set()))
    by = {(s, u, lem): d for s, u, lem, d in rows}
    # sabi has 3 forms, tahi only 1 → the sabi reading is the default.
    assert by[("sabihin", "VERB", "sabi")] == 1
    assert by[("sabihin", "VERB", "tahi")] == 0


def test_a_verb_surface_tie_breaks_to_the_alphabetically_first_root():
    entries = [
        entry("kata", "verb", ("kita",), (("af", {"1": "tl", "2": "kata", "3": "-in"}),)),
        entry("kita", "verb", ("kita",), (("af", {"1": "tl", "2": "kita", "3": "-in"}),)),
    ]
    verb_surfaces, nonverb, _ = extract(entries)
    rows = assign_defaults(verb_surfaces, nonverb, linker_rows(nonverb, set()))
    by = {(s, u, lem): d for s, u, lem, d in rows}
    # One form for each root, and kata < kita alphabetically.
    assert by[("kita", "VERB", "kata")] == 1
    assert by[("kita", "VERB", "kita")] == 0


def test_every_surface_gets_exactly_one_default_row():
    entries = [
        entry("sabi", "noun"),
        entry("magsabi", "verb", ("sabi", "sabihin"), (ETY_AF,)),
        entry("tahi", "verb", ("sabihin",), (("af", {"1": "tl", "2": "tahi", "3": "-in"}),)),
        entry("ako", "pron"),
    ]
    verb_surfaces, nonverb, _ = extract(entries)
    rows = assign_defaults(verb_surfaces, nonverb, linker_rows(nonverb, set()))
    from collections import Counter

    assert all(v == 1 for v in Counter(s for s, _u, _l, d in rows if d).values())
    assert len({s for s, _u, _l, d in rows if d}) == len({s for s, _u, _l, _d in rows})


# ── the -ng linker ────────────────────────────────────────────────────────────


def test_linker_rows_are_emitted_for_n_and_vowel_ending_headwords():
    nonverb = {"ako": {"PRON"}, "namin": {"ADJ"}, "lalaki": {"NOUN"}}
    assert linker_rows(nonverb, set()) == {
        ("akong", "PRON", "ako"),
        ("naming", "ADJ", "namin"),
        ("lalaking", "NOUN", "lalaki"),
    }


def test_a_candidate_already_a_surface_gets_no_linker_row():
    # `ng` is a headword; `ngahan` would be its linker candidate only if it
    # ended in n or a vowel — instead, an unrelated headword collides with an
    # existing surface and loses outright (precedence 1 of the brief).
    nonverb = {"ako": {"PRON"}}
    base = {"akong"}  # already a surface (e.g. a verb form)
    assert linker_rows(nonverb, base) == set()


def test_the_n_plus_g_source_wins_over_the_vowel_plus_ng_one():
    # naming → namin (n + g), never nami (na + mi? no: nami + ng) — ordering is
    # load-bearing even though BOTH headwords are real.
    nonverb = {"namin": {"ADJ"}, "nami": {"NOUN"}}
    assert linker_rows(nonverb, set()) == {("naming", "ADJ", "namin")}


def test_a_consonant_final_headword_makes_no_linker():
    nonverb = {"lakad": {"VERB"}}
    assert linker_rows(nonverb, set()) == set()


# ── end-to-end: the file the builder writes ───────────────────────────────────


def test_build_from_path_writes_the_tables_we_spec(tmp_path):
    entries = [
        entry("kain", "noun"),
        entry("kumain", "verb", ("kumain", "kakain", "ba ka"), (AFFIX,)),
        entry("ako", "pron"),
        # Bigay-shaped trio mirroring the real data's root-candidate tangle:
        # binigay's own entry yields bigay, ibinigay's yields ibigay, and
        # ibigay's own entry yields bigay — so the shorter-root property is what
        # makes ALL of binigay/ibinigay/ibibigay resolve to bigay.
        entry("binigay", "verb", ("binigay",), (("infix", {"1": "tl", "2": "bigay", "3": "in"}),)),
        entry(
            "ibigay",
            "verb",
            ("ibigay", "ibinigay", "ibibigay"),
            (("ety", {"1": "tl", "2": ":af", "3": "i-", "4": "bigay"}),),
        ),
        entry("ibinigay", "verb", ("ibinigay", "binigay"), (("infix", {"1": "tl", "2": "ibigay", "3": "in"}),)),
    ]
    src = write_fixture(tmp_path, entries)
    out = tmp_path / "out.tsv.gz"
    stats = build_from_path(src, out)
    with gzip.open(out, "rt", encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    assert lines[0] == f"#source_version=kaikki-{stats['sha256'][:12]}"
    rows = set(lines[1:])
    assert ("kumain\tVERB\tkain\t1") in rows
    assert ("kakain\tVERB\tkain\t1") in rows
    assert "ba ka" not in "\n".join(lines)
    # binigay has candidates {ibigay, bigay}; the shorter root wins, and so does
    # ibinigay (via ibigay's forms) and ibibigay (via ibigay's own roots).
    assert ("binigay\tVERB\tbigay\t1") in rows
    assert ("ibinigay\tVERB\tbigay\t1") in rows
    assert ("ibibigay\tVERB\tbigay\t1") in rows
    # akong is the linker surface of the pronoun ako.
    assert ("akong\tPRON\tako\t1") in rows
    src = write_fixture(tmp_path, entries)
    out = tmp_path / "out.tsv.gz"
    stats = build_from_path(src, out)
    with gzip.open(out, "rt", encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    assert lines[0] == f"#source_version=kaikki-{stats['sha256'][:12]}"
    rows = set(lines[1:])
    assert ("kumain\tVERB\tkain\t1") in rows
    assert ("kakain\tVERB\tkain\t1") in rows
    assert "ba ka" not in "\n".join(lines)
    # binigay has candidates {ibigay, bigay}; the shorter root wins.
    assert ("binigay\tVERB\tbigay\t1") in rows
    assert ("ibinigay\tVERB\tbigay\t1") in rows  # ibigay (superstring) dropped
    # akong is the linker surface of the pronoun ako.
    assert ("akong\tPRON\tako\t1") in rows


def test_golden_matches_counts_the_three_families(tmp_path):
    src = write_fixture(tmp_path, [entry("bahay", "noun"), entry("dalawang", "noun")])
    out = tmp_path / "out.tsv.gz"
    stats = build_from_path(src, out)
    # The fixture deliberately contains only one headword correctly (bahay);
    # dalawang is NOT in the golden self-headword list and there is no linker
    # surface for it (ends in g). So exactly one of the 72 golden checks hits.
    assert stats["golden"] == (1, 72)


def test_writing_the_same_table_twice_is_reproducible(tmp_path):
    entries = [entry("kain", "noun"), entry("kumain", "verb", ("kumain",), (AFFIX,))]
    src = write_fixture(tmp_path, entries)
    out1, out2 = tmp_path / "a.tsv.gz", tmp_path / "b.tsv.gz"
    build_from_path(src, out1)
    build_from_path(src, out2)
    assert out1.read_bytes() == out2.read_bytes()


def test_golden_reporter_reads_rows_from_the_locked_test():
    # The golden literal table is the shared oracle: reading it back from a row
    # set must agree on which checks pass, so a builder regression shows up as a
    # count change in the report, not a silent miss in the locked test.
    rows = {
        ("kumain", "VERB", "kain", 1),
        ("nakita", "VERB", "kita", 1),
        ("kita", "PRON", "kita", 1),
        ("sabi", "NOUN", "sabi", 1),
        ("namin", "ADJ", "namin", 1),
        ("naming", "ADJ", "namin", 1),
    }
    matches, total = read_golden_matches(rows)
    assert set(matches) == {"kumain", "nakita", "naming"}
    assert total == 72
    assert matches["kumain"] == "kain" and matches["naming"] == "namin"
    # The self-headword check is a lookup on the surface alone: kita/sabi/namin
    # are NOT golden surfaces, so their presence changes nothing.
    assert "binigay" not in matches  # absent rows are misses, not half-matches
