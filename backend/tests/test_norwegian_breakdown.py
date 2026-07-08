"""Tests for the Norwegian breakdown module.

Golden values are human-confirmed (iterated via the CLI preview) and must NOT be
edited to match code. Key design decisions they pin:

- Compound splitting is frequency-gated: a free stem must clear ``_MAX_STEM_RANK``
  so junk fragments (``poli``, ``tie``) can't form bogus splits (politiet stays
  whole, not ``poli|tie|t``).
- Norwegian linking elements (fuge-s / fuge-e) are honoured (``stor|tings``).
- Derivational suffixes (``-het``, ``-lig``, ``-ning``) are syllable-level only,
  never isolated as compound chunks (``arbeidsledighet`` stays whole).
- A doubled consonant stays with the preceding syllable for TTS vowel length
  (``etter`` -> ``ett|er``, ``mannen`` -> ``mann|en``), and the peel guard keeps
  a stem geminate intact (``snømann`` -> ``snø|mann``, not ``snø|man|n``).
- The compound buildup keeps the article on its stem (``teamet``), speaking each
  morpheme whole, then breaking it, then rebuilding.
"""

from app.generation.norwegian_breakdown import (
    _is_content_stem,
    _load_ranked_lexicon,
    _merge_geminates,
    _segment_surface,
    build_norwegian_breakdown,
    load_no_lexicon,
    segment_compound,
    slow_norwegian_word,
    syllabify_morpheme,
)

# -- Lexicon loader --------------------------------------------------------


def test_load_no_lexicon_contains_expected_words():
    lexicon = load_no_lexicon()
    needed = {
        "etterforskning",
        "team",
        "fly",
        "plass",
        "snø",
        "mann",
        "barne",
        "hage",
        "forskning",
        "kjærlighet",
        "mannen",
        "jeg",
        "plassen",
        "teamet",
        "på",
    }
    for w in needed:
        assert w in lexicon, f"Missing from lexicon: {w}"


def test_load_no_lexicon_has_minimum_size():
    assert len(load_no_lexicon()) >= 25000


def test_ranked_lexicon_is_frequency_ordered():
    ranks = _load_ranked_lexicon()
    # Common words rank ahead of junk fragments (the whole point of the floor).
    assert ranks["politi"] < ranks["poli"]
    assert ranks["mann"] < ranks["poli"]


# -- _is_content_stem (frequency floor + suffix exclusion) -----------------


def test_is_content_stem_common_word():
    ranks = _load_ranked_lexicon()
    assert _is_content_stem("mann", ranks) is True


def test_is_content_stem_too_short():
    ranks = _load_ranked_lexicon()
    assert _is_content_stem("på", ranks) is False


def test_is_content_stem_absent():
    ranks = _load_ranked_lexicon()
    assert _is_content_stem("zzz", ranks) is False


def test_is_content_stem_below_floor_rank():
    ranks = _load_ranked_lexicon()
    # `poli` is in the lexicon but far down the tail -> not a real stem.
    assert _is_content_stem("poli", ranks) is False


def test_is_content_stem_derivational_suffix_excluded():
    ranks = _load_ranked_lexicon()
    # "het" (=hot) is common but is a derivational suffix, not a free stem.
    assert _is_content_stem("het", ranks) is False


# -- segment_compound ----------------------------------------------------


def test_segment_compound_etterforskningsteamet():
    assert segment_compound("etterforskningsteamet") == ["etter", "forsknings", "team", "et"]


def test_segment_compound_flyplassen():
    assert segment_compound("flyplassen") == ["fly", "plass", "en"]


def test_segment_compound_snoemannen():
    assert segment_compound("snømannen") == ["snø", "mann", "en"]


def test_segment_compound_snoemann_no_inflection():
    """Compound without an article splits at the stem boundary (final _segment_surface)."""
    assert segment_compound("snømann") == ["snø", "mann"]


def test_segment_compound_barnehagen():
    assert segment_compound("barnehagen") == ["barne", "hage", "n"]


def test_segment_compound_stortingsrepresentanten():
    """Linking-s (fuge) inside a deep compound: stor + ting(+s) + representant + en."""
    assert segment_compound("stortingsrepresentanten") == [
        "stor",
        "tings",
        "representant",
        "en",
    ]


def test_segment_compound_politiet_no_gibberish():
    """The frequency floor blocks poli|tie|t; politi is a simplex root."""
    assert segment_compound("politiet") == ["politiet"]


def test_segment_compound_mannen_is_single_stem():
    assert segment_compound("mannen") == ["mannen"]


def test_segment_compound_geminate_guard():
    """Peeling '-n' must not break the stem geminate of a double-n word."""
    assert segment_compound("vann") == ["vann"]


def test_segment_compound_forskning_is_single_stem():
    assert segment_compound("forskning") == ["forskning"]


def test_segment_compound_kjaerlighet_suffix_not_split():
    assert segment_compound("kjærlighet") == ["kjærlighet"]


def test_segment_compound_arbeidsledighet_stays_whole():
    """-het is a suffix, not a compound part -> the word is not over-split."""
    assert segment_compound("arbeidsledighet") == ["arbeidsledighet"]


def test_segment_compound_lexicalized_word_not_split():
    """A common simplex word that coincidentally decomposes stays whole.

    ``morgen`` (rank ~424) is more common than both ``mor`` and ``gen``, so it is
    a lexicalized simplex, not the compound mor+gen. A real compound is rarer
    than its own parts. (Reached via the final, no-inflection path.)
    """
    assert segment_compound("morgen") == ["morgen"]


def test_segment_compound_lexicalized_word_with_inflection_not_split():
    """Same guard, reached via the inflection-peel path (base decomposes >=2).

    ``prosent`` (base ``prosen`` -> pro|sen) and ``samfunnet`` (base ``samfunn``
    -> sam|funn) out-rank their parts and must stay whole, not become
    ``pro, sen, t`` / ``sam, funn, et``.
    """
    assert segment_compound("prosent") == ["prosent"]
    assert segment_compound("samfunnet") == ["samfunnet"]


def test_segment_compound_simple_word():
    assert segment_compound("jeg") == ["jeg"]


def test_segment_compound_short_base_after_inflection():
    """Peeling an inflection can leave a sub-min-length base (det -> single stem)."""
    assert segment_compound("det") == ["det"]


def test_segment_compound_non_compound():
    assert segment_compound("plassen") == ["plassen"]


def test_segment_compound_empty():
    assert segment_compound("") == []


# -- _segment_surface edge branches --------------------------------------


def test_segment_surface_none_for_uncoverable():
    ranks = _load_ranked_lexicon()
    assert _segment_surface("zzzq", ranks) is None


def test_segment_surface_first_plus_link_consumes_whole():
    """first + linking-s leaves no remainder -> that candidate is skipped."""
    ranks = _load_ranked_lexicon()
    # "forsknings" = forskning + s with nothing after -> no >=2 split, not a stem.
    assert _segment_surface("forsknings", ranks) is None


# -- syllabify_morpheme --------------------------------------------------


def test_syllabify_morpheme_forskning():
    assert syllabify_morpheme("forskning") == ["forsk", "ning"]


def test_syllabify_morpheme_forskningen():
    """Inflection + derivational suffix both peeled."""
    assert syllabify_morpheme("forskningen") == ["forsk", "ning", "en"]


def test_syllabify_morpheme_kjaerlighet_multilayer():
    """Two stacked derivational suffixes: kjær + lig + het."""
    assert syllabify_morpheme("kjærlighet") == ["kjær", "lig", "het"]


def test_syllabify_morpheme_etterforsknings_linking_and_geminate():
    assert syllabify_morpheme("etterforsknings") == ["ett", "er", "forsk", "nings"]


def test_syllabify_morpheme_geminate_plassen():
    assert syllabify_morpheme("plassen") == ["plass", "en"]


def test_syllabify_morpheme_geminate_mannen():
    assert syllabify_morpheme("mannen") == ["mann", "en"]


def test_syllabify_morpheme_geminate_etter():
    assert syllabify_morpheme("etter") == ["ett", "er"]


def test_syllabify_morpheme_no_geminate_informasjon():
    assert syllabify_morpheme("informasjon") == ["in", "for", "ma", "sjon"]


def test_syllabify_morpheme_team_loanword():
    assert syllabify_morpheme("team") == ["team"]


def test_syllabify_morpheme_short_word():
    assert syllabify_morpheme("jeg") == ["jeg"]


def test_syllabify_morpheme_empty():
    assert syllabify_morpheme("") == []


def test_syllabify_morpheme_linking_fallthrough():
    """Word ends with 's' but no derivational/inflection — falls through to standard."""
    result = syllabify_morpheme("ukes")
    assert len(result) >= 2


def test_syllabify_morpheme_loanword_with_derivational():
    """Stem is a loanword monosyllable, derivational suffix follows."""
    result = syllabify_morpheme("teamlig")
    assert "team" in result
    assert "lig" in result


# -- _merge_geminates ----------------------------------------------------


def test_merge_geminates_moves_doubled_consonant():
    assert _merge_geminates(["man", "nen"]) == ["mann", "en"]


def test_merge_geminates_leaves_non_geminate():
    assert _merge_geminates(["in", "for", "ma", "sjon"]) == ["in", "for", "ma", "sjon"]


def test_merge_geminates_single_syllable_untouched():
    assert _merge_geminates(["team"]) == ["team"]


# -- slow_norwegian_word -------------------------------------------------


def test_slow_compound_keeps_article_on_stem():
    assert slow_norwegian_word("etterforskningsteamet") == "etter, forsknings, teamet"


def test_slow_compound_flyplassen():
    assert slow_norwegian_word("flyplassen") == "fly, plassen"


def test_slow_compound_snoemannen():
    assert slow_norwegian_word("snømannen") == "snø, mannen"


def test_slow_compound_barnehagen():
    assert slow_norwegian_word("barnehagen") == "barne, hagen"


def test_slow_compound_stortings():
    assert slow_norwegian_word("stortingsrepresentanten") == "stor, tings, representanten"


def test_slow_derived_word_stays_whole():
    """-het is not isolated; a derived word is not syllable-split in the slow form."""
    assert slow_norwegian_word("arbeidsledighet") == "arbeidsledighet"


def test_slow_long_non_compound_stays_whole():
    assert slow_norwegian_word("informasjon") == "informasjon"


def test_slow_short_word_unchanged():
    assert slow_norwegian_word("mannen") == "mannen"


def test_slow_very_short_word_unchanged():
    assert slow_norwegian_word("jeg") == "jeg"


def test_slow_word_empty():
    assert slow_norwegian_word("") == ""


def test_slow_trailing_period_splits_and_reattaches():
    """A compound at a sentence boundary keeps its period but still splits."""
    assert slow_norwegian_word("flyplassen.") == "fly, plassen."


def test_slow_trailing_comma_splits_and_reattaches():
    assert slow_norwegian_word("etterforskningsteam,") == "etter, forsknings, team,"


def test_slow_leading_punctuation_preserved():
    assert slow_norwegian_word("«flyplassen") == "«fly, plassen"


def test_slow_surrounding_punctuation_non_compound():
    """Punctuation is peeled/reattached even when the core doesn't split."""
    assert slow_norwegian_word("informasjon.") == "informasjon."


def test_slow_all_punctuation_token():
    """A token with no alphabetic core is returned unchanged."""
    assert slow_norwegian_word("...") == "..."


# -- build_norwegian_breakdown -------------------------------------------


def test_breakdown_compound_full_golden_sequence():
    """The whole morpheme-first buildup, human-confirmed line-for-line."""
    assert build_norwegian_breakdown("etterforskningsteamet") == [
        "etterforskningsteamet",
        "teamet",
        "et",
        "team",
        "teamet",
        "forsknings",
        "nings",
        "forsk",
        "forsknings",
        "forskningsteamet",
        "etter",
        "er",
        "ett",
        "etter",
        "etterforskningsteamet",
    ]


def test_breakdown_compound_without_inflection():
    """Compound with no article: units carry no merged tail (buildup False branch)."""
    assert build_norwegian_breakdown("snømann") == [
        "snømann",
        "mann",
        "snø",
        "snømann",
    ]


def test_breakdown_single_stem_per_syllable():
    assert build_norwegian_breakdown("forskning") == [
        "forskning",
        "ning",
        "forsk",
        "forskning",
        "forskning",
    ]


def test_breakdown_single_stem_3_syllables():
    assert build_norwegian_breakdown("kjærlighet") == [
        "kjærlighet",
        "het",
        "lig",
        "lighet",
        "kjær",
        "kjærlighet",
        "kjærlighet",
    ]


def test_breakdown_simplex_root_no_gibberish():
    assert build_norwegian_breakdown("politiet") == [
        "politiet",
        "et",
        "ti",
        "tiet",
        "li",
        "litiet",
        "po",
        "politiet",
        "politiet",
    ]


def test_breakdown_single_syllable_word():
    assert build_norwegian_breakdown("jeg") == ["jeg", "jeg"]


def test_breakdown_empty():
    assert build_norwegian_breakdown("") == []


# -- Multi-word phrase ----------------------------------------------------


def test_breakdown_multi_word_with_compound():
    assert build_norwegian_breakdown("på flyplassen") == [
        "på flyplassen",
        "plassen",
        "en",
        "plass",
        "plassen",
        "fly",
        "på",
        "på flyplassen",
        "på flyplassen",
    ]


def test_breakdown_multi_word_non_compound():
    assert build_norwegian_breakdown("på plassen") == [
        "på plassen",
        "en",
        "plass",
        "plassen",
        "på",
        "på plassen",
        "på plassen",
    ]


def test_breakdown_three_word_phrase():
    """Three-word phrase hits the partial-append path for the middle word."""
    assert build_norwegian_breakdown("jeg er her") == [
        "jeg er her",
        "her",
        "er",
        "er her",
        "jeg",
        "jeg er her",
        "jeg er her",
    ]
