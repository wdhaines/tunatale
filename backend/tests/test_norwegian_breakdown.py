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
    _segment_surface,
    _spoken_syllable,
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


def test_is_content_stem_closed_class_excluded():
    """Closed-class function words (pronouns, conjunctions, etc.) are never
    compound stems, even though they rank well under _MAX_STEM_RANK."""
    ranks = _load_ranked_lexicon()
    for word in ("som", "mer", "men", "den", "det", "han", "hun", "seg", "jeg"):
        assert _is_content_stem(word, ranks) is False, f"{word!r} should be excluded"


def test_is_content_stem_compound_initial_only():
    """Compound-initial-only homographs are valid stems at word-initial position
    but rejected at non-initial positions."""
    ranks = _load_ranked_lexicon()
    for word in ("hver", "selv", "vår"):
        assert _is_content_stem(word, ranks, initial=True) is True, f"{word!r} should be allowed at initial position"
        assert _is_content_stem(word, ranks, initial=False) is False, (
            f"{word!r} should be rejected at non-initial position"
        )


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


# -- segment_compound: closed-class exclusion goldens ---------------------


def test_segment_compound_sommer_stays_whole():
    """'sommer' (summer) must not split into 'som'+'mer' — both are
    closed-class words that should never be compound stems."""
    assert segment_compound("sommer") == ["sommer"]


def test_segment_compound_morsom_stays_whole():
    """'morsom' (funny) must not split into 'mor'+'som'."""
    assert segment_compound("morsom") == ["morsom"]


def test_segment_compound_togstasjon_fewer_parts():
    """'togstasjon' (train station) splits into two real stems, not three —
    both candidates share the anchor 'tog', so fewer parts wins the tie."""
    assert segment_compound("togstasjon") == ["tog", "stasjon"]


# -- segment_compound: preposition eligibility regression ------------------


def test_segment_compound_etterforskning_preposition_eligible():
    """'etter' is a preposition and MUST remain a valid compound stem."""
    assert segment_compound("etterforskning") == ["etter", "forskning"]


def test_segment_compound_forstand_lexicalized_whole():
    """'forstand' (understanding) is a lexicalized derivative, not a transparent
    compound — it stays whole.  The rank-based guard can't catch it (forstand
    does not outrank stand), so it is in the human-ratified override set."""
    assert segment_compound("forstand") == ["forstand"]


# -- segment_compound: for-derivatives that DO split (for stays eligible) ---


def test_segment_compound_fortid_splits():
    """'fortid' (before-time) is a transparent for-derivative that splits."""
    assert segment_compound("fortid") == ["for", "tid"]


def test_segment_compound_formiddag_splits():
    """'formiddag' (fore-midday) is a transparent for-derivative that splits."""
    assert segment_compound("formiddag") == ["for", "middag"]


# -- segment_compound: compound-initial-only homographs --------------------


def test_segment_compound_hverdag():
    """'hver' is compound-productive at word-initial position."""
    assert segment_compound("hverdag") == ["hver", "dag"]


def test_segment_compound_hverdagen():
    assert segment_compound("hverdagen") == ["hver", "dag", "en"]


def test_segment_compound_selvtillit():
    """'selv' is compound-productive at word-initial position."""
    assert segment_compound("selvtillit") == ["selv", "tillit"]


# -- segment_compound: s-overlap compounds ---------------------------------


def test_segment_compound_busstasjon_s_overlap():
    """'busstasjon' splits at the doubled-consonant boundary: surface ['bus',
    'stasjon'], spoken 'buss, stasjon'."""
    assert segment_compound("busstasjon") == ["bus", "stasjon"]


def test_slow_busstasjon_s_overlap():
    """Overlap-truncated part is voiced with doubled final consonant."""
    assert slow_norwegian_word("busstasjon") == "buss, stasjon"


def test_breakdown_busstasjon_s_overlap():
    """Breakdown contains 'buss' as the spoken form, never bare 'bus', and no
    step ever spells the triple-s join ('bussstasjon')."""
    bd = build_norwegian_breakdown("busstasjon")
    assert "buss" in bd
    assert "stasjon" in bd
    assert "bus" not in bd  # the truncated surface is never voiced alone
    for item in bd:
        assert "sss" not in item, f"triple-s join leaked into {item!r}"


def test_segment_compound_fjellandskap_s_overlap():
    """'fjellandskap' splits at the ll-boundary: surface ['fjel', 'landskap']."""
    assert segment_compound("fjellandskap") == ["fjel", "landskap"]


def test_segment_compound_snomann_no_s_overlap():
    """'snømann' must NOT trigger s-overlap — its nm boundary is not a
    doubled consonant."""
    assert segment_compound("snømann") == ["snø", "mann"]


def test_spoken_part_no_false_doubling_for_full_lexeme_parts():
    """A matching consonant boundary is NOT enough to double: 'bok|klubb' and
    'sol|lys' have the same surface shape as an overlap truncation, but 'bok'
    and 'sol' are full lexemes (long vowels) — voicing 'bokk'/'soll' would be
    wrong. Only a non-stem surface whose doubled form IS a stem doubles
    ('bus' → 'buss')."""
    assert slow_norwegian_word("bokklubb") == "bok, klubb"
    assert slow_norwegian_word("sollys") == "sol, lys"
    bd = build_norwegian_breakdown("bokklubb")
    assert "bok" in bd
    assert "bokk" not in bd


def test_segment_surface_overlap_candidate_beats_existing_best():
    """The overlap comparison branch: a candidate formed at a doubled-consonant
    boundary competes against an already-set best and WINS on anchor rank.

    Synthetic 'fooffbar' (descending `end` scan): at end=5 the normal path sets
    best=['fooff','bar'] (anchor 100).  At end=4 the ff-boundary overlap fires:
    spoken 'foof'+'f'='fooff' passes the stem gate, rest 'fbar' (anchor 5) —
    5 < 100, so the overlap candidate ['foof','fbar'] takes over."""
    ranks = {
        "fooff": 100,  # normal first part at end=5 AND overlap spoken at end=4
        "bar": 200,  # rest after the normal split
        "fbar": 5,  # rest after the overlap split (the winning anchor)
    }
    assert _segment_surface("fooffbar", ranks) == ["foof", "fbar"]


def test_segment_surface_overlap_candidate_loses_to_existing_best():
    """Same shape, but the overlap candidate's anchor is WEAKER than the
    existing best's — the comparison branch keeps the normal split."""
    ranks = {
        "fooff": 100,
        "bar": 200,
        "fbar": 7000,  # overlap rest is the weakest anchor: min(8000,7000) > 100
    }
    assert _segment_surface("fooffbar", ranks) == ["fooff", "bar"]


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


def test_syllabify_morpheme_etterforsknings_linking_raw():
    """Syllables are raw (et|ter); geminate lengthening happens at buildup."""
    assert syllabify_morpheme("etterforsknings") == ["et", "ter", "forsk", "nings"]


def test_syllabify_morpheme_geminate_plassen_raw():
    assert syllabify_morpheme("plassen") == ["plas", "sen"]


def test_syllabify_morpheme_geminate_mannen_raw():
    assert syllabify_morpheme("mannen") == ["man", "nen"]


def test_syllabify_morpheme_geminate_etter_raw():
    assert syllabify_morpheme("etter") == ["et", "ter"]


def test_syllabify_morpheme_finne_not_over_peeled():
    """-inne is an agent suffix (venninne); it must not peel off finne -> f|inne."""
    assert syllabify_morpheme("finne") == ["fin", "ne"]


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


# -- _spoken_syllable ----------------------------------------------------


def test_spoken_syllable_lengthens_left_of_geminate():
    """et|ter -> the left chunk voiced alone is 'ett' (short vowel), right is 'ter'."""
    assert _spoken_syllable(["et", "ter"], 0) == "ett"
    assert _spoken_syllable(["et", "ter"], 1) == "ter"


def test_spoken_syllable_mannen():
    assert _spoken_syllable(["man", "nen"], 0) == "mann"
    assert _spoken_syllable(["man", "nen"], 1) == "nen"


def test_spoken_syllable_non_geminate_untouched():
    assert _spoken_syllable(["in", "for", "ma", "sjon"], 0) == "in"


def test_spoken_syllable_last_untouched():
    assert _spoken_syllable(["team"], 0) == "team"


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
        "ter",
        "ett",
        "etter",
        "etterforskningsteamet",
    ]


def test_breakdown_geminate_spoken_as_ett_ter():
    """The 'etter' morpheme is voiced ett/ter (ambisyllabic geminate), not ett/er."""
    result = build_norwegian_breakdown("etter")
    assert result == ["etter", "ter", "ett", "etter", "etter"]


def test_breakdown_finne_no_lone_consonant():
    """finne is fin|ne -> voiced finn/ne, never the bogus f|inne split."""
    result = build_norwegian_breakdown("finne")
    assert result == ["finne", "ne", "finn", "finne", "finne"]


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
        "sen",
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
