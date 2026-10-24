"""Oracle for the Cebuano affix decoder (tunatale-tofg.3).

Source: Wolff, *A Dictionary of Cebuano Visayan* (1972), Introduction §5.1, the
morphophonemics an affixed form undergoes. Every worked example the section
prints is a row below, root + affix = the form Wolff writes, so the table is the
section, not a sample of it. Design: .beads-tasks/briefs/design-ceb-affix-decoder.md.

Forms are compared in Wolff's letters with the accents (vowel length) stripped:
§5 states where the STRESS goes, and the length marks that follow from it are
a separate claim, measured in its own table. Hyphens are kept: a hyphen after a
consonant is a glottal stop (``kan-a``), and a rule that loses one is wrong.
"""

from __future__ import annotations

import pytest

from app.plugins.languages.ceb.affixes import (
    Analysis,
    affix_of_label,
    analyse,
    class_affixes,
    decode,
    generate,
    lexicon,
    licensed,
    strip_marks,
    wolff_letters,
)

# (root, affix, the form Wolff writes, the section)
O1: list[tuple[str, str, str, str]] = [
    # 5.11 shift of stress: (→) to the final syllable, (←) to the penult
    ("káun", "nag-(→)", "nagkaun", "5.11"),
    ("tugnaw", "-un(→)", "tugnawun", "5.11"),
    ("sakay", "nag-(←)", "nagsákay", "5.11"),
    # 5.12 a suffix after a stressed final syllable drops its vowel
    ("dakup", "-an", "dakpan", "5.12"),
    ("pisik", "-an", "piskan", "5.12"),
    ("lakat", "-unun", "laktunun", "5.12"),
    ("kalímut", "-i", "kalimti", "5.12"),
    # 5.13 a vowel-final root adds h or a glottal stop before a suffix
    ("bása", "-un", "basáhun", "5.13"),
    ("adtu", "-un", "adtúun", "5.13"),
    ("kabaláka", "-an", "kabalak-an", "5.13"),
    ("sulti", "-un", "sultíhun", "5.13"),
    ("sulti", "-ánay", "sultiánay", "5.13"),
    # 5.14 metathesis: ʔC and hC become Cʔ and Ch; ʔVh becomes hVʔ; a liquid or
    # s before a consonant swaps with it once the vowel between has dropped
    ("káun", "-a", "kan-a", "5.14"),
    ("luhud", "-an", "ludhan", "5.14"),
    ("túu", "-an", "tuhúan", "5.14"),
    ("ngálan", "-an", "nganlan", "5.14"),
    ("sulud", "-un", "sudlun", "5.14"),
    ("inum", "-a", "imna", "5.14"),
    ("putus", "-un", "pustun", "5.14"),
    ("lusut", "-an", "lutsan", "5.14"),
    # 5.15 d between vowels becomes r or l; l or r abutting a consonant becomes
    # d, and g before a velar; a Spanish final r or l becomes h before a suffix
    ("búkid", "ka-an", "kabukíran", "5.15"),
    ("búkid", "ka-an", "kabukílan", "5.15"),
    ("walà", "-un", "wad-un", "5.15"),
    ("hurut", "-un", "hutdun", "5.15"),
    ("pálung", "-an", "pagngan", "5.15"),
    ("haluk", "-an", "hagkan", "5.15"),
    ("mantinil", "-an", "mantinihan", "5.15"),
    ("imbitar", "-un", "imbitahun", "5.15"),
]

ROOTS = frozenset(root for root, *_ in O1)


def _ids(rows):
    return [f"{root}+{affix}={form}" for root, affix, form, _ in rows]


@pytest.mark.parametrize(("root", "affix", "form", "section"), O1, ids=_ids(O1))
def test_generate_offers_the_form_wolff_writes(root, affix, form, section):
    surfaces = {strip_marks(c.surface) for c in generate(root, affix)}
    assert strip_marks(form) in surfaces, f"§{section}: {sorted(surfaces)}"


def _unshifted(affix: str) -> str:
    return affix.replace("(→)", "").replace("(←)", "")


@pytest.mark.parametrize(("root", "affix", "form", "section"), O1, ids=_ids(O1))
def test_decode_finds_the_root_and_the_affix(root, affix, form, section):
    """A shift lives only in the accents, which decoding reads past: ``nagkaun``
    is káun + nag- whether or not the stress moved."""
    analyses = {(a.root, a.affix) for a in decode(form, ROOTS)}
    assert (root, _unshifted(affix)) in analyses, f"§{section}: {sorted(analyses)}"


# ── controls: what the rules must NOT do ───────────────────────────────────────

#: Plain headwords that look affixed (a prefix or suffix spelled at an edge).
#: Decoding them against a root list that holds both the word and the
#: lookalike root must not root them anywhere but themselves.
LOOKALIKES = [
    ("tan-aw", "taw"),  # the hyphen is the root's own glottal stop
    ("mangga", "ga"),  # mang- would leave 'ga'
    ("gikan", "kan"),  # gi- would leave 'kan'
    ("ibabaw", "babaw"),  # i- would leave 'babaw' (a real word: shallow)
    ("dagat", "dag"),  # -at is not a suffix
]


@pytest.mark.parametrize(("word", "lookalike"), LOOKALIKES)
def test_a_headword_is_not_decoded_into_a_lookalike_root(word, lookalike):
    """A surface that is itself a headword is that headword. Rooting it in a
    shorter headword would send a lesson's ``gikan`` to a card it is not."""
    assert {a.root for a in decode(word, frozenset({word, lookalike}))} == {word}


def test_decode_never_invents_a_root_outside_the_list():
    assert decode("dakpan", frozenset({"pisik"})) == []


def test_generate_refuses_an_affix_it_does_not_know():
    with pytest.raises(ValueError, match="-xyz"):
        generate("dakup", "-xyz")


# ── O2: §6.1's chart and §6.2's paN- row, on Wolff's model verb palit 'buy' (§7.11) ──

O2: list[tuple[str, str]] = [
    # Active: punctual, durative, potential (future / past / subjunctive)
    ("mu-", "mupalit"), ("mi-", "mipalit"), ("ni-", "nipalit"), ("ning-", "ningpalit"), ("ming-", "mingpalit"),
    ("mag-", "magpalit"), ("maga-", "magapalit"), ("nag-", "nagpalit"), ("naga-", "nagapalit"), ("ga-", "gapalit"),
    ("maka-", "makapalit"), ("ka-", "kapalit"), ("naka-", "nakapalit"),
    # Direct passive
    ("-un", "palitun"), ("gi-", "gipalit"), ("-a", "palita"),
    ("paga-un", "pagapalitun"), ("gina-", "ginapalit"), ("paga-a", "pagapalita"),
    ("ma-", "mapalit"), ("na-", "napalit"),
    # Local passive
    ("-an", "palitan"), ("gi-an", "gipalitan"), ("-i", "paliti"),
    ("paga-an", "pagapalitan"), ("gina-an", "ginapalitan"), ("paga-i", "pagapaliti"),
    ("ma-an", "mapalitan"), ("ka-an", "kapalitan"), ("na-an", "napalitan"), ("ma-i", "mapaliti"), ("ka-i", "kapaliti"),
    # Instrumental passive
    ("i-", "ipalit"), ("iga-", "igapalit"), ("ika-", "ikapalit"), ("gika-", "gikapalit"),
    # §6.2: paN- and the active forms it takes, with the nasal in place of p
    ("paN-", "pamalit"), ("naN-", "namalit"), ("maN-", "mamalit"),
]  # fmt: skip


@pytest.mark.parametrize(("affix", "form"), O2, ids=[f"{a}={f}" for a, f in O2])
def test_every_chart_affix_composes_on_palit(affix, form):
    assert form in {strip_marks(c.surface) for c in generate("palit", affix)}


@pytest.mark.parametrize(("affix", "form"), O2, ids=[f"{a}={f}" for a, f in O2])
def test_every_chart_form_of_palit_decodes_to_palit(affix, form):
    assert ("palit", affix) in {(a.root, a.affix) for a in decode(form, frozenset({"palit"}))}


@pytest.mark.parametrize(
    ("root", "form"),
    [("tan-aw", "panan-aw"), ("lakaw", "panglakaw"), ("kaun", "pangaun"), ("bása", "pamása"), ("adtu", "pangadtu")],
)
def test_pan_puts_its_nasal_in_place_of_the_roots_first_consonant(root, form):
    """§6.2's paN- (the dictionary's own entry: p b -> m, t d s -> n, k -> ng, else ng + root)."""
    assert strip_marks(form) in {strip_marks(c.surface) for c in generate(root, "paN-")}


# ── O1b: -in-, the derivational suffixes and stacking, on forms Wolff writes out ──
#
# Each row is a sub-entry word printed in the root's own entry (Wolff writes out the
# forms whose morphophonemics are not predictable, §5.1), picked from the first O3
# measurement's misses where §5 and the affix entries explain the form. A stacked
# affix is spelled piece by piece, joined by '+'; the infix goes after the first
# consonant of the whole word, prefix included (k-in-a-).

O1B: list[tuple[str, str, str]] = [
    ("sulud", "-in-+-an", "sinudlan"),  # 5.12 + 5.14
    ("dala", "-in-+-an", "dinalhan"),  # 5.13 h, then 5.12
    ("burda", "-in-+-an", "binurdáhan"),
    ("ubra", "-in-+-an", "inubráhan"),  # a vowel-initial root takes in-
    ("íkut", "-in-+-an", "iniktan"),
    ("lábu", "-in-+-an", "linabúhan"),
    ("Pasku", "-in-+-an", "pinaskúhan"),
    ("matay", "ka-+-in-+-an", "kinamatyan"),
    ("hunas", "ka-+-in-+-an", "kinahunsan"),
    ("ulahi", "ka-+-in-+-an", "kinaulahian"),
    ("káun", "-anan", "kan-anan"),  # 5.12 + 5.14 ʔn -> nʔ
    ("abut", "-anan", "abtanan"),
    ("táwu", "-ánun", "tawhánun"),
    ("hapun", "-ánun", "hapunánun"),
    ("agaw", "pag-+-an", "pag-agáwan"),
]
O1B_ROOTS = frozenset(root for root, *_ in O1B)


@pytest.mark.parametrize(("root", "affix", "form"), O1B, ids=[f"{r}+{a}={f}" for r, a, f in O1B])
def test_generate_offers_the_written_out_form(root, affix, form):
    assert strip_marks(form) in {strip_marks(c.surface) for c in generate(root, affix)}


@pytest.mark.parametrize(("root", "affix", "form"), O1B, ids=[f"{r}+{a}={f}" for r, a, f in O1B])
def test_decode_finds_the_stacked_affix(root, affix, form):
    assert (root, affix) in {(a.root, a.affix) for a in decode(form, O1B_ROOTS)}


def test_a_listed_circumfix_keeps_its_own_label():
    """gi-an is one affix in §6.1's chart, so decoding names it so, not gi-+-an."""
    assert {a.affix for a in decode("gipalitan", frozenset({"palit"}))} == {"gi-an"}


# ── stacking: what §6.1's chart forbids, and the stacks Wolff writes ─────────────


@pytest.mark.parametrize(
    ("form", "roots", "expected"),
    [
        # mi- and na- are cells of the chart; a cell never takes a second inflection (-a)
        ("mihulma", frozenset({"hulam", "hulma"}), {("hulma", "mi-")}),
        ("nahulma", frozenset({"hulam", "hulma"}), {("hulma", "na-")}),
    ],
)
def test_an_inflectional_prefix_takes_no_inflectional_suffix(form, roots, expected):
    """The chart's cells are single affixes; a prefix and a suffix together are
    one only where the chart, or Wolff's own label (gi-un 'the extent of'), lists them:
    hulam + mi- + -a would spell mihulma too."""
    assert {(a.root, a.affix) for a in decode(form, roots)} == expected


#: Stacks Wolff writes out in the root's entry (root, affix, the form), which the
#: stacking rule must keep.
WRITTEN_STACKS: list[tuple[str, str, str]] = [
    ("kusug", "gi-un", "gikusgun"),  # kusug: 'gi-un, kakusgun'
    ("dakù", "gi-un", "gidak-un"),
    ("lakat", "-in-+-an", "nilaktan"),  # lakat: 'linaktan, nilaktan'; -in- is ni- before l
    ("lakat", "-in-+-an", "linaktan"),
    ("uswa", "ma-+-un", "mauswáhun"),  # ma-un, Wolff's adjective
    ("lúbu", "pa-+-a", "palubuha"),  # causative pa- takes the inflections
]


@pytest.mark.parametrize(("root", "affix", "form"), WRITTEN_STACKS, ids=[f"{r}+{a}={f}" for r, a, f in WRITTEN_STACKS])
def test_a_stack_wolff_writes_generates_and_decodes(root, affix, form):
    assert strip_marks(form) in {strip_marks(c.surface) for c in generate(root, affix)}
    assert (root, affix) in {(a.root, a.affix) for a in decode(form, frozenset({root}))}


# ── what a root's entry licenses: its class formula (§7) and its affix labels ──


def test_a_class_formula_licenses_every_chart_cell_of_its_voices():
    """'A; a' (palit, §7.11): active mo-/mag-/maka-, and the direct, local and
    instrumental passives, each with its durative and potential cells (§6.1)."""
    assert class_affixes("A; a") == frozenset(
        {"mu-", "mi-", "ni-", "ning-", "ming-", "mag-", "maga-", "nag-", "naga-", "ga-", "pag-",
         "maka-", "ka-", "naka-",
         "-un", "gi-", "-a", "paga-un", "gina-", "paga-a", "ma-", "na-",
         "-an", "gi-an", "-i", "paga-an", "gina-an", "paga-i", "ma-an", "na-an", "ka-an", "ma-i", "ka-i",
         "i-", "iga-", "ika-", "gika-"}
    )  # fmt: skip


def test_a_code_that_is_not_a_class_formula_licenses_nothing():
    assert class_affixes("word referring to an action") == frozenset()


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("paN-", "paN-"),
        ("-an", "-an"),
        ("ka-an", "ka-an"),  # a chart circumfix keeps its own name
        ("gi-un", "gi-un"),
        ("ma-un", "ma-+-un"),  # Wolff joins a stack with one hyphen
        ("-in-an", "-in-+-an"),
        ("ma-in-un", "ma-+-in-+-un"),
        ("paN-an", "paN-+-an"),
        ("-anay", "-ánay"),
        ("hiN-(←)", "hiN-"),
        ("maN-r-", None),  # reduplication: out of scope
        ("-l-un", None),
    ],
)
def test_wolffs_affix_label_reads_as_the_decoders(label, expected):
    assert affix_of_label(label) == expected


# ── analyse: decode against the real lexicon, then rank ─────────────────────────


@pytest.fixture(scope="module")
def lex():
    from app.plugins.languages.ceb.wolff_cards import load_lexicon

    return lexicon(load_lexicon().entries)


@pytest.mark.parametrize(("form", "root"), [("sinudlan", "sulud"), ("nilaktan", "lakat"), ("gidak-un", "dakù")])
def test_a_written_out_form_is_its_entrys_root_by_lookup(lex, form, root):
    """§5.1: Wolff writes out the forms the rules do not predict, so the entry that
    prints the word is its root, with no decoding."""
    assert analyse(form, lex) == [Analysis(root, "", ("5.1",))]


def test_a_headword_is_itself(lex):
    assert analyse("gikan", lex)[0] == Analysis("gíkan", "", ())  # Wolff marks its length


#: Inflected forms from Wolff's example sentences (under the root's own verb sense)
#: whose root the class formula decides: the other candidates' entries do not take the affix.
LICENSED = [
    ("girid", "ríd"),
    ("pintaha", "pinta"),
    ("pluhun", "plú"),
    ("namangku", "mangku"),
    ("milusay", "lusay"),
    ("musulay", "sulay"),
    ("hinglui", "hinglù"),
]


@pytest.mark.parametrize(("form", "root"), LICENSED)
def test_the_root_whose_entry_takes_the_affix_ranks_first(lex, form, root):
    assert analyse(form, lex)[0].root == root


#: Inflected forms from Wolff's example sentences, with the example's English, whose
#: root only the meaning decides (most differ from a rival by accents alone).
BY_MEANING = [
    ("mipilit", "He insisted on getting inside the very dark room.", "pílit"),
    ("tahiun", "This wound requires stitches.", "tahì"),
    ("kumahi", "Put a comma, not a period.", "kúma"),
    ("kindihun", "Durian can be made into a very delicious candy.", "kindi"),
    ("nakatuma", "You’re drunk. You must have come from a drinking spree.", "túmà"),
    ("gibalaan", "You have been warned and prohibited.", "bálà"),
    ("tumaha", "Take those pills.", "tumar"),
]


@pytest.mark.parametrize(("form", "english", "root"), BY_MEANING, ids=[f for f, *_ in BY_MEANING])
def test_the_english_picks_the_entry_whose_gloss_it_shares(lex, form, english, root):
    assert analyse(form, lex, english=english)[0].root == root


@pytest.mark.parametrize(("form", "english", "root"), BY_MEANING, ids=[f for f, *_ in BY_MEANING])
def test_without_the_english_a_rival_entry_ranks_first(lex, form, english, root):
    """The control: each row above needs its English."""
    ranked = analyse(form, lex)
    assert ranked[0].root != root
    assert root in {a.root for a in ranked}


def test_an_equals_entry_takes_and_means_what_its_target_does(lex):
    """Wolff's lakaw entry is only '= lakat': no class formula, no gloss of its own.
    Without its target's, milakaw would rank as an unlicensed analysis."""
    assert lex.takes["lakaw"] >= lex.takes["lakat"] > frozenset()
    assert "walk" in lex.gloss["lakaw"]
    assert licensed(analyse("milakaw", lex)[0], lex)


def test_analyse_reads_todays_spelling():
    """Lessons spell o and e where Wolff writes u and i (§3.2: three vowels)."""
    lex = lexicon([_entry("languy", ("v", "A; a", "swim"))])
    assert analyse("molangoy", lex) == [Analysis("languy", "mu-", ())]
    assert wolff_letters("Molángoy") == "mulanguy"


def test_the_root_block_says_what_the_root_itself_is():
    """táwu is a noun at its root (its verbs are sub-entries); bilar is a verb there too."""
    lex = lexicon([_entry("bilar", ("n", None, "vigil"), ("v", "A; b", "keep a wake"))])
    assert lex.root_pos["bilar"] == frozenset({"n", "v"})


def _entry(head: str, *senses: tuple[str, str | None, str]) -> dict:
    """A lexicon entry in ``wolff_lexicon.json``'s shape: the root and its (pos, class, gloss) senses."""
    rows = [{"pos": p, "n": None, "cls": c, "gloss": g, "phrase": None, "ex": None} for p, c, g in senses]
    form = {"label": head, "wolff": head, "hb": head, "short": [], "xref": None, "senses": rows}
    return {"id": head, "heads": [head], "forms": [form]}


# ── which surface letters spell the root (tunatale-tofg.3, consumer: the EPUB respeller) ──
#
# The book respells Wolff's three vowels by Raisanen's rules: u is o in the ROOT's last
# syllable and in the affixes mo- and -on. A form that went through §5 (a dropped vowel,
# a moved consonant, an added h) no longer shows where its root is, so
# affixes.letter_map(surface, root, affix) says it letter by letter. One entry per letter
# of strip_marks(surface), hyphens included:
#   int  the index of the root letter (in strip_marks(root)) this letter spells. A letter a
#        rule changed (l -> d, d -> r, a final r -> h) is the letter it replaced; a letter
#        a rule moved (5.14) keeps its index; a dropped root letter appears nowhere.
#   "p" / "i" / "s"  a prefix / infix / suffix letter. paN-'s nasal is "p" (the root
#        consonant it stands in for appears nowhere).
#   "+"  a letter that spells neither: 5.13's h, and a glottal-stop hyphen the root
#        spells with no letter of its own (káun's, walà's) or that comes before a
#        vowel-initial root (pag-agáwan).
# None when the rules do not turn (root, affix) into surface.

LETTER_MAPS: list[tuple[str, str, str, tuple[int | str, ...]]] = [
    # O1, §5's own worked examples
    ("káun", "nag-(→)", "nagkaun", ("p", "p", "p", 0, 1, 2, 3)),
    ("tugnaw", "-un(→)", "tugnawun", (0, 1, 2, 3, 4, 5, "s", "s")),
    ("sakay", "nag-(←)", "nagsákay", ("p", "p", "p", 0, 1, 2, 3, 4)),
    ("dakup", "-an", "dakpan", (0, 1, 2, 4, "s", "s")),  # 5.12: the root's last vowel drops
    ("pisik", "-an", "piskan", (0, 1, 2, 4, "s", "s")),
    ("lakat", "-unun", "laktunun", (0, 1, 2, 4, "s", "s", "s", "s")),
    ("kalímut", "-i", "kalimti", (0, 1, 2, 3, 4, 6, "s")),
    ("bása", "-un", "basáhun", (0, 1, 2, 3, "+", "s", "s")),  # 5.13 h
    ("adtu", "-un", "adtúun", (0, 1, 2, 3, "s", "s")),
    ("kabaláka", "-an", "kabalak-an", (0, 1, 2, 3, 4, 5, 6, "+", "s", "s")),
    ("sulti", "-un", "sultíhun", (0, 1, 2, 3, 4, "+", "s", "s")),
    ("sulti", "-ánay", "sultiánay", (0, 1, 2, 3, 4, "s", "s", "s", "s")),
    ("káun", "-a", "kan-a", (0, 1, 3, "+", "s")),  # 5.14: the glottal stop between a and u moves
    ("luhud", "-an", "ludhan", (0, 1, 4, 2, "s", "s")),
    ("túu", "-an", "tuhúan", (0, 1, "+", 2, "s", "s")),
    ("ngálan", "-an", "nganlan", (0, 1, 2, 5, 3, "s", "s")),
    ("sulud", "-un", "sudlun", (0, 1, 4, 2, "s", "s")),
    ("inum", "-a", "imna", (0, 3, 1, "s")),
    ("putus", "-un", "pustun", (0, 1, 4, 2, "s", "s")),
    ("lusut", "-an", "lutsan", (0, 1, 4, 2, "s", "s")),
    ("búkid", "ka-an", "kabukíran", ("p", "p", 0, 1, 2, 3, 4, "s", "s")),  # 5.15 d -> r
    ("walà", "-un", "wad-un", (0, 1, 2, "+", "s", "s")),  # l -> d
    ("hurut", "-un", "hutdun", (0, 1, 4, 2, "s", "s")),
    ("pálung", "-an", "pagngan", (0, 1, 2, 4, 5, "s", "s")),  # l -> g before a velar
    ("haluk", "-an", "hagkan", (0, 1, 2, 4, "s", "s")),
    ("mantinil", "-an", "mantinihan", (0, 1, 2, 3, 4, 5, 6, 7, "s", "s")),  # a final l -> h
    ("imbitar", "-un", "imbitahun", (0, 1, 2, 3, 4, 5, 6, "s", "s")),
    # O1B, forms Wolff writes out
    ("sulud", "-in-+-an", "sinudlan", (0, "i", "i", 1, 4, 2, "s", "s")),
    ("dala", "-in-+-an", "dinalhan", (0, "i", "i", 1, 2, "+", "s", "s")),
    ("ubra", "-in-+-an", "inubráhan", ("i", "i", 0, 1, 2, 3, "+", "s", "s")),
    ("íkut", "-in-+-an", "iniktan", ("i", "i", 0, 1, 3, "s", "s")),
    ("matay", "ka-+-in-+-an", "kinamatyan", ("p", "i", "i", "p", 0, 1, 2, 4, "s", "s")),
    ("káun", "-anan", "kan-anan", (0, 1, 3, "+", "s", "s", "s", "s")),
    ("táwu", "-ánun", "tawhánun", (0, 1, 2, "+", "s", "s", "s", "s")),
    ("hapun", "-ánun", "hapunánun", (0, 1, 2, 3, 4, "s", "s", "s", "s")),
    ("agaw", "pag-+-an", "pag-agáwan", ("p", "p", "p", "+", 0, 1, 2, 3, "s", "s")),
    # the book's Lessons 1-4 (the course's q written as the dictionary writes it)
    ("tuun", "-in-+-an", "tinun-an", (0, "i", "i", 1, 3, "+", "s", "s")),  # the u left is the root's FIRST
    ("dala", "-un", "dad-un", (0, 1, 2, "+", "s", "s")),  # dádqun
    ("uwan", "mu-", "muuwan", ("p", "p", 0, 1, 2, 3)),  # muquwán: mu- is the book's mo-
    ("bása", "paN-", "pamása", ("p", "p", "p", 1, 2, 3)),
]


@pytest.mark.parametrize(
    ("root", "affix", "form", "expected"), LETTER_MAPS, ids=[f"{r}+{a}={f}" for r, a, f, _ in LETTER_MAPS]
)
def test_letter_map_says_which_letters_spell_the_root(root, affix, form, expected):
    from app.plugins.languages.ceb import affixes

    assert affixes.letter_map(form, root, affix) == expected


def test_letter_map_ignores_marks_and_refuses_a_form_the_rules_do_not_make():
    from app.plugins.languages.ceb import affixes

    assert affixes.letter_map("basahun", "bása", "-un") == affixes.letter_map("basáhun", "bása", "-un")
    assert affixes.letter_map("dakpan", "sulud", "-an") is None
    assert affixes.letter_map("palitan", "palit", "-un") is None


@pytest.mark.parametrize(("affix", "form"), O2, ids=[f"{a}={f}" for a, f in O2])
def test_every_chart_form_of_palit_has_a_letter_map(affix, form):
    """Shape, on every chart affix: one entry per letter, each root letter at most once,
    prefix letters before the root and suffix letters after it."""
    from app.plugins.languages.ceb import affixes

    got = affixes.letter_map(form, "palit", affix)
    assert got is not None and len(got) == len(strip_marks(form))
    ints = [x for x in got if isinstance(x, int)]
    assert len(ints) == len(set(ints)) and all(0 <= x < len("palit") for x in ints)
    first, last = got.index(ints[0]), got.index(ints[-1])
    assert "p" not in got[first:] and "s" not in got[: last + 1]
