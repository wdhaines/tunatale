"""Cebuano affixation by the rules of Wolff's Dictionary, Introduction §5-6 (tunatale-tofg.3).

Two directions over one set of rules:

* :func:`generate` — root + affix to the forms §5 allows, each tagged with the
  rules it applied;
* :func:`decode` — a surface to every (root, affix) the rules explain, among the
  roots a caller passes (Wolff's headwords, in practice).

§5 calls most of its alternations tendencies ("the tendency is to drop the
vowel", "almost always", "tend to be metathesized"), so generation returns
CANDIDATES, not one answer: a caller that needs one form ranks them by what is
attested. Decoding is the same rules run as a search, kept honest by the root
list. Design: ``.beads-tasks/briefs/design-ceb-affix-decoder.md``.

Spelling is Wolff's (§3.2): three vowels; an acute marks a long vowel; a grave
marks a glottal stop after its vowel (``walà``); a hyphen after a consonant is a
glottal stop (``tan-aw``); two vowels side by side have one between them, and a
vowel-initial word starts with one. Internally a word is a list of sounds with
the glottal stop written out (``ʔ``) and ``ng`` as one sound (``ŋ``).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from functools import cache
from typing import Any, NamedTuple

_VOWELS = frozenset("aiueo")
_ACUTE, _GRAVE, _CIRCUMFLEX, _MACRON, _WEDGE = "́", "̀", "̂", "̄", "̌"
_VELARS = frozenset("kgŋ")
_LIQUIDS = frozenset("lr")


class Candidate(NamedTuple):
    surface: str  # in Wolff's spelling
    rules: tuple[str, ...]  # the §5 sections applied, in order


class Analysis(NamedTuple):
    root: str  # as the caller's root list spells it
    affix: str  # as AFFIXES spells it, shift marks left off
    rules: tuple[str, ...]


class _Sound(NamedTuple):
    s: str  # a vowel letter, a consonant letter, "ŋ" or "ʔ"
    long: bool = False


# ── the affixes: §6.1's chart, §6.2's derivational prefixes, and the suffixes §5 uses ──────────

_PREFIXES = (
    # §6.1 active: punctual, durative, potential
    "mu", "mi", "ni", "ning", "ming", "mag", "maga", "nag", "naga", "ga", "maka", "ka", "naka",
    # passives: direct/instrumental past, durative, potential
    "gi", "gina", "ma", "na", "i", "iga", "ika", "gika",
    # §6.2 bases and the active forms they take (paN- -> naN-/maN-, ...)
    "pa", "paN", "maN", "naN", "hiN", "hi", "ha", "pakig", "makig", "nakig", "paki",
    "paniN", "maniN", "naniN", "pani", "mani", "nani", "panggi", "manggi", "nanggi",
    "magka", "nagka", "makapa", "nakapa", "pag", "paga",
)  # fmt: skip
_SUFFIXES = ("un", "a", "an", "i", "ay", "ánay", "unun", "anan", "ánun")
#: §7.0's productive -in- (nominal and past-participle forms); after the word's first consonant
_INFIXES = ("in",)
_CIRCUMFIXES = (
    ("paga", "un"), ("paga", "a"), ("gi", "an"), ("gina", "an"), ("paga", "an"), ("paga", "i"),
    ("ma", "an"), ("ka", "an"), ("na", "an"), ("ma", "i"), ("ka", "i"), ("hi", "an"), ("hi", "i"),
    ("gika", "an"), ("gika", "i"),
    ("gi", "un"),  # not the chart's: Wolff's own label for 'the extent of' (gidak-un, gitas-un)
)  # fmt: skip
#: §6.1's inflecting prefixes and suffixes. Each prefix is a whole cell of the chart, so it
#: takes an inflecting suffix only as a circumfix listed above (mi- + -a is no form).
_INFLECTING = frozenset(
    ("mu", "mi", "ni", "ning", "ming", "mag", "maga", "nag", "naga", "ga", "maka", "naka",
     "gi", "gina", "na", "i", "iga", "ika", "gika", "maN", "naN", "makig", "nakig", "magka", "nagka")
)  # fmt: skip
_INFLECTIONS = frozenset(("un", "a", "an", "i"))

#: Every single affix this module knows, spelled as Wolff labels it (``nag-``, ``-an``,
#: ``-in-``, and the chart's circumfixes ``ka-an``). A stacked affix joins pieces with
#: '+' (``ka-+-in-+-an``): at most one prefix, one infix and one suffix.
AFFIXES: tuple[str, ...] = (
    *(f"{p}-" for p in _PREFIXES),
    *(f"-{i}-" for i in _INFIXES),
    *(f"-{s}" for s in _SUFFIXES),
    *(f"{p}-{s}" for p, s in _CIRCUMFIXES),
)


def _label(pre: str, inf: str, suf: str) -> str:
    """The name of (prefix, infix, suffix): the chart's own for a listed circumfix."""
    if not inf and pre and suf and (pre, suf) in _CIRCUMFIXES:
        return f"{pre}-{suf}"
    return "+".join(x for x in (pre and f"{pre}-", inf and f"-{inf}-", suf and f"-{suf}") if x)


def _split_affix(affix: str) -> tuple[str, str, str, str]:
    """``'nag-(→)'`` -> (prefix, infix, suffix, shift); ``'ka-+-in-+-an'`` stacks."""
    shift = "→" if "(→)" in affix else "←" if "(←)" in affix else ""
    bare = affix.replace("(→)", "").replace("(←)", "")
    pre = inf = suf = ""
    for piece in bare.split("+"):
        if piece not in AFFIXES:
            raise ValueError(f"not an affix this module knows: {affix!r}")
        if piece.startswith("-") and piece.endswith("-"):
            inf = piece.strip("-")
        elif piece.startswith("-"):
            suf = piece[1:]
        elif piece.endswith("-"):
            pre = piece[:-1]
        else:
            pre, _, suf = piece.partition("-")
    return pre, inf, suf, shift


def _grammatical(pre: str, suf: str) -> bool:
    """Whether a prefix and a suffix can stack. ma- and ka- also make adjectives and
    nouns with -un (ma-un, ka-un), but never take the subjunctive -a."""
    if not (pre and suf) or (pre, suf) in _CIRCUMFIXES or suf not in _INFLECTIONS:
        return True
    return pre not in _INFLECTING and not (pre in ("ma", "ka") and suf == "a")


_SUFFIX_BY_LETTERS = {"".join(c for c in unicodedata.normalize("NFD", s) if c.isascii()): s for s in _SUFFIXES}


def affix_of_label(label: str) -> str | None:
    """A sub-entry label as Wolff prints it (``ma-un``, ``-in-an``, ``hiN-(←)``) in this
    module's spelling (``ma-+-un``), or None for one it does not decode (``maN-r-``)."""
    bare = re.sub(r"\((?:→|←)\)", "", label)
    pre, _, rest = bare.partition("-")
    inf, _, suf = rest.partition("-") if "-" in rest else ("", "", rest)
    suf = _SUFFIX_BY_LETTERS.get(suf, suf)
    if (pre or inf or suf) and pre in ("", *_PREFIXES) and inf in ("", *_INFIXES) and suf in ("", *_SUFFIXES):
        return _label(pre, inf, suf)
    return None


# ── spelling <-> sounds ───────────────────────────────────────────────────────────────────────


def strip_marks(word: str) -> str:
    """*word* lower-cased with every accent removed; hyphens kept."""
    return "".join(c for c in unicodedata.normalize("NFD", word) if not unicodedata.combining(c)).lower()


def wolff_letters(word: str) -> str:
    """*word* in Wolff's letters: marks stripped, and today's o and e read as the u and i
    of his three vowels (§3.2), so a lesson's ``molangoy`` meets his ``languy``."""
    return strip_marks(word).replace("o", "u").replace("e", "i")


def _sounds(word: str) -> list[_Sound]:
    out: list[_Sound] = []
    chars = unicodedata.normalize("NFD", word.lower())
    i = 0
    while i < len(chars):
        c = chars[i]
        if c in _VOWELS:
            if not out or out[-1].s in _VOWELS:
                out.append(_Sound("ʔ"))  # word-initial, or between two vowels
            marks = ""
            while i + 1 < len(chars) and unicodedata.combining(chars[i + 1]):
                i += 1
                marks += chars[i]
            out.append(_Sound(c, long=_ACUTE in marks or _MACRON in marks or _CIRCUMFLEX in marks))
            if _GRAVE in marks or _CIRCUMFLEX in marks:
                out.append(_Sound("ʔ"))
        elif c == "-":
            if out and out[-1].s not in _VOWELS and out[-1].s != "ʔ":
                out.append(_Sound("ʔ"))
        elif c == "n" and chars[i + 1 : i + 2] == "g":
            out.append(_Sound("ŋ"))
            i += 1
        elif not unicodedata.combining(c):
            out.append(_Sound(c))
        i += 1
    return out


def _spell(sounds: list[_Sound]) -> str:
    out = ""
    for k, snd in enumerate(sounds):
        prev = sounds[k - 1].s if k else ""
        nxt = sounds[k + 1].s if k + 1 < len(sounds) else ""
        if snd.s == "ʔ":
            if k == 0 or nxt in _VOWELS and prev in _VOWELS:
                continue  # unwritten word-initially and between vowels
            if prev in _VOWELS:
                out = unicodedata.normalize("NFD", out)
                out = out[:-1] if out.endswith(_ACUTE) else out  # one mark per vowel: the glottal's
                out += _GRAVE
            else:
                out += "-"
        elif snd.s == "ŋ":
            out += "ng"
        else:
            out += snd.s + (_ACUTE if snd.long else "")
    return unicodedata.normalize("NFC", out)


# ── §5.1: the stem a suffix attaches to ───────────────────────────────────────────────────────


def _is_c(snd: _Sound) -> bool:
    return snd.s not in _VOWELS


def _stems(root: list[_Sound]) -> set[tuple[tuple[_Sound, ...], tuple[str, ...]]]:
    """Every stem §5.12-5.15 lets *root* take before a suffix, with the rules applied."""
    base = tuple(root)
    stems: set[tuple[tuple[_Sound, ...], tuple[str, ...]]] = set()
    if not _is_c(base[-1]):
        # 5.13: a vowel-final root adds h or a glottal stop
        stems |= {(base + (_Sound("h"),), ("5.13",)), (base + (_Sound("ʔ"),), ("5.13",))}
    else:
        stems.add((base, ()))
        if base[-1].s in _LIQUIDS:
            stems.add((base[:-1] + (_Sound("h"),), ("5.15",)))  # imbitar -> imbitah-
    for stem, rules in list(stems):
        # 5.14: ʔVh becomes hVʔ (túu + h -> tuhuʔ)
        if len(stem) >= 3 and stem[-3].s == "ʔ" and not _is_c(stem[-2]) and stem[-1].s == "h":
            stems.add((stem[:-3] + (_Sound("h"), stem[-2], _Sound("ʔ")), (*rules, "5.14")))
        # 5.12: the vowel of the final syllable drops (dakup -> dakp-)
        if len(stem) >= 3 and _is_c(stem[-3]) and not _is_c(stem[-2]) and _is_c(stem[-1]):
            dropped = stem[:-2] + stem[-1:]
            stems.add((dropped, (*rules, "5.12")))
            c1, c2 = dropped[-2], dropped[-1]
            # 5.14: ʔC/hC become Cʔ/Ch; a liquid or s swaps with the consonant it abuts,
            # either side (putus -> puts- -> pust-), and so does n before m (inum -> imn-)
            if c1.s in "ʔhlrsn" or c2.s in "lrs":
                stems.add((dropped[:-2] + (c2, c1), (*rules, "5.12", "5.14")))
    changed: set[tuple[tuple[_Sound, ...], tuple[str, ...]]] = set()
    for stem, rules in stems:
        # 5.15: l or r abutting a consonant becomes d, and g next to a velar
        for k in range(max(0, len(stem) - 3), len(stem)):
            if stem[k].s not in _LIQUIDS | {"d"}:
                continue
            near = {stem[j].s for j in (k - 1, k + 1) if 0 <= j < len(stem) and _is_c(stem[j])}
            if near & _VELARS:
                changed.add((stem[:k] + (_Sound("g"),) + stem[k + 1 :], (*rules, "5.15")))
            elif near and stem[k].s in _LIQUIDS:
                changed.add((stem[:k] + (_Sound("d"),) + stem[k + 1 :], (*rules, "5.15")))
    return stems | changed


def _suffixed(root: list[_Sound], suffix: str) -> set[tuple[tuple[_Sound, ...], tuple[str, ...]]]:
    tail = tuple(_sounds(suffix))[1:]  # a suffix's own leading glottal is the stem's business
    out = set()
    for stem, rules in _stems(root):
        word = stem + tail
        out.add((word, rules))
        # 5.15: d between vowels becomes r or l (búkid + ka-an -> kabukíran / kabukílan)
        k = len(stem) - 1
        if word[k].s == "d" and not _is_c(word[k - 1]) and not _is_c(word[k + 1]):
            for liquid in "rl":
                out.add((word[:k] + (_Sound(liquid),) + word[k + 1 :], (*rules, "5.15")))
    return out


# ── prefixes ──────────────────────────────────────────────────────────────────────────────────


def _prefixed(prefix: str, word: tuple[_Sound, ...]) -> set[tuple[_Sound, ...]]:
    """*prefix* on *word*. ``N`` is the nasal that takes the place of p b (m), t d s (n) and
    k (ng), and is ng before anything else."""
    head = tuple(_sounds(prefix.removesuffix("N")))
    if not prefix.endswith("N"):
        return {head + word}
    first = word[0].s
    if first in "pb":
        return {head + (_Sound("m"),) + word[1:]}
    if first in "tds":
        return {head + (_Sound("n"),) + word[1:]}
    if first == "k":
        return {head + (_Sound("ŋ"),) + word[1:]}
    if first == "ʔ":  # a vowel-initial root: the glottal may stay or go
        return {head + (_Sound("ŋ"),) + word, head + (_Sound("ŋ"),) + word[1:]}
    return {head + (_Sound("ŋ"),) + word}


# ── the two directions ────────────────────────────────────────────────────────────────────────


def _infixed(infix: str, word: tuple[_Sound, ...]) -> set[tuple[_Sound, ...]]:
    """*infix* after the word's first consonant, a vowel-initial word's glottal included
    (k-in-a-matay-an, ʔ-in-ubra-han); before l, -in- may also come first as ni-
    (Wolff's entry 'ni-, -in-': linaktan, nilaktan)."""
    sounds = tuple(_sounds(infix))[1:]
    out = {word[:1] + sounds + word[1:]}
    if infix == "in" and word[0].s == "l":
        out.add(sounds[::-1] + word)
    return out


def generate(root: str, affix: str) -> list[Candidate]:
    """The forms §5 allows for *root* (Wolff's spelling) plus *affix* (``nag-``, ``-an``,
    ``ka-an``, ``-in-+-an``, with ``(→)``/``(←)`` for a shift of stress, §5.11)."""
    pre, inf, suf, shift = _split_affix(affix)
    sounds = _sounds(root)
    words = _suffixed(sounds, suf) if suf else {(tuple(sounds), ())}
    out: dict[str, tuple[str, ...]] = {}
    for word, rules in words:
        for prefixed in _prefixed(pre, word) if pre else {word}:
            for full in _infixed(inf, prefixed) if inf else {prefixed}:
                surface, applied = _spell(list(full)), rules
                if shift:
                    from app.plugins.languages.ceb.wolff_dictionary import shifted

                    surface, applied = shifted(surface, shift), (*rules, "5.11")
                out.setdefault(surface, applied)
    return [Candidate(s, r) for s, r in sorted(out.items())]


@cache
def _by_first(roots: frozenset[str]) -> dict[str, tuple[str, ...]]:
    index: dict[str, list[str]] = {}
    for root in roots:
        letters = strip_marks(root).lstrip("-")
        index.setdefault(letters[:1], []).append(root)
    return {k: tuple(v) for k, v in index.items()}


def _first_letters(core: str, prefix: str) -> set[str]:
    """Which first letters a root can have, given the core a prefix left."""
    core = core.lstrip("-")
    if not prefix.endswith("N"):
        return {core[:1]}
    if core.startswith("ng"):
        rest = core[2:].lstrip("-")
        return {"k", rest[:1], *_VOWELS}
    if core.startswith("m"):
        return {"p", "b"}
    if core.startswith("n"):
        return {"t", "d", "s"}
    return set()


def _shells() -> list[tuple[str, str, str]]:
    """Every (prefix, infix, suffix) decoding tries: each alone, and stacked."""
    pres, infs, sufs = ("", *_PREFIXES), ("", *_INFIXES), ("", *_SUFFIXES)
    return [(p, i, s) for p in pres for i in infs for s in sufs if (p or i or s) and _grammatical(p, s)]


def _core(target: str, pre: str, inf: str, suf: str) -> str | None:
    """*target* with the shell's letters taken off, or None when they are not there."""
    if not target.endswith(suf):
        return None
    word = target[: len(target) - len(suf)] if suf else target
    lead = pre.removesuffix("N")
    if inf:
        # the infix sits after the first letter of whatever comes first
        if lead:
            lead = lead[:1] + inf + lead[1:]
        elif word.startswith(inf):
            return word[len(inf) :] or None  # a vowel-initial root: in + ubra
        elif word[1 : 1 + len(inf)] == inf:
            return word[:1] + word[1 + len(inf) :]
        elif word.startswith("nil"):
            return word[2:]  # ni- before l
        else:
            return None
    if not word.startswith(lead) or len(word) <= len(lead):
        return None
    return word[len(lead) :]


def decode(surface: str, roots: frozenset[str]) -> list[Analysis]:
    """Every (root, affix) among *roots* that the rules turn into *surface*.

    A surface that is itself one of *roots* is that root and nothing else: Wolff
    lists strictly by root (§4), so a headword that looks affixed (``gikan``,
    ``ibabaw``) is not a form of a shorter one.
    """
    target = strip_marks(surface)
    own = [r for r in roots if strip_marks(r) == target]
    if own:
        return [Analysis(r, "", ()) for r in sorted(own)]
    index = _by_first(roots)
    found: dict[tuple[str, str], tuple[str, ...]] = {}
    for pre, inf, suf in _shells():
        core = _core(target, strip_marks(pre) if not pre.endswith("N") else pre, inf, strip_marks(suf))
        if core is None:
            continue
        affix = _label(pre, inf, suf)
        for first in _first_letters(core, pre):
            for root in index.get(first, ()):
                if abs(len(strip_marks(root)) - len(core)) > 2:
                    continue
                for cand in generate(root, affix):
                    if strip_marks(cand.surface) == target:
                        found.setdefault((root, affix), cand.rules)
    return [Analysis(root, affix, rules) for (root, affix), rules in sorted(found.items())]


# ── what a root's entry licenses, and ranking against the lexicon ─────────────────────────────

#: decode_classes' Handbook pairs (§7) -> the §6.1 cells each stands for, in Wolff's spelling:
#: every voice with its durative and potential cells.
_CELLS: dict[str, frozenset[str]] = {
    "mo-/mi-": frozenset(("mu-", "mi-", "ni-", "ning-", "ming-")),
    "mag-/nag-": frozenset(("mag-", "maga-", "nag-", "naga-", "ga-", "pag-")),
    "maka-/naka-": frozenset(("maka-", "ka-", "naka-")),
    "magka-/nagka-": frozenset(("magka-", "nagka-")),
    "makig-/nakig-": frozenset(("makig-", "nakig-", "pakig-")),
    "maN-/naN-": frozenset(("maN-", "naN-", "paN-")),
    "-on/gi-": frozenset(("-un", "gi-", "-a", "paga-un", "gina-", "paga-a", "ma-", "na-")),
    "-an/gi-an": frozenset(
        ("-an", "gi-an", "-i", "paga-an", "gina-an", "paga-i", "ma-an", "na-an", "ka-an", "ma-i", "ka-i")
    ),
    "i-/gi-": frozenset(("i-", "iga-", "gi-", "gina-", "-an", "ika-", "gika-")),
    "ma-/na-": frozenset(("ma-", "na-")),
    "ma-an/na-an": frozenset(("ma-an", "na-an", "ka-an", "ma-i", "ka-i")),
    "hi-an": frozenset(("hi-an", "hi-i")),
    "ika-/gika-": frozenset(("ika-", "gika-")),
}


#: Every affix a class formula can license: §6.1's chart, all voices.
CHART_AFFIXES: frozenset[str] = frozenset().union(*_CELLS.values())


def class_affixes(code: str) -> frozenset[str]:
    """The affixes a §7 class formula (``A; a12``) licenses, as this module spells them."""
    from app.plugins.languages.ceb.wolff_dictionary import decode_classes

    return frozenset().union(*(_CELLS[pair] for pair, _ in decode_classes(code)))


_STOP = frozenset((
    "the", "and", "for", "with", "from", "into", "that", "this", "his", "her", "its", "their", "one",
    "not", "but", "are", "was", "were", "has", "had", "have", "get", "got", "about", "than", "then",
    "there", "what", "when", "who", "will", "would", "can",
))  # fmt: skip


def _english_words(text: str) -> frozenset[str]:
    """Content words of *text*, cut to five letters so 'stitches' meets 'stitch'."""
    return frozenset(w[:5] for w in re.findall(r"[a-z]+", text.lower()) if len(w) > 2 and w not in _STOP)


class Lexicon(NamedTuple):
    roots: frozenset[str]
    written: Mapping[str, tuple[str, ...]]  # a written-out form's letters -> the roots printing it
    takes: Mapping[str, frozenset[str]]  # root -> the affixes its entry licenses
    gloss: Mapping[str, frozenset[str]]  # root -> its glosses' content words
    size: Mapping[str, tuple[int, int]]  # root -> (sub-entries, characters of gloss and example)
    root_pos: Mapping[str, frozenset[str]]  # root -> the parts of speech of its root block ('v', 'a', 'n')


def lexicon(entries: Iterable[Mapping[str, Any]]) -> Lexicon:
    """The decoder's view of Wolff's lexicon (``wolff_lexicon.json``'s entries)."""
    from app.plugins.languages.ceb.wolff_dictionary import is_affix_label

    entries = list(entries)
    roots = frozenset(h for e in entries for h in e["heads"])
    head_letters = {strip_marks(h) for h in roots}
    written: dict[str, list[str]] = {}
    takes: dict[str, set[str]] = {}
    gloss: dict[str, set[str]] = {}
    size: dict[str, tuple[int, int]] = {}
    root_pos: dict[str, set[str]] = {}
    same: list[tuple[str, str]] = []  # (head, target) of an entry that is only '= X' or 'see X'
    for e in entries:
        ok: set[str] = set()
        words: set[str] = set()
        chars = 0
        for f in e["forms"]:
            for piece in re.split(r",\s*|/", f["label"]):
                if is_affix_label(piece):
                    ok.update(a for a in [affix_of_label(piece)] if a)
                elif " " not in piece and not piece.startswith("(") and strip_marks(piece) not in head_letters:
                    written.setdefault(strip_marks(piece), []).append(e["heads"][0])
            for sense in f["senses"]:
                ok |= class_affixes(sense["cls"]) if sense["cls"] else set()
                words |= _english_words(sense["gloss"])
                chars += len(sense["gloss"]) + len(" ".join(sense["ex"] or ()))
        root = e["forms"][0]
        if root["xref"] and root["xref"][0] in ("=", "see"):
            same.extend((h, root["xref"][1]) for h in e["heads"])
        for h in e["heads"]:
            root_pos.setdefault(h, set()).update(sense["pos"] for sense in root["senses"] if sense["pos"])
            takes.setdefault(h, set()).update(ok)
            gloss.setdefault(h, set()).update(words)
            subs, total = size.get(h, (0, 0))
            size[h] = (subs + len(e["forms"]), total + chars)
    # A root that is only another's spelling ('lakaw = lakat') takes and means what that one does.
    for head, target in same:
        takes[head] |= takes.get(target, set())
        gloss[head] |= gloss.get(target, set())
        root_pos[head] |= root_pos.get(target, set())
    return Lexicon(
        roots,
        {k: tuple(sorted(set(v))) for k, v in written.items()},
        {k: frozenset(v) for k, v in takes.items()},
        {k: frozenset(v) for k, v in gloss.items()},
        size,
        {k: frozenset(v) for k, v in root_pos.items()},
    )


def licensed(a: Analysis, lex: Lexicon) -> bool:
    """Whether *a*'s root's entry takes its affix: whole (``gi-an``), or piece by piece."""
    takes = lex.takes[a.root]
    return a.affix in takes or all(piece in takes for piece in a.affix.split("+"))


def analyse(surface: str, lex: Lexicon, english: str = "") -> list[Analysis]:
    """*surface*'s analyses, best first.

    A headword is itself; a form Wolff writes out (§5.1) is its entry's root, by
    lookup. *surface* may be in today's spelling (:func:`wolff_letters`). Otherwise :func:`decode`, ranked by the meaning (*english*, the
    sentence or gloss the word came with, against each root's glosses), then by
    whether the root's entry takes the affix (its class formula and sub-entry
    labels), then by the size of the entry.
    """
    surface = wolff_letters(surface)
    written = lex.written.get(surface)
    if written:
        return [Analysis(root, "", ("5.1",)) for root in written]
    words = _english_words(english)

    def rank(a: Analysis) -> tuple[int, bool, bool, int, int]:
        subs, chars = lex.size[a.root]
        return (
            -len(lex.gloss[a.root] & words),
            not licensed(a, lex),
            a.root.startswith("-") or a.root.endswith("-"),
            -subs,
            -chars,
        )

    return sorted(decode(surface, lex.roots), key=rank)
