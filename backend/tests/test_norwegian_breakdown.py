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

from app.plugins.languages.no.norwegian_breakdown import (
    _NORWEGIAN_VOWELS,
    _fold_vowel_only_inflections,
    _is_content_stem,
    _load_ranked_lexicon,
    _segment_surface,
    _spoken_syllable,
    build_norwegian_breakdown,
    flat_syllables,
    load_no_lexicon,
    segment_compound,
    slow_norwegian_word,
    syllabify_morpheme,
)


def _has_vowel(chunk: str) -> bool:
    """True if *chunk* is speakable on its own (contains a syllable nucleus)."""
    return bool(set(chunk) & _NORWEGIAN_VOWELS)


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


def test_genitive_ens_collapses_onto_stem_in_buildup():
    """tunatale-95zt: genitive -ens merges back onto the final stem.

    'ens' is a real Norwegian adjective but it is NOT a compound constituent.
    Merging it onto the stem (etterforskerens -> etter|forskerens) is consistent
    with how every other trailing inflection is already handled.
    """
    from app.plugins.languages.no.norwegian_breakdown import (
        _INFLECTIONS,
        _compound_buildup_units,
    )

    assert "ens" in _INFLECTIONS

    # The decisive pair: one letter apart, same treatment
    seg_en = segment_compound("etterforskeren")
    seg_ens = segment_compound("etterforskerens")
    units_en = _compound_buildup_units(seg_en)
    units_ens = _compound_buildup_units(seg_ens)
    assert [u for u, _ in units_en] == ["etter", "forskeren"]
    assert [u for u, _ in units_ens] == ["etter", "forskerens"]

    # kongens: simplex genitive collapses to one unit
    seg_kongens = segment_compound("kongens")
    units_kongens = _compound_buildup_units(seg_kongens)
    assert [u for u, _ in units_kongens] == ["kongens"]

    # statsministerens ALSO re-segments its stem: peeling 'ens' first leaves
    # 'statsminister', which then segments as stats+minister. That is a
    # second-order change ABOVE the syllable and it is INTENDED -- 'stats|
    # minister' is the correct compound split and 'stat|smi|ni|ster|ens' was a
    # syllabification bug. Pinned so nobody "fixes" it back.
    assert [u for u, _ in _compound_buildup_units(segment_compound("statsministerens"))] == [
        "stats",
        "ministerens",
    ]
    # /mɪ.ˈnɪ.stə.ɳs/ — the r belongs to the retroflex that ends the word, so
    # the cut is ste|rens, not ster|ens (tunatale-4rj5).
    assert flat_syllables("statsministerens") == ["stats", "mi", "ni", "ste", "rens"]

    # muligens, overens: lexicalised adverbs collapse to one unit
    for word in ("muligens", "overens"):
        seg = segment_compound(word)
        units = _compound_buildup_units(seg)
        assert [u for u, _ in units] == [word], f"{word}: expected units [{word!r}], got {[u for u, _ in units]}"


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


def test_breakdown_oppklart_no_orphan_consonant():
    """The -t of a compound past participle never becomes its own chunk.

    segment_compound peels ``t`` as a morpheme (``opp|klar|t``), which is right
    morphologically and wrong as a *chunk*: the buildup spoke a bare ``t``,
    whose audio is a CTC-sliced consonant burst out of the whole-word render.
    Day 7's "en sak som aldri ble oppklart" shipped it. The ending rides its
    stem instead: ``opp | klart``.
    """
    bd = build_norwegian_breakdown("oppklart")
    assert "klart" in bd
    for item in bd:
        assert _has_vowel(item), f"vowel-less chunk {item!r} in breakdown {bd}"


def test_breakdown_vowelless_inflection_class_never_orphans():
    """The whole -t/-n class, not just the word that surfaced the bug."""
    for word in ("planlagt", "åpenbart", "innført", "velkommen", "president"):
        for item in build_norwegian_breakdown(word):
            assert _has_vowel(item), f"vowel-less chunk {item!r} in breakdown of {word!r}"


def test_segment_compound_still_peels_the_inflection():
    """The fix lives in the buildup, not the morphology — segmentation is unchanged."""
    assert segment_compound("oppklart") == ["opp", "klar", "t"]


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


def test_syllabify_morpheme_vowelless_inflection_rides_previous_group():
    """A vowel-less inflection (-n, -t) is not a syllable and never stands alone.

    begynnelsen is begynn|else|n morphologically, but ``n`` alone is
    unpronounceable — and its audio is a slice of a whole-word render, so the
    learner gets a consonant fragment. The ending rides the suffix group it
    belongs to instead.
    """
    assert syllabify_morpheme("begynnelsen") == ["be", "gynn", "elsen"]
    assert syllabify_morpheme("ledelsen") == ["led", "elsen"]


def test_syllabify_morpheme_syllabic_inflection_still_its_own_group():
    """The merge is scoped to vowel-less endings: -en keeps its own group."""
    assert syllabify_morpheme("forskningen") == ["forsk", "ning", "en"]


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


def test_segment_compound_forsvunnet_no_false_fuge_s():
    """'for' + fuge-s + 'vunnet' is a false compound: fuge-s attaches to noun
    first-elements (forsknings-, tings-), never to the verbal prefix 'for-'.
    Stays whole so it syllabifies as for·svun·net."""
    assert segment_compound("forsvunnet") == ["forsvunnet"]


def test_syllabify_morpheme_forsvunnet():
    assert syllabify_morpheme("forsvunnet") == ["for", "svun", "net"]


def test_syllabify_morpheme_mistenkt_prefix():
    """The 'mis-' prefix beats onset-maximization (mis·tenkt, not mi·stenkt)."""
    assert syllabify_morpheme("mistenkt") == ["mis", "tenkt"]


def test_syllabify_morpheme_misjon_not_prefix_split():
    """'mis' is a prefix only when the remainder is a content stem; 'jon' is not,
    so misjon stays mi·sjon (sj-digraph onset)."""
    assert syllabify_morpheme("misjon") == ["mi", "sjon"]


def test_is_content_stem_rejects_vowelless_candidate():
    """A content stem has a nucleus — the frequency list contains junk that doesn't.

    ``lsk`` (rank 7702) and ``stk`` (rank 3420) are real entries in
    no_wordlist.txt: OCR/abbreviation noise, not words. Both clear the rank gate
    and the 3-char floor, so without this guard they formed bogus splits —
    ``moralsk`` -> ``mora|lsk``, ``brystkreft`` -> ``bry|stk|ref|t``.
    """
    ranks = _load_ranked_lexicon()
    assert "lsk" in ranks, "fixture assumption: the junk entry is in the wordlist"
    assert "stk" in ranks, "fixture assumption: the junk entry is in the wordlist"
    assert not _is_content_stem("lsk", ranks)
    assert not _is_content_stem("stk", ranks)


def test_segment_compound_rejects_vowelless_parts():
    """The bogus splits collapse; the genuine compound underneath surfaces."""
    assert segment_compound("moralsk") == ["moralsk"]
    # tunatale-9yd0: forelske is a for- verb with no ``%`` — merged by design.
    assert segment_compound("forelske") == ["forelske"]
    assert segment_compound("brystkreft") == ["bryst", "kreft"]


def test_syllabify_morpheme_derivational_peel_needs_a_vowel_left_behind():
    """-ing must not peel off spring: the remainder ``spr`` has no nucleus.

    The 3-char floor in _strip_derivational_suffixes counts characters, which
    ``spr`` passes. A morpheme needs a vowel, not a length.
    """
    assert syllabify_morpheme("spring") == ["spring"]
    assert syllabify_morpheme("springer") == ["sprin", "ger"]
    assert syllabify_morpheme("springe") == ["sprin", "ge"]


def test_segment_compound_forbrytelsens_lexicalized_whole():
    """forbrytelse is a lexeme; the rank guard can't catch its over-split
    (for|bry|tel|s|ens), so it's a human-ratified whole → for·bry·tel·sens."""
    assert segment_compound("forbrytelsens") == ["forbrytelsens"]


def test_syllabify_morpheme_forbrytelsens():
    """Morpheme decomposition for·bryt·else·ns, with the genitive riding its group.

    The original golden here was ``["for", "bryt", "else", "ns"]`` — correct by
    morpheme, wrong as a chunk: ``ns`` has no nucleus, and a chunk is something
    the learner hears in isolation, sliced out of a whole-word render. The
    morpheme boundary is still honoured (else|ns is why the ``s`` is a genitive
    and not part of the stem); it just no longer gets its own audio.
    """
    assert syllabify_morpheme("forbrytelsens") == ["for", "bryt", "elsens"]


# -- _fold_vowel_only_inflections -----------------------------------------


def test_fold_vowel_only_inflection_takes_stems_final_consonant():
    """A vowel-only inflection takes exactly one onset from its stem.

    for·klar·e -> for·kla·re: a bare-nucleus chunk has no consonant to slice
    on, so the stem's final consonant rides onto the inflection. One consonant,
    not the maximal onset — reproducing the raw syllabifier's V-CV split
    (``syllabify_norwegian_word("forklare")`` is already for·kla·re).
    """
    assert _fold_vowel_only_inflections(["klar", "e"]) == ["kla", "re"]
    assert _fold_vowel_only_inflections(["be", "stemt", "e"]) == ["be", "stem", "te"]


def test_fold_vowel_only_inflection_merges_rather_than_stranding_an_all_vowel_stem():
    """When moving would leave an all-vowel stem, merge the pair instead.

    ``air`` + ``e``: taking the ``r`` across gives ``ai`` + ``re`` — which
    manufactures a bare-nucleus chunk (``ai``) one slot earlier, the very thing
    this fold removes. Merging yields ``aire`` and the defect disappears
    outright: mil·li·on·aire, not mil·li·on·ai·re.
    """
    assert _fold_vowel_only_inflections(["air", "e"]) == ["aire"]
    assert _fold_vowel_only_inflections(["eid", "e"]) == ["eide"]
    # The ordinary move is unaffected — ``kla`` is not all-vowel.
    assert _fold_vowel_only_inflections(["klar", "e"]) == ["kla", "re"]


def test_fold_vowel_only_inflection_refuses_to_empty_single_character_previous():
    """A single-character previous piece cannot give up its only consonant."""
    assert _fold_vowel_only_inflections(["r", "e"]) == ["r", "e"]


def test_fold_vowel_only_inflection_leaves_non_inflection_all_vowel_piece():
    """An all-vowel piece that is not an inflection stays at its compound seam.

    Moving a consonant there would cross a morpheme boundary between two
    content stems (arbeids·u·ke).
    """
    assert _fold_vowel_only_inflections(["klar", "u", "ke"]) == ["klar", "u", "ke"]


def test_fold_vowel_only_inflection_requires_consonant_final_previous():
    """Hiatus: a vowel-final previous piece has no consonant to move.

    no·e stays split (or does not — a product decision the brief leaves alone);
    the fold simply must not invent an onset.
    """
    assert _fold_vowel_only_inflections(["no", "e"]) == ["no", "e"]


def test_flat_syllables_vowel_only_inflection_takes_stems_final_consonant():
    """Worked literals: the fold end-to-end through flat_syllables.

    ``adelige`` and ``alvorlige``: the ``-lig`` derivational suffix loses its
    final ``g`` to the inflection. Intended and approved — the morpheme
    boundary stays honoured in analysis, it just stops dictating chunk edges.
    """
    assert flat_syllables("forklare") == ["for", "kla", "re"]
    # allmenne now comes through the seam-guarded whole-word fallback: 'menne'
    # is not a lexicon word, the whole-word split is all|me|nne, and the
    # all|menn seam falls on a cut — so the lexicon boundary wins (tunatale-nlhh).
    assert flat_syllables("allmenne") == ["all", "me", "nne"]
    assert flat_syllables("allerede") == ["a", "lle", "re", "de"]
    assert flat_syllables("akerselva") == ["a", "kers", "el", "va"]
    assert flat_syllables("adelige") == ["a", "del", "ig", "e"]
    assert flat_syllables("aldersbestemte") == ["al", "ders", "be", "stem", "te"]
    assert flat_syllables("anerkjente") == ["a", "ner", "kjen", "te"]
    assert flat_syllables("alvorlige") == ["al", "vo", "rlig", "e"]


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


def test_spoken_syllable_de_ending_keeps_real_spelling():
    """The weak-past '-de' fragment is emitted as written.

    It used to be respelled 'deh', because the nb-NO voice reads an ISOLATED
    'de' as the pronoun /diː/ rather than the schwa. Slicing removed the reason:
    the chunk is now cut out of a whole-word render of 'hadde', so nothing
    synthesises the fragment and the invented spelling only ever reached the
    learner's eye — `cues.py` feeds `Phrase.text` straight to the caption.

    Geminate lengthening (et -> ett) is deliberately NOT covered by this: a
    doubled consonant is ambisyllabic and 'ett' is a real Norwegian spelling,
    so it shows the learner something true.
    """
    assert _spoken_syllable(["had", "de"], 1) == "de"
    assert _spoken_syllable(["bøy", "de"], 1) == "de"


def test_de_fragment_matches_the_word_it_came_from():
    seq = build_norwegian_breakdown("hadde")
    assert "dde" in seq
    assert "deh" not in seq
    assert seq[0] == "hadde"  # full word intact
    assert seq[-1] == "hadde"


def test_de_standalone_word_unaffected():
    seq = build_norwegian_breakdown("de lyver")
    assert "de" in seq
    assert "deh" not in seq


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
        "met",
        "tea",
        "teamet",
        "forsknings",
        "knings",
        "fors",
        "forsknings",
        "forskningsteamet",
        "etter",
        "tter",
        "e",
        "etter",
        "etterforskningsteamet",
    ]


def test_breakdown_geminate_spoken_as_ett_ter():
    """The 'etter' morpheme: lexicon e|tter, no geminate respelling (respell=False)."""
    result = build_norwegian_breakdown("etter")
    assert result == ["etter", "tter", "e", "etter"]


def test_breakdown_finne_no_lone_consonant():
    """finne is fi|nne from the lexicon — no geminate respelling."""
    result = build_norwegian_breakdown("finne")
    assert result == ["finne", "nne", "fi", "finne"]


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
        "kning",
        "fors",
        "forskning",
    ]


def test_breakdown_single_stem_3_syllables():
    assert build_norwegian_breakdown("kjærlighet") == [
        "kjærlighet",
        "het",
        "rlig",
        "rlighet",
        "kjæ",
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
        "ssen",
        "pla",
        "plassen",
        "fly",
        "på",
        "på flyplassen",
    ]


def test_breakdown_multi_word_non_compound():
    assert build_norwegian_breakdown("på plassen") == [
        "på plassen",
        "ssen",
        "pla",
        "plassen",
        "på",
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
    ]


def test_lexicon_pieces_are_not_geminate_respelled_in_a_multi_word_phrase():
    """The geminate respelling must stay OFF wherever lexicon pieces are used.

    'hadde' is cut ha|dde by the lexicon, so the doubled consonant already sits
    whole in the second syllable and _spoken_syllable must not double it again.
    This pins the MULTI-WORD path specifically: it had its own inverted respell
    flag, fixed separately from the single-word one, and only a phrase-level
    test distinguishes them.
    """
    chunks = build_norwegian_breakdown("jeg hadde")
    assert "hadd" not in chunks
    assert "ha" in chunks
    assert "dde" in chunks
