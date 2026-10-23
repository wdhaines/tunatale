#!/usr/bin/env python3
"""Build the Cebuano lemma table from the kaikki.org Wiktionary extract.

Run from ``backend/``::

    uv run python scripts/build_cebuano_lemma_table.py
    uv run python scripts/build_cebuano_lemma_table.py --source <jsonl>

Why: Cebuano has no Stanza/classla model in this repo, but Wiktionary already
publishes conjugation tables (kaikki.org, wiktextract). The measured design is
``.beads-tasks/briefs/brief-u8nz5-cebuano-lemma-table-2026-09-26.md``.
The rows go straight into the torch-free table engine
(``app.srs.lemma_table``): ``surface, upos, lemma, is_default``, one default row
per surface. Verb lemmas are ROOTS (``naglakaw`` → ``lakaw``).

Rules, in order (see the brief for rationale):

1. kaikki ``pos`` → UPOS; every other pos is dropped. Every entry's own
   ``word`` is a row for itself under its UPOS.
1b. The plugin's own 625 list (``ceb/data/base625.tsv``): a mintable one-word
   entry Wiktionary lacks joins the headwords (category ``verb`` → VERB,
   ``adjective`` → ADJ, anything else → NOUN). Every one is a card in the decks.
2. Attested forms: verb entries carry Wiktionary conjugation tables. Walk the
   entry's ``forms`` list IN ORDER: a form tagged ``inflection-template`` (e.g.
   ``ceb-infl-mo``) opens a block; the FIRST later form in that block that is a
   word is the block's ROOT; every later word-form in the block is a surface of
   that root, UPOS VERB. Skip table-tags/canonical forms, affix markers
   (leading/trailing ``-``), anything with a space or ``{``, table labels, and
   forms tagged ``alternative``/``obsolete``.
2b. Form-of headwords: Wiktionary gives 605 verb forms an entry of their own
   whose every sense is "<form> of X" (``andama``: "imperative of andam"; 557
   are imperatives, measured 2026-10-09). Such a headword is an attested form
   of X, exactly like a conjugation-table row: it gets no reading of its own
   and is not a root for rule 3. It is still a STEM: rule 3's affixes are
   applied to it too, and those forms (``mabas-a`` on ``bas-a``, "imperative
   of basa") get X as their lemma. One sense that is NOT a form-of and the
   headword keeps itself (rule 5). A headword merely DERIVED from a root (its
   etymology says ``mo-`` + ``gawas``, but it has senses of its own) is not
   covered: that shape is also ``natawo`` "born" from ``tawo`` "person". The
   plain inflections among those are listed by hand in ``closed_class.tsv``.
3. Generated forms: roots = every headword with a VERB or ADJ reading (NOT
   noun-only). For each root ``r``, emit VERB rows ``form → r`` for:
   - prefixes ``mo mi ni maka naka ma na gi i ka`` + r;
   - ``mag nag pag`` + r, with a hyphen when r starts with a vowel;
   - ``mang nang pang`` with assimilation on r's first letter: ``p``/``b`` →
     drop it and use ``ma``/``na``/``pa`` + ``m``; ``t``/``d``/``s`` → ``n``;
     ``k`` → drop the k; anything else → plain ``mang``+r;
   - suffixes r + ``on``/``an``/``a``/``i``, and ``gi`` + r + ``an``.
   An attested pair (rule 2) always beats a generated one for the same surface.
4. Linker: ``h+"g"`` when a non-verb headword ``h`` ends in ``n``, ``h+"ng"``
   when it ends in a vowel. A candidate that is already a surface gets no row;
   the n+g source beats the vowel+ng one.
5. Defaults (``is_default``, exactly one per surface):
   - A surface that is itself a NON-VERB headword keeps that reading, chosen by
     ``DEFAULT_UPOS_ORDER`` (closed class first). An INTJ reading never wins.
   - Else an attested VERB reading; if several roots, the one with the most
     attested forms, then alphabetical.
   - Else a verb headword is its own lemma.
   - Else a generated reading: prefer a root that has a VERB reading over an
     ADJ-only one, then the shorter root, then alphabetical.
   - Linker rows last.
6b. Wolff roots (tunatale-tofg.3), after the hand list (rule 6): each word of the news corpus that has no
   row, or whose default reads it as its own VERB or ADJ, is decoded by
   ``ceb.affixes.analyse`` against Wolff's dictionary. Its best analysis becomes
   the default, with the word's own reading kept as a non-default, only when:
   the word has 5+ letters; the affix is one of §6.1's inflections (or ma-un),
   not a derivation (ka-an: kalibutan 'world'; paN-: pangulo 'president'; pag-:
   pagtuo 'belief'); the root's entry takes it;
   Wolff's ROOT block reads the root as a verb or an adjective (táwu 'person'
   is a noun there, so natawo 'born' stays itself); and exactly one word of the
   table spells the root, unless the table reads that word as a number (liboan:
   the -an tens stay words of their own) or a function word (napud is na + the
   particle pud), with an e only if the word has one
   (Wolff's i is also the table's e: gibilin is not belen), in 3+ letters; a
   root the table lacks takes the corpus's commonest spelling of the bare word
   (moapil: apil), and with no bare word in the corpus, no row. A word
   + the linker (nakitang) is not decoded. Nouns are never moved: the corpus diff
   showed that is where decoding goes wrong (barangay as bárang + -ay). The words
   these gates still get wrong are hand rows in closed_class.tsv (rule 6).
7. Spelling variants (``spelling_variants.tsv``) read as their main spelling.
8. Contractions (Wolff, Beginning Cebuano 3 F; tunatale-u8nz.36), last, so each
   host reads as its final default (say = sab + y reaches sab's main spelling
   usab). The WRITTEN contractions only: ug/og -> g, nga/ang/ni -> ng, and the
   y particle -> y join the word before (sa/si -> s is "spoken but not
   written", 3 F 2, and as a rule it read bukas 'tomorrow' and the Spanish
   plurals as contractions): after a vowel, w or y (kog, balayng; a vowel's
   -ng is rule 4's); after n the n drops (daghag, may); a postpositive drops
   its final consonant (gyug, say, lay). The surface reads as its host, the
   marker dropped, as rule 4 and the hand rows unsay/kinsay do. Hosts are
   non-verb defaults, as for rule 4, and never a marker themselves. A
   candidate is no contraction when Wolff lists it as a word (subay 'along',
   angay 'should', matag 'each'), when it spells a verb prefix written apart
   (pag, mag, nag), or when the news corpus uses it more than its host (inig
   646 against inin 202: a contraction is one use of its host). A new surface
   takes the contraction as its default, the plain host before one that
   dropped a consonant (kog: ko + og before kon + og). A surface the table
   already has keeps its default and gains the contraction as another reading,
   for the context resolver (may: 'there is', or man + y), when the host is a
   function word, as all of 3 F's homographs are, and the word has no reading
   under that tag yet (a tag picks the first reading listed). What these gates
   still get wrong are hand rows in closed_class.tsv (rule 6 runs first, and a
   listed surface keeps its listed default).
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from app.plugins.languages.ceb.affixes import Lexicon

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# The rules these share with Tagalog's builder are imported, not copied, so a fix
# to the POS map, the word shape or the linker lands in both tables.
from scripts.build_kaikki_lemma_table import (  # noqa: E402
    _WORD,
    DEFAULT_UPOS_ORDER,
    POS_TO_UPOS,
    UPOS_ORDER,
    VOWELS,
    _sha256,
    linker_rows,
)

_BACKEND = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = _BACKEND / "scripts/local/kaikki/Cebuano.jsonl"
OUTPUT = _BACKEND / "app/plugins/languages/ceb/data/cebuano_lemmas.tsv.gz"
BASE_LIST = _BACKEND / "app/plugins/languages/ceb/data/base625.tsv"
CLOSED_CLASS = _BACKEND / "app/plugins/languages/ceb/data/closed_class.tsv"
FREQUENCY = _BACKEND / "app/plugins/languages/ceb/data/cebuano_frequency.tsv.gz"
SPELLING_VARIANTS = _BACKEND / "app/plugins/languages/ceb/data/spelling_variants.tsv"

# base625.tsv's category column → the UPOS a base-list word keeps. Everything
# else in that list is a thing (nouns, colours, places), so NOUN.
_BASE_LIST_UPOS = {"verb": "VERB", "adjective": "ADJ"}

_TABLE_LABELS = frozenset(
    {
        "actor",
        "obj",
        "object",
        "objective",
        "circumstantial",
        "instrumental",
        "instrumentative",
        "benefactive",
    }
)

Readings = dict[str, list[tuple[str, str]]]
Rows = set[tuple[str, str, str, int]]


def _normalize(text: str) -> str:
    """Lowercase and strip accents (NFD, drop combining marks)."""
    text = text.lower()
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text


def _is_word(value: str) -> bool:
    """A Latin-script word: no affix hyphen at either end, no markup."""
    return bool(_WORD.fullmatch(value.lower()))


def _is_word_form(form: dict) -> bool:
    """Filter: a real word, not table metadata."""
    text = form.get("form") or ""
    tags = form.get("tags") or ()
    if not _is_word(text):
        return False
    if "table-tags" in tags or "canonical" in tags:
        return False
    if "alternative" in tags or "obsolete" in tags:
        return False
    if text.startswith("-") or text.endswith("-"):
        return False
    if " " in text or "{" in text:
        return False
    return text.lower() not in _TABLE_LABELS


def extract(entries: Iterable[dict]) -> tuple[dict[str, set[str]], dict[str, set[str]], Counter]:
    """Rule 1: (verb surface → candidate roots, non-verb headword → UPOSes, per-pos counts)."""
    verb_surfaces: dict[str, set[str]] = defaultdict(set)
    nonverb: dict[str, set[str]] = defaultdict(set)
    counts: Counter = Counter()
    for entry in entries:
        pos = entry.get("pos") or "?"
        counts[pos] += 1
        upos = POS_TO_UPOS.get(pos)
        if upos is None:
            continue
        word = _normalize(entry.get("word") or "")
        if not _is_word(word):
            continue
        if pos == "verb":
            verb_surfaces[word].add(word)  # headword is its own candidate
        else:
            nonverb[word].add(upos)
    return dict(verb_surfaces), dict(nonverb), counts


def extract_attested(entries: Iterable[dict]) -> tuple[dict[str, set[str]], dict[str, int]]:
    """Rule 2: attested forms from Wiktionary conjugation tables.

    Returns (surface → candidate roots, root → attested form count).
    """
    verb_surfaces: dict[str, set[str]] = defaultdict(set)
    root_form_count: Counter = Counter()

    for entry in entries:
        pos = entry.get("pos")
        if pos != "verb":
            continue
        word = _normalize(entry.get("word") or "")
        if not _is_word(word):
            continue

        forms = entry.get("forms") or []
        i = 0
        while i < len(forms):
            form = forms[i]
            tags = form.get("tags") or []
            if "inflection-template" in tags:
                # This opens a conjugation block. Find the first word-form as root.
                root = None
                j = i + 1
                while j < len(forms):
                    f = forms[j]
                    if "inflection-template" in f.get("tags", []):
                        break  # New block
                    if _is_word_form(f):
                        root = _normalize(f["form"])
                        break
                    j += 1
                if root:
                    # All later word-forms in this block are surfaces of this root
                    k = j + 1
                    while k < len(forms):
                        f = forms[k]
                        if "inflection-template" in f.get("tags", []):
                            break
                        if _is_word_form(f):
                            surface = _normalize(f["form"])
                            verb_surfaces[surface].add(root)
                            # Rule 5's tie-break is "the root with the most
                            # attested FORMS", so count forms, not blocks.
                            root_form_count[root] += 1
                        k += 1
                i = j
            else:
                i += 1

    return dict(verb_surfaces), dict(root_form_count)


def extract_form_of(entries: Iterable[dict]) -> dict[str, set[str]]:
    """Rule 2b: verb headwords Wiktionary defines only as a form of another word.

    Returns headword → the word(s) it is a form of. A headword qualifies when
    EVERY sense of its verb entries names another single word in ``form_of``;
    one sense of its own (``tipi`` "to kill") and it is a word, not a form.
    """
    senses_by_word: dict[str, list[dict]] = defaultdict(list)
    for entry in entries:
        if entry.get("pos") != "verb":
            continue
        word = _normalize(entry.get("word") or "")
        if not _is_word(word):
            continue
        # An entry with no senses says nothing about being a form: it counts
        # as one sense that names no target.
        senses_by_word[word].extend(entry.get("senses") or [{}])

    form_of: dict[str, set[str]] = {}
    for word, senses in senses_by_word.items():
        named = [{_normalize(f.get("word") or "") for f in sense.get("form_of") or []} for sense in senses]
        targets = [{t for t in sense_targets if _is_word(t) and t != word} for sense_targets in named]
        if all(targets):
            form_of[word] = set().union(*targets)
    return form_of


def _is_vowel_initial(word: str) -> bool:
    return bool(word) and word[0] in VOWELS


def _assimilate_mang(prefix: str, root: str) -> str:
    """Assimilation rules for mang-/nang-/pang-."""
    if not root:
        return prefix + root
    first = root[0]
    if first in "pb":
        # Drop the p/b and use mam/nam/pam + rest of root
        return prefix[:-2] + "m" + root[1:]  # mang -> mam, nang -> nam, pang -> pam
    if first in "tds":
        # Replace ng with n (man/nan/pan + rest of root)
        return prefix[:-1] + root[1:]  # mang -> man, nang -> nan, pang -> pan
    if first == "k":
        # Drop the k and use mang/nang/pang + rest of root
        return prefix + root[1:]
    return prefix + root


def generate_forms(roots: set[str], headword_upos: dict[str, set[str]]) -> dict[str, set[str]]:
    """Rule 3: generate verb forms from roots.

    roots: set of headwords that have VERB or ADJ readings (not noun-only).
    headword_upos: headword → UPOS set (to distinguish VERB from ADJ-only).
    """
    generated: dict[str, set[str]] = defaultdict(set)

    for root in roots:
        if len(root) == 1:
            continue  # Skip one-letter roots

        # Only generate for VERB or ADJ roots, not noun-only
        uposes = headword_upos.get(root, set())
        if not ("VERB" in uposes or "ADJ" in uposes):
            continue

        # Simple prefixes
        for prefix in ["mo", "mi", "ni", "maka", "naka", "ma", "na", "gi", "i", "ka"]:
            generated[prefix + root].add(root)

        # mag/nag/pag with hyphen before vowel
        for prefix in ["mag", "nag", "pag"]:
            if _is_vowel_initial(root):
                generated[prefix + "-" + root].add(root)
            else:
                generated[prefix + root].add(root)

        # mang/nang/pang with assimilation
        for prefix in ["mang", "nang", "pang"]:
            generated[_assimilate_mang(prefix, root)].add(root)

        # Suffixes
        for suffix in ["on", "an", "a", "i"]:
            generated[root + suffix].add(root)

        # gi + root + an
        generated["gi" + root + "an"].add(root)

    return dict(generated)


def assign_defaults(
    verb_surfaces: dict[str, set[str]],
    nonverb: dict[str, set[str]],
    attested_surfaces: dict[str, set[str]],
    root_form_count: dict[str, int],
    headword_upos: dict[str, set[str]],
    linkers: set[tuple[str, str, str]],
) -> Rows:
    """Rule 5: exactly one default row per surface."""
    readings: Readings = {}

    # Non-verb headwords
    for surface, uposes in nonverb.items():
        readings.setdefault(surface, []).extend((upos, surface) for upos in uposes)

    # Attested verb forms (beat generated)
    for surface, roots in attested_surfaces.items():
        readings.setdefault(surface, []).extend(("VERB", root) for root in roots)

    # Generated verb forms (only for surfaces not in attested)
    for surface, roots in verb_surfaces.items():
        if surface not in attested_surfaces:
            readings.setdefault(surface, []).extend(("VERB", root) for root in roots)

    # Linker rows
    for surface, upos, lemma in linkers:
        readings.setdefault(surface, []).append((upos, lemma))

    rows: Rows = set()
    for surface, surface_readings in readings.items():
        default = _pick_default(surface, surface_readings, nonverb, attested_surfaces, root_form_count, headword_upos)
        rows.update((surface, upos, lemma, int((upos, lemma) == default)) for upos, lemma in surface_readings)
    return rows


def _pick_default(
    surface: str,
    readings: list[tuple[str, str]],
    nonverb: dict[str, set[str]],
    attested_surfaces: dict[str, set[str]],
    root_form_count: dict[str, int],
    headword_upos: dict[str, set[str]],
) -> tuple[str, str]:
    if surface in nonverb:
        # Rule 5a: the surface is itself a non-verb headword → that reading,
        # tie-broken by DEFAULT_UPOS_ORDER. An INTJ reading never wins.
        headword = [r for r in readings if r[0] in nonverb[surface] and r[0] != "INTJ"]
        if headword:
            return min(headword, key=lambda r: DEFAULT_UPOS_ORDER.index(r[0]))

    attested = [r for r in readings if r[0] == "VERB" and r[1] in attested_surfaces.get(surface, set())]
    if attested:
        # Rule 5b: attested VERB reading, most forms then alphabetical
        return min(attested, key=lambda r: (-root_form_count.get(r[1], 0), r[1]))

    verb = [r for r in readings if r[0] == "VERB"]
    if verb:
        # Rule 5c: verb headword is its own lemma
        if ("VERB", surface) in verb:
            return ("VERB", surface)

        # Rule 5d: generated reading, prefer VERB root over ADJ-only, then shorter, then alphabetical
        def root_key(root: str) -> tuple:
            has_verb = "VERB" in headword_upos.get(root, set())
            return (not has_verb, len(root), root)  # False < True, so VERB comes first

        return min(verb, key=lambda r: root_key(r[1]))

    # Rule 5e: linker row (single reading by construction)
    return readings[0]


def base_list_headwords(path: Path | None) -> tuple[set[str], dict[str, set[str]]]:
    """Rule 1b: the plugin's own 625 list, as headwords Wiktionary may lack.

    Returns (verb headwords, non-verb headword → UPOS). Only rows the list marks
    mintable (``app.srs.base_list.MINTABLE``) and that are one word. Every one is
    a card in the learners' decks, so it must stay itself: without this rule the
    generator reads ``orasan`` "clock" as ``oras`` "hour" + ``-an``.
    """
    if path is None or not path.exists():
        return set(), {}
    from app.srs.base_list import MINTABLE, load_base_list

    verbs: set[str] = set()
    nonverb: dict[str, set[str]] = defaultdict(set)
    with open(path, encoding="utf-8") as fh:
        for word in load_base_list(fh):
            text = _normalize(word.text)
            if word.status not in MINTABLE or not _is_word(text):
                continue
            upos = _BASE_LIST_UPOS.get(word.category, "NOUN")
            if upos == "VERB":
                verbs.add(text)
            else:
                nonverb[text].add(upos)
    return verbs, dict(nonverb)


def load_closed_class(path: Path) -> list[tuple[str, str, str]]:
    """``(surface, UPOS, lemma)`` rows of the hand-curated closed-class list (tunatale-u8nz.21).

    Raises on an unknown UPOS or a surface listed twice: either is a typo that
    would otherwise ship silently.
    """
    import csv

    with open(path, encoding="utf-8") as fh:
        body = [line for line in fh if line.strip() and not line.startswith("#")]
    out: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for row in csv.DictReader(body, delimiter="\t"):
        surface, upos, lemma = row["surface"], row["upos"], row["lemma"]
        if upos not in UPOS_ORDER:
            raise ValueError(f"{path}: {surface}: unknown UPOS {upos!r}")
        if surface in seen:
            raise ValueError(f"{path}: {surface} is listed twice")
        seen.add(surface)
        out.append((surface, upos, lemma))
    return out


def apply_closed_class(rows: Rows, entries: Iterable[tuple[str, str, str]]) -> Rows:
    """Make each listed ``(upos, lemma)`` THE default of its surface; keep every other reading.

    Wiktionary's own readings of a listed surface survive as non-defaults, so a
    context resolver can still pick them (``apan`` the noun "grasshopper").
    """
    listed = {surface: (upos, lemma) for surface, upos, lemma in entries}
    out: Rows = {row for row in rows if row[0] not in listed}
    for surface, (upos, lemma) in listed.items():
        out.update((s, u, lem, 0) for s, u, lem, _ in rows if s == surface and (u, lem) != (upos, lemma))
        out.add((surface, upos, lemma, 1))
    return out


def _wolff_root_affixes() -> frozenset[str]:
    from app.plugins.languages.ceb.affixes import CHART_AFFIXES

    # Not the derivations: ka-an (kalibutan 'world'), and §6.2's paN- (pangulo 'president')
    # and pag- (pagtuo 'belief'), whose maN-/naN- the generator (rule 3) already covers.
    return (CHART_AFFIXES - {"ka-an", "paN-", "maN-", "naN-", "pag-"}) | {"ma-+-un"}


def apply_wolff_roots(rows: Rows, surfaces: Iterable[str], lex: Lexicon, keep: Iterable[str] = ()) -> Rows:
    """Rule 6b: root each of *surfaces* that has no row, or reads as its own VERB or ADJ,
    by Wolff's dictionary (see the module docstring for the gates). *keep* (the
    hand-listed surfaces) are never touched."""
    from app.plugins.languages.ceb.affixes import analyse, licensed, wolff_letters

    affixes = _wolff_root_affixes()
    default = {r[0]: r for r in rows if r[3] == 1}
    surfaces = list(surfaces)
    known = set(default) | set(surfaces)
    # Wolff letters -> the table's words spelled so, when some reading of the word is a
    # content word (napud is na + pud, only ever a particle) and it is not by default a
    # number (liboan: the -an tens are words of their own). Wiktionary's default alone
    # would lose most of Wolff's verb roots: kita 'we' is also 'see', sulod 'inside' also
    # 'enter', hurot is a DET there and mando only a name.
    content = {r[0] for r in rows if r[2] == r[0] and r[1] in ("NOUN", "VERB", "ADJ", "ADV", "PRON", "PROPN")}
    words: dict[str, set[str]] = defaultdict(set)
    for surface, upos, lemma, _ in default.values():
        if lemma == surface and upos != "NUM" and surface in content:
            words[wolff_letters(surface)].add(surface)
    # A root the table lacks (Wiktionary has no apil 'join') is spelled as the corpus
    # spells the bare word, its commonest spelling first (*surfaces* come commonest first).
    # Only when the table has no word so spelled at all: one the gates above exclude
    # (libo, a number) is not to come back by way of the corpus (libuan).
    table = {wolff_letters(r[0]) for r in default.values() if r[2] == r[0]}
    spelled: dict[str, list[str]] = defaultdict(list)
    for surface in surfaces:
        spelled[wolff_letters(surface)].append(surface)
    out: Rows = set(rows)
    keep = set(keep)
    for surface in surfaces:
        own = default.get(surface)
        if surface in keep or len(surface) < 5 or own and not (own[2] == surface and own[1] in ("VERB", "ADJ")):
            continue
        if surface.endswith("ng") and surface[-3] in "aeiou" and surface[:-2] in known:
            continue  # a word + the linker (nakitang), not a root ending in -ng
        best = next(iter(analyse(surface, lex)), None)
        if best is None or best.affix not in affixes or not licensed(best, lex):
            continue
        # Wolff's i is also the table's e: bílin is belen as well as bilin, and the word's
        # own spelling decides (ibilin, bilin). A two-letter root is too easily a stray
        # syllable (magda as mag- + dá).
        root = wolff_letters(best.root)
        lemmas = {w for w in words.get(root, ()) if ("e" in w) == ("e" in surface)}
        if not lemmas and root not in table:
            lemmas = {w for w in spelled.get(root, ()) if ("e" in w) == ("e" in surface)}
            lemmas = set(sorted(lemmas, key=spelled[root].index)[:1])
        if len(lemmas) != 1 or not lex.root_pos[best.root] & {"v", "a"}:
            continue
        if len(wolff_letters(best.root).replace("-", "")) < 3:
            continue
        lemma = next(iter(lemmas))
        if own:
            out.discard(own)
            out.add((surface, own[1], own[2], 0))
        out.add((surface, own[1] if own else "VERB", lemma, 1))
    return out


def load_spelling_variants(path: Path) -> list[tuple[str, str]]:
    """``(variant, main)`` pairs of the hand-kept spelling-variant list.

    Raises on a variant listed twice, a word mapped to itself, or a chain (a
    main spelling that is itself listed as a variant): each is a typo that would
    otherwise ship silently, and a chain would leave some readings one hop short.
    """
    import csv

    with open(path, encoding="utf-8") as fh:
        body = [line for line in fh if line.strip() and not line.startswith("#")]
    pairs: list[tuple[str, str]] = []
    for row in csv.DictReader(body, delimiter="\t"):
        variant, main = row["variant"], row["main"]
        if variant == main:
            raise ValueError(f"{path}: {variant} is mapped to itself")
        if any(v == variant for v, _ in pairs):
            raise ValueError(f"{path}: {variant} is listed twice")
        pairs.append((variant, main))
    variants = {v for v, _ in pairs}
    for variant, main in pairs:
        if main in variants:
            raise ValueError(f"{path}: {variant} -> {main} is a chain; {main} is itself listed as a variant")
    return pairs


def apply_spelling_variants(rows: Rows, pairs: Iterable[tuple[str, str]]) -> Rows:
    """Send every reading whose lemma is a variant to its main spelling.

    Affixed and linker forms follow (``puwedeng`` -> ``pwede``), because they
    carry the variant as their lemma. A variant Wiktionary lacks gets the main
    spelling's default reading. A main spelling with no reading at all raises:
    it is a typo, and mapping real words onto it would orphan them.
    """
    out: Rows = set(rows)
    for variant, main in pairs:
        main_default = next((r for r in out if r[0] == main and r[3] == 1), None)
        if main_default is None:
            raise ValueError(f"spelling variant {variant}: main spelling {main} has no reading in the table")
        # The variant IS its main word, so its readings as a third word go: the
        # linker rule read nong (short for manong) as no + ng.
        out = {r for r in out if r[0] != variant or r[2] in (variant, main)}
        out = {(s, u, main if lem == variant else lem, d) for s, u, lem, d in out}
        if not any(r[0] == variant for r in out):
            out.add((variant, main_default[1], main, 1))
    return out


def build_from_path(
    source: Path,
    output: Path,
    base_list: Path | None = None,
    closed_class: Path | None = None,
    spelling_variants: Path | None = None,
    frequency: Path | None = None,
    wolff: Lexicon | None = None,
) -> dict:
    """Build the table from *source* JSONL into *output* ``.tsv.gz``, print the report."""
    sha_hex = _sha256(source)
    with open(source, encoding="utf-8") as fh:
        entries = [json.loads(line) for line in fh if line.strip()]

    # Rule 1: headwords
    verb_surfaces, nonverb, counts = extract(entries)
    # Rule 1b: base-list words Wiktionary lacks join the headwords. A word
    # Wiktionary already has keeps Wiktionary's parts of speech.
    base_verbs, base_nonverb = base_list_headwords(base_list)
    known = set(verb_surfaces) | set(nonverb)
    base_added = 0
    for word in base_verbs - known:
        verb_surfaces[word] = {word}
        base_added += 1
    for word, uposes in base_nonverb.items():
        if word not in known:
            nonverb[word] = set(uposes)
            base_added += 1

    # Rule 2: attested forms
    attested_surfaces, root_form_count = extract_attested(entries)
    # Rule 2b: a headword that is only a form of another word is attested as
    # that word's form. From here on it is treated like a conjugation-table row:
    # it loses its own reading and is not a root (below).
    form_of = extract_form_of(entries)
    # A conjugation table that already lists the form lists its paradigm too,
    # so only the other form-of headwords are stems for rule 3 (below).
    stems = set(form_of) - set(attested_surfaces)
    for surface, targets in form_of.items():
        attested_surfaces.setdefault(surface, set()).update(targets)

    # Rule 3's roots are HEADWORDS only (verb, or adjective): computed before the
    # attested forms are merged in, or every attested form would be treated as a
    # root of its own.
    roots = set(verb_surfaces) | {w for w, uposes in nonverb.items() if "ADJ" in uposes}
    # Wiktionary also keeps some affixed forms as verb headwords of their own
    # (``mokaon`` "to eat"). One that a conjugation table already lists as a form
    # of another root is not a root: it would grow ``momokaon``.
    roots -= set(attested_surfaces)
    headword_upos: dict[str, set[str]] = {w: set(uposes) for w, uposes in nonverb.items()}
    for w in verb_surfaces:
        headword_upos.setdefault(w, set()).add("VERB")

    # Attested beats generated: merged here, and assign_defaults drops the
    # generated readings of any attested surface.
    for surface, attested_roots in attested_surfaces.items():
        verb_surfaces.setdefault(surface, set()).update(attested_roots)

    generated = generate_forms(roots, headword_upos)
    # Rule 2b's headwords are STEMS as well as forms: Cebuano builds further
    # forms on an imperative or syncopated stem (``mabas-a`` on ``bas-a``,
    # ``mahatagi`` on ``hatagi``, ``nagtinabangay`` on ``tinabangay``; 22 such
    # stem + affix pairs occur in the news corpus). Those forms belong to the
    # stem's root, never to the stem.
    for surface, surface_stems in generate_forms(stems, dict.fromkeys(stems, {"VERB"})).items():
        for stem in surface_stems:
            generated.setdefault(surface, set()).update(form_of[stem])

    # Merge generated (attested already merged, so attested wins)
    for surface, generated_roots in generated.items():
        verb_surfaces.setdefault(surface, set()).update(generated_roots)

    # Rule 4: linker
    base_surfaces = set(verb_surfaces) | set(nonverb)
    linkers = linker_rows(nonverb, base_surfaces)

    # Rule 5: defaults
    rows = assign_defaults(verb_surfaces, nonverb, attested_surfaces, root_form_count, headword_upos, linkers)
    # Rule 6: the hand-curated closed-class list overrides (tunatale-u8nz.21).
    listed = load_closed_class(closed_class) if closed_class is not None else []
    rows = apply_closed_class(rows, listed)
    # Rule 6b: the corpus's words Wiktionary leaves unrooted, rooted by Wolff's dictionary.
    # After the hand list, so a root it adds is a word to root to (bilin: ibilin), and a
    # word it lists is left alone (natawo).
    wolff_count = 0
    if frequency is not None and wolff is not None:
        before = {r for r in rows if r[3] == 1}
        rows = apply_wolff_roots(rows, corpus_words(frequency), wolff, keep={s for s, _, _ in listed})
        wolff_count = len({r for r in rows if r[3] == 1} - before)
    # Rule 7: hand-kept spelling variants read as their main spelling (the user,
    # 2026-09-28: pwede/puwede, lamesa/mesa). Last, so no earlier rule re-splits them.
    if spelling_variants is not None:
        rows = apply_spelling_variants(rows, load_spelling_variants(spelling_variants))
    # Rule 8: contractions, last, so each host reads as its final default.
    before_contractions = len(rows)
    rows = apply_contractions(
        rows,
        words=wolff_words(wolff) if wolff is not None else frozenset(),
        frequency=corpus_counts(frequency) if frequency is not None else None,
        keep={s for s, _, _ in listed},
    )

    # Emit
    out = io.StringIO()
    out.write(f"#source_version=kaikki-{sha_hex[:12]}\n")
    by_surface: dict[str, list[tuple[str, str, int]]] = defaultdict(list)
    for surface, upos, lemma, is_default in rows:
        by_surface[surface].append((upos, lemma, is_default))
    for surface in sorted(by_surface):
        for upos, lemma, is_default in sorted(by_surface[surface], key=lambda r: (UPOS_ORDER.index(r[0]), r[1])):
            out.write(f"{surface}\t{upos}\t{lemma}\t{is_default}\n")
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        gz.write(out.getvalue().encode("utf-8"))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(buf.getvalue())

    # Stats
    multi_lemma = sum(1 for readings in by_surface.values() if len({r[1] for r in readings}) > 1)
    attested_count = len(attested_surfaces)  # count of surfaces with attested forms
    generated_count = len(generated)  # count of surfaces with generated forms
    headword_count = len(nonverb)
    linker_count = len(linkers)

    stats = {
        "sha256": sha_hex,
        "rows": len(rows),
        "surfaces": len(by_surface),
        "multi_lemma": multi_lemma,
        "size": output.stat().st_size,
        "pos_counts": dict(counts),
        "headword_count": headword_count,
        "attested_count": attested_count,
        "generated_count": generated_count,
        "linker_count": linker_count,
        "base_list_added": base_added,
        "form_of_count": len(form_of),
        "wolff_roots": wolff_count,
        "contraction_rows": len(rows) - before_contractions,
    }

    print(f"source: {source} ({sha_hex[:12]})")
    print(f"per-pos entry counts: {dict(sorted(counts.items()))}")
    print(f"headwords: {headword_count}")
    print(f"attested surface→root pairs: {attested_count}")
    print(f"headwords that are only a form of another word: {len(form_of)}")
    print(f"generated surface→root pairs: {generated_count}")
    print(f"linker rows: {linker_count}")
    print(f"base-list headwords Wiktionary lacks: {base_added}")
    print(f"corpus words rooted by Wolff's dictionary: {wolff_count}")
    print(f"contraction rows: {stats['contraction_rows']}")
    print(f"total rows: {stats['rows']}; distinct surfaces: {stats['surfaces']}; surfaces with >1 lemma: {multi_lemma}")
    print(f"wrote {output} ({stats['size']} bytes)")
    return stats


#: Rule 8's markers, as written contracted: ug/og, nga/ang/ni, and the y particle.
_CONTRACTED = ("g", "ng", "y")
#: The postpositives that drop a final consonant before a contracted marker (3 F 2-3).
_POSTPOSITIVES = frozenset(("gud", "gyud", "jud", "dyud", "gayod", "sad", "sab", "pud", "lang", "tuod"))
#: Hosts whose contraction may also read a word the table already has: 3 F's homographs are
#: all function words (may = man + y, sang = sad + nga, tuong = tuod + nga). A content word
#: there would only add noise (barangay as barangan + y).
_FUNCTION_UPOS = frozenset(("PRON", "PART", "ADV", "SCONJ", "CCONJ", "ADP", "DET"))
#: Words that are markers themselves: no host (sa y is no contraction).
_MARKERS = frozenset(("ang", "nga", "ng", "sa", "si", "ni", "sang", "kang", "ug", "og", "y"))
#: Prefixes written apart (pag-abot split in a lesson, panag- in the news): never a contraction.
_PREFIX_SPELLINGS = frozenset(
    ("mo", "mi", "ni", "maka", "naka", "ma", "na", "gi", "i", "ka", "mag", "nag", "pag", "mang", "nang", "pang")
    + ("manag", "nanag", "panag", "magka", "nagka", "maga", "naga", "paga", "gina")
)


def _contractions(host: str) -> list[tuple[str, int]]:
    """The surfaces *host* takes with a contracted marker, each with its precedence (0 = plain)."""
    if host in _POSTPOSITIVES:
        stem = host[:-2] if host.endswith("ng") else host[:-1]  # ng is one consonant: lang y -> lay
        return [(stem + m, 2) for m in _CONTRACTED]
    if host[-1] in VOWELS:
        return [(host + m, 0) for m in _CONTRACTED if m != "ng"]  # vowel + ng is rule 4's
    if host[-1] in "wy":
        return [(host + m, 0) for m in _CONTRACTED]
    if host[-1] == "n":
        return [(host[:-1] + m, 1) for m in _CONTRACTED if m != "ng"]  # n + g is rule 4's nga
    return []


def apply_contractions(
    rows: Rows,
    *,
    words: frozenset[str] = frozenset(),
    frequency: dict[str, int] | None = None,
    keep: Iterable[str] = (),
) -> Rows:
    """Rule 8: each host's contracted surfaces read as that host (module docstring).

    *words* are Wolff's words in his letters (``affixes.wolff_letters``): none is a contraction.
    *frequency* is the news corpus's count per word: no contraction outnumbers its host
    (or the host's lemma: sab is always usab in the news).
    *keep* are the hand-listed surfaces (rule 6), which get no contraction reading at all.
    """
    kept = frozenset(keep)
    from app.plugins.languages.ceb.affixes import wolff_letters

    counts = frequency or {}
    defaults = {s: (u, lem) for s, u, lem, d in rows if d == 1}
    readings = {(s, u, lem) for s, u, lem, _ in rows}
    found: dict[str, list[tuple[int, str, str, str]]] = defaultdict(list)
    for host, (upos, lemma) in defaults.items():
        if upos == "VERB" or host in _MARKERS or len(host) < 2:
            continue
        for surface, rank in _contractions(host):
            if surface == host or surface in kept or surface in _PREFIX_SPELLINGS:
                continue
            # The gates guard a word the table lacks from being taken over; a word it has
            # only gains a reading. A postpositive's contraction is 3 F's own rule (lay =
            # lang + y, though Wolff also lists a lay).
            if surface in defaults and upos not in _FUNCTION_UPOS:
                continue
            if surface not in defaults and (
                (rank < 2 and wolff_letters(surface) in words)
                or counts.get(surface, 0) > max(counts.get(host, 0), counts.get(lemma, 0))
            ):
                continue
            found[surface].append((rank, host, upos, lemma))
    tags = {(s, u) for s, u, _ in readings}
    out: Rows = set(rows)
    for surface, hosts in found.items():
        hosts.sort()
        has_default = surface in defaults
        for i, (_, _, upos, lemma) in enumerate(hosts):
            # Under a tag the word already reads, an added lemma could be the one listed first.
            if has_default and (surface, upos) in tags:
                continue
            if (surface, upos, lemma) not in readings:
                out.add((surface, upos, lemma, int(i == 0 and not has_default)))
                readings.add((surface, upos, lemma))
    return out


def wolff_words(lex: Lexicon) -> frozenset[str]:
    """Every word Wolff lists, as a headword or written out, in his letters (marks and homograph numbers off)."""
    from app.plugins.languages.ceb.affixes import strip_marks

    return frozenset(strip_marks(r).lstrip("*").split("_")[0] for r in lex.roots) | frozenset(lex.written)


def corpus_counts(path: Path) -> dict[str, int]:
    """The frequency list as word -> count."""
    counts: dict[str, int] = {}
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 2 and parts[1].isdigit():
                counts[parts[0]] = int(parts[1])
    return counts


def corpus_words(path: Path) -> list[str]:
    """The words of the frequency list (``cebuano_frequency.tsv.gz``), commonest first."""
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return [line.split("\t", 1)[0] for line in fh if not line.startswith("#")]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="kaikki Cebuano JSONL extract")
    args = parser.parse_args(argv)
    if not args.source.exists():
        parser.error(f"source extract not found: {args.source}")
    from app.plugins.languages.ceb.affixes import lexicon
    from app.plugins.languages.ceb.wolff_cards import load_lexicon

    wolff = lexicon(load_lexicon().entries)
    build_from_path(args.source, OUTPUT, BASE_LIST, CLOSED_CLASS, SPELLING_VARIANTS, FREQUENCY, wolff)
    return 0


if __name__ == "__main__":
    sys.exit(main())
