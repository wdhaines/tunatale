"""Read one entry of Wolff's *Dictionary of Cebuano Visayan* (1972) into its parts (tunatale-h0q3.2).

The cards show the dictionary in three tiers, as the Norwegian cards do: the
meaning and the verb's affixes, then the numbered senses with an example each,
who the subject is per affix, the related words and the homographs, then the
entry verbatim. This module turns an entry paragraph (Project Gutenberg #40074,
public domain) into the parts those tiers are built from. It converts no
spelling; ``scripts/build_cebuano_wolff_lexicon.py`` does that with
``handbook_spelling``.

An entry is a root and its sub-entries (intro §4.0: listing is strictly by
root). The marks it uses, read off the intro's table of symbols:

* ``tuu, túu_1`` heads, a number for homographs; ``*`` a root not used alone;
  ``†`` an addition; ``(from walà)`` its source.
* ``short form(s): x, y``, ``short for x``; ``= x`` (an alternative
  pronunciation, defined at x) and ``see x`` point elsewhere.
* ``n``, ``v``, ``a``, ``numeral``... the part of speech; ``{1}``, ``{2a}`` the
  senses; ``[A; ab2]`` the verb class (§7, decoded by :func:`decode_classes`).
* a sub-entry starts a sentence with an affix label (``paN-``, ``-in-ay``,
  ``ka-, kama-``) or with a written-out word and its part of speech
  (``katluan n thirty``). ``(→)`` and ``(←)`` shift the stress to the final
  syllable or the penult (§5.11); alone, they make a stress-shifted form of the
  root (``taas`` 'tall', ``(←)`` táas).
* ``—`` stands for the root in a phrase (``— ang buut`` 'be in a good mood').

Examples follow a gloss as "Cebuano, English." pairs; :func:`split_examples`
tells the halves apart.
"""

from __future__ import annotations

import gzip
import re
import unicodedata
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import NamedTuple

POS_LABELS = frozenset(
    [
        "n",
        "v",
        "a",
        "numeral",
        "particle",
        "interjection",
        "exclamation",
        "prefix",
        "suffix",
        "infix",
        "affix",
        "abbreviation",
        "expression",
        "pronoun",
        "conjunction",
        "preposition",
        "adverb",
    ]
)
# Pieces an affix label is built from (the dictionary's affix entries), so a label
# can be told from a hyphenated word: ka-an is an affix, tan-aw a word.
_AFFIX_PIECES = frozenset(
    [
        "a",
        "an",
        "anan",
        "anay",
        "ay",
        "g",
        "gi",
        "gika",
        "ha",
        "han",
        "hi",
        "hiN",
        "hun",
        "i",
        "ig",
        "iga",
        "igka",
        "ika",
        "in",
        "inig",
        "ini",
        "ka",
        "kama",
        "ki",
        "l",
        "m",
        "ma",
        "maN",
        "mag",
        "maga",
        "magka",
        "maka",
        "makapa",
        "maki",
        "makig",
        "man",
        "mang",
        "mi",
        "min",
        "mina",
        "mu",
        "mung",
        "n",
        "na",
        "naN",
        "nag",
        "naga",
        "nagka",
        "naka",
        "nakig",
        "ni",
        "ning",
        "pa",
        "paN",
        "pag",
        "paga",
        "pagka",
        "pahi",
        "paka",
        "paki",
        "pakig",
        "pala",
        "pan",
        "pang",
        "panggi",
        "pani",
        "paniN",
        "pi",
        "r",
        "s",
        "t",
        "ta",
        "tag",
        "taga",
        "ti",
        "tig",
        "tiN",
        "ting",
        "u",
        "um",
        "un",
        "y",
    ]
)
_ABBREVIATIONS = ("s.t.", "s.o.", "k.o.", "s.w.", "e.g.", "i.e.", "lit.", "cf.", "etc.", "gen.", "dat.", "Lit.")
_SHIFT = re.compile(r"\((→|←)\)")
_TOKEN_CHARS = r"[^\s,.;:?!(){}\[\]‘’“”\"]"


class Example(NamedTuple):
    ceb: str
    en: str


@dataclass
class Sense:
    pos: str | None = None
    number: str | None = None
    classes: str | None = None
    gloss: str = ""
    examples: list[Example] = field(default_factory=list)
    phrase: str | None = None  # the words after '—' (the root) in a phrase sense


@dataclass
class Form:
    """The root, or one sub-entry."""

    label: str  # the root's head, an affix label ('paN-'), a written word, or '(←)'
    form: str | None  # the word in Wolff's spelling, when it can be read off the entry
    shift: str | None = None  # '→' or '←'
    short_forms: list[str] = field(default_factory=list)
    xref: tuple[str, str] | None = None  # ('=', 'anáa'), ('see', 'áyu'), ('short for', 'kiri')
    senses: list[Sense] = field(default_factory=list)


@dataclass
class Entry:
    id: str  # the first head as printed, with its homograph number: 'kini_1'
    heads: list[str]
    number: str | None
    root: Form
    subs: list[Form]
    text: str  # the paragraph, verbatim
    not_alone: bool = False  # '*': a root not used alone
    source: str | None = None  # '(from walà)'


def strip_marks(word: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", word) if unicodedata.category(c) != "Mn")


def _letters(word: str) -> str:
    return strip_marks(word).lower().replace("-", "")


def is_affix_label(label: str) -> bool:
    """True for an affix label (``ma-``, ``paN-un``, ``-in-an(→)``, ``ka-, kama-``), not a word."""
    parts = [p.strip() for p in re.split(r",\s*|/", _SHIFT.sub("", label)) if p.strip()]
    if not parts:
        return False
    for p in parts:
        if "-" not in p or not re.fullmatch(r"[a-zN\-]+", p):
            return False
        if any(piece not in _AFFIX_PIECES for piece in p.split("-") if piece):
            return False
    return True


def shifted(root: str, shift: str) -> str:
    """*root* in Wolff's spelling with its stress moved: '→' to the final syllable, '←' to the penult."""
    plain = unicodedata.normalize("NFC", "".join(c for c in unicodedata.normalize("NFD", root) if c not in "́̄̌"))
    letters = list(unicodedata.normalize("NFD", plain))
    vowels = [i for i, c in enumerate(letters) if c.lower() in "aeiou"]
    if len(vowels) < 2:
        return plain
    penult, final = vowels[-2], vowels[-1]
    between = "".join(c for c in letters[penult + 1 : final] if unicodedata.category(c) != "Mn")
    closed = len(between.replace("ng", "N")) >= 2 or "̀" in letters[penult + 1 : penult + 2]
    if shift == "←":
        letters.insert(penult + 1, "́")
    elif closed:
        letters.insert(final + 1, "̌")  # the wedge: a stressed final after a closed penult
    return unicodedata.normalize("NFC", "".join(letters))


# --- examples ---------------------------------------------------------------------------------


@cache
def _english() -> frozenset[str]:
    import wordfreq

    cebuano_too = {"ang", "sa", "ug", "nga", "ka", "na", "ba", "man", "si", "ni", "kay", "ku", "ta", "mu", "mi", "ra"}
    cebuano_too |= {"pa", "may", "wa", "ay", "di", "pud", "sab", "day", "bay", "ha", "uy", "ma", "kita", "unsa", "ari"}
    return frozenset(wordfreq.top_n_list("en", 20000)) - cebuano_too


def _is_cebuano(clause: str) -> bool:
    words = re.findall(r"[^\W\d_][\ẁ-ͯ\-’']*", unicodedata.normalize("NFD", clause))
    if not words:
        return False
    if any(re.search(r"[̀́̂̄̌]", w) or re.search(r"\w-\w", w) for w in words):
        return True
    english = sum(strip_marks(w).lower().strip("’'") in _english() for w in words)
    return english * 2 < len(words)


@cache
def _cebuano_counts() -> dict[str, int]:
    """Word -> its count in the Cebuano news corpus (the frequency list the cards use)."""
    out: dict[str, int] = {}
    with gzip.open(Path(__file__).parent / "data" / "cebuano_frequency.tsv.gz", "rt") as fh:
        for line in fh:
            word, _, count = line.rstrip("\n").partition("\t")
            out[word] = int(count.split("\t")[0]) if count.split("\t")[0].isdigit() else 0
    return out


def _english_word(token: str) -> bool:
    """Whether *token* is English, though rare: English uses it (wordfreq zipf >= 2) and the
    Cebuano news corpus hardly does. 'spiderweb' and 'crowbar' are not among the 20,000
    commonest English words, but they are English; 'sa' and 'lang' are common in both, and
    Cebuano. A hyphened compound is English when every part is (thick-lipped). Wolff's own
    headwords never are: his 1972 spelling (batu, tris) is not the corpus's, and a glottal
    hyphen (lut-ud) is no compound."""
    import wordfreq

    if re.search(r"[̀́̂̄̌]", unicodedata.normalize("NFD", token)):
        return False  # Wolff marks only Cebuano
    word = token.lower().strip(".,;:()’'\"")
    if word in _wolff_heads():
        return False  # Wolff's own headword (animas, kiting, burd 'board'), however English it looks
    parts = word.split("-")
    return all(
        p.isalpha() and wordfreq.zipf_frequency(p, "en") >= 2 and _cebuano_counts().get(p, 0) < 20 for p in parts
    )


@cache
def _wolff_heads() -> frozenset[str]:
    """The headwords of the shipped lexicon, unmarked. Heads do not depend on how a phrase
    is split, so rebuilding the lexicon may read the last build's."""
    import json

    with gzip.open(Path(__file__).parent / "data" / "wolff_lexicon.json.gz", "rt") as fh:
        entries = json.load(fh)["entries"]
    return frozenset(strip_marks(h).lower().split("_")[0] for e in entries for h in e["heads"])


def split_examples(text: str) -> list[Example]:
    """'Ceb, Eng. Ceb? Eng?' -> examples. A clause is Cebuano by its marks or its words."""
    clauses = [c for c in re.split(r"(?<=[,.?!])\s+(?=[A-ZÀ-Ý‘“'\"(])", text.strip()) if c]
    runs: list[tuple[bool, list[str]]] = []
    for c in clauses:
        ceb = _is_cebuano(c)
        if runs and runs[-1][0] == ceb:
            runs[-1][1].append(c)
        else:
            runs.append((ceb, [c]))
    out = []
    for i, (ceb, cs) in enumerate(runs):
        if ceb and i + 1 < len(runs):
            out.append(Example(" ".join(cs).rstrip(",").strip(), " ".join(runs[i + 1][1]).strip()))
    return out


def _gloss_and_examples(text: str) -> tuple[str, list[Example]]:
    """The gloss runs to the first sentence end before a capital: the first example.
    An abbreviation ends the gloss too ('the way one regards s.t. Sa ákung tan-aw...')."""
    m = re.search(r"[.?!]\s+(?=[A-ZÀ-Ý‘“'\"])", text)
    if not m:
        return text.strip().rstrip("."), []
    end = m.start() + 1
    gloss = text[:end].strip()
    if gloss.endswith(".") and not gloss.endswith(_ABBREVIATIONS):
        gloss = gloss[:-1]
    return gloss, split_examples(text[end:])


# --- senses -----------------------------------------------------------------------------------

_SENSE_NUMBER = re.compile(r"\{(\w+)\}")
_CLASSES = re.compile(r"\[([^\]]+)\]")


def _sentence_end(prev: str) -> bool:
    return prev.endswith((".", "?", "!", "’", "”")) and not prev.endswith(_ABBREVIATIONS)


def _senses(text: str) -> tuple[list[Sense], list[str], tuple[str, str] | None]:
    """A form's body -> (senses, short forms, cross-reference)."""
    short: list[str] = []
    xref = None
    senses: list[Sense] = []
    tokens = text.split()
    cur: Sense | None = None
    heading_classes: str | None = None  # a code before the first number holds for the numbered senses
    words: list[str] = []
    i = 0

    def close():
        nonlocal words
        if cur is not None and words:
            body = " ".join(words)
            gloss, examples = _gloss_and_examples(body)
            cur.gloss, cur.examples = gloss, examples
        words = []

    pos: str | None = None
    while i < len(tokens):
        t = tokens[i]
        prev = tokens[i - 1] if i else "."
        at_start = i == 0 or _sentence_end(prev) or _SENSE_NUMBER.fullmatch(prev) is not None
        if t in POS_LABELS and (at_start or (cur is not None and not words)):
            close()
            pos, heading_classes = t, None
            cur = None
            i += 1
            continue
        if t in ("short", "Short") and i + 1 < len(tokens) and tokens[i + 1].startswith("form"):
            j = i + 2
            while j < len(tokens):
                word = tokens[j].rstrip(",.")
                if not word or word.startswith("{"):
                    break
                short.append(word)
                j += 1
                if not tokens[j - 1].endswith(","):
                    break
            i = j
            continue
        if t == "=" and at_start and cur is None:
            m = re.match(r"(.+?)\.(?:\s|$)", " ".join(tokens[i + 1 :]) + " ")
            if m:
                xref = ("=", m.group(1))
                i += 1 + len(m.group(1).split())
                continue
        if t == "see" and at_start and cur is None and not senses and i + 1 < len(tokens):
            xref = ("see", tokens[i + 1].rstrip("."))
            i += 2
            continue
        if t == "short" and i + 1 < len(tokens) and tokens[i + 1] == "for" and cur is None and not senses:
            xref = ("short for", tokens[i + 2].rstrip(".")) if i + 2 < len(tokens) else None
            i += 3
            continue
        m = _SENSE_NUMBER.fullmatch(t)
        if m:
            close()
            cur = Sense(pos=pos, number=m.group(1), classes=heading_classes)
            senses.append(cur)
            i += 1
            continue
        if t == "—" and at_start:
            close()
            j = i + 1
            phrase = []
            while j < len(tokens) and tokens[j] not in POS_LABELS and not tokens[j].startswith(("[", "{")):
                if _sentence_end(tokens[j - 1]) and j > i + 1:
                    break
                if phrase and (not _is_cebuano(tokens[j]) or _english_word(tokens[j])):
                    break  # no part of speech given: the gloss starts at the first English word
                phrase.append(tokens[j])
                j += 1
            ppos = tokens[j] if j < len(tokens) and tokens[j] in POS_LABELS else pos
            if j < len(tokens) and tokens[j] in POS_LABELS:
                j += 1
            cur = Sense(pos=ppos, phrase=" ".join(phrase))
            senses.append(cur)
            i = j
            continue
        if t.startswith("[") and not words:
            j = i
            while not tokens[j].endswith("]") and j + 1 < len(tokens):
                j += 1
            code = " ".join(tokens[i : j + 1])[1:].rstrip("]")
            if cur is None:
                heading_classes = code
            else:
                cur.classes = code
            i = j + 1
            continue
        if cur is None:
            cur = Sense(pos=pos, classes=heading_classes)
            senses.append(cur)
        words.append(t)
        i += 1
    close()
    return [s for s in senses if s.gloss or s.examples or s.phrase], short, xref


# --- sub-entries ------------------------------------------------------------------------------

#: A head's letters: lower case, with the marks Wolff prints on them. Accented
#: heads are the run-in possessives ('átù {1} gen.' inside kita), which an ASCII
#: class read as kita's own senses (the owner, 2026-10-18). Never upper case: an
#: example sentence starts with a capital.
_HEAD_LETTERS = "a-zàáâèéìíîòóôùúûāǎēīǐōūǔñN"
_LABEL = rf"(?:\((?:→|←)\)|-?[{_HEAD_LETTERS}]+(?:-[{_HEAD_LETTERS}]*)*-?(?:\((?:→|←)\))?)"
_LABEL_LIST = rf"{_LABEL}(?:(?:, |/){_LABEL})*"


def _head_at(tokens: list[str], i: int) -> int:
    """Length in tokens of a sub-entry head starting at tokens[i], or 0."""
    rest = " ".join(tokens[i : i + 8])
    m = re.match(rf"({_LABEL_LIST})(?=\s|$)", rest)
    if not m:
        return 0
    label = m.group(1)
    n = len(label.split())
    nxt = tokens[i + n] if i + n < len(tokens) else ""
    if _SHIFT.fullmatch(label) or is_affix_label(label):
        return n
    words = [w for w in re.split(r",\s*", label)]
    cebuano = all(re.fullmatch(r"[a-z][a-z\-]*[a-z]", strip_marks(w)) for w in words)
    # 'preposed' heads a possessive with neither a sense number nor a part of
    # speech ('íla, iláha preposed gen.: their' inside sila).
    if cebuano and (nxt in POS_LABELS or nxt.startswith("{") or nxt in ("short", "preposed")):
        return n
    return 0


def compose(label: str, root: str) -> str | None:
    """The word an affix sub-entry names, where the rule is plain: a prefix, or an infix after
    the first consonant. ``N`` is the nasal that takes the place of p b (m), t d s (n) and k
    (ng), and is ng before anything else (paN- + tan-aw = panan-aw, + lakaw = panglakaw). A
    suffix moves the stress unpredictably, so those are left to the entry's own examples."""
    first = re.split(r",\s*|/", label)[0]
    shift = (_SHIFT.search(first) or [None, None])[1]
    affix = _SHIFT.sub("", first)
    if not is_affix_label(affix):
        return None
    if re.fullmatch(r"[a-zN]{2,}-|i-", affix):
        stem = affix[:-1]
        if stem.endswith("N"):
            head = strip_marks(root[:1]).lower()
            stem = stem[:-1]
            if head in "pb":
                word = stem + "m" + root[1:]
            elif head in "tds":
                word = stem + "n" + root[1:]
            elif head == "k":
                word = stem + "ng" + root[1:]
            else:
                word = stem + "ng" + root
        else:
            word = stem + root
    elif m := re.fullmatch(r"-([a-z]+)-", affix):
        lead = re.match(r"[^aeiou]*", strip_marks(root).lower()).end()
        word = root[:lead] + m.group(1) + root[lead:] if lead <= 1 else None
        if word is None:
            return None
    else:
        return None
    return shifted(word, shift) if shift else word


def _word_from_examples(label: str, root: str, senses: list[Sense]) -> str | None:
    """An affix sub-entry's word, read off its first example that carries the root."""
    core = _letters(root)
    if core and core[0] not in "aeiou":
        core = core[1:]  # paN- replaces the first consonant (tan-aw -> panan-aw)
    for s in senses:
        for ex in s.examples:
            for tok in re.findall(rf"{_TOKEN_CHARS}+", ex.ceb):
                if core in _letters(tok) and _letters(tok) != _letters(root):
                    return tok[0].lower() + tok[1:]
    return None


def parse_entry(paragraph: str) -> Entry:
    text = paragraph
    not_alone = text.startswith("*") or text.startswith("†*")
    body = text.lstrip("†*")
    m = re.match(r"((?:[^\s,]+, )*[^\s,]+)\s*", body)
    heads_raw = m.group(1).split(", ")
    rest = body[m.end() :]
    number = None
    heads = []
    for h in heads_raw:
        word, _, num = h.partition("_")
        heads.append(word)
        number = num or number
    source = None
    sm = re.match(r"\((from [^)]*)\)\s*", rest)
    if sm:
        source, rest = sm.group(1), rest[sm.end() :]
    rest = re.sub(r"^\(not [^)]*\)\s*", "", rest)
    root_shift = None
    rm = re.match(r"\((→|←)\)\s*", rest)
    if rm:
        root_shift, rest = rm.group(1), rest[rm.end() :]

    tokens = rest.split()
    blocks: list[tuple[str | None, int, int]] = []  # (label, start, end) over tokens
    start, label = 0, None
    i = 0
    while i < len(tokens):
        n = _head_at(tokens, i) if i and _sentence_end(tokens[i - 1]) else 0
        if n:
            blocks.append((label, start, i))
            label, start = " ".join(tokens[i : i + n]), i + n
            i += n
            continue
        i += 1
    blocks.append((label, start, len(tokens)))

    root_head = heads[0]
    root = Form(label=root_head, form=root_head, shift=root_shift)
    subs = []
    for label, a, b in blocks:
        senses, short, xref = _senses(" ".join(tokens[a:b]))
        if label is None:
            root.senses, root.short_forms, root.xref = senses, short, xref
            continue
        shift = (_SHIFT.search(label) or [None, None])[1]
        if _SHIFT.fullmatch(label):
            form = shifted(root_head, shift)
        elif is_affix_label(label):
            form = compose(label, root_head) or _word_from_examples(label, root_head, senses)
        else:
            form = re.split(r",\s*", label)[0]
        subs.append(Form(label=label, form=form, shift=shift, short_forms=short, xref=xref, senses=senses))
    first = paragraph.lstrip("†*").split(" ")[0].rstrip(",")
    return Entry(
        id=first, heads=heads, number=number, root=root, subs=subs, text=paragraph, not_alone=not_alone, source=source
    )


# --- verb classes (intro §7.11-7.231) ---------------------------------------------------------

DOER, UNDERGOER, TOGETHER = "the one doing it", "the one it happens to", "those doing it together"
CAUSE = "what causes it"
DIRECT, PLACE, USED = "what it is done to", "where or for whom it is done", "what is used or moved"
_LOCAL_SUBJECT = {
    "": "who or what it is done to",
    "1": "where it is done, or who it is done to",
    "3": "the reason for it",
    "4": "what it affects",
    "5": "what it is done to",
    "6": PLACE,
    "7": "what is taken from or added to",
}


def decode_classes(code: str) -> list[tuple[str, str]]:
    """'[A; ab2]' -> the affixes the verb takes (Handbook spelling) and who is the subject of each."""
    out: list[tuple[str, str]] = []

    def add(affix: str, subject: str) -> None:
        if affix not in dict(out):
            out.append((affix, subject))

    parts = [p.strip() for p in code.split(";")]
    if not all(re.fullmatch(r"(?:[ABCabc](?:\d|\(\d\)|[SPN])*)+", p) for p in parts):
        return []
    for part in parts:
        for letter, digits in re.findall(r"([ABCabc])((?:\d|\(\d\)|[SPN])*)", part):
            nums = set(re.sub(r"\(\d\)|[SPN]", "", digits))
            paren = re.findall(r"\((\d)\)", digits)
            if letter == "A":
                for d, affix in (("1", "mo-/mi-"), ("2", "mag-/nag-"), ("3", "maka-/naka-")):
                    if d not in nums:
                        add(affix, DOER)
                if "N" in digits:
                    add("maN-/naN-", DOER)
            elif letter == "B":
                # §7.12: class B's maka- (or makapa-) means 'cause s.t. to become [so-and-so]', so its
                # subject is the cause; §7.121: on B3 it means 'become' instead, and B3(1) has both
                maka = f"{UNDERGOER}, or {CAUSE}" if "3(1)" in digits else UNDERGOER if "3" in nums else CAUSE
                for d, affix in (
                    ("1", "mo-/mi-"),
                    ("2", "mag-/nag-"),
                    ("4", "ma-/na-"),
                    ("5", "maka-/naka-"),
                    ("6", "magka-/nagka-"),
                ):
                    if d not in nums:
                        add(affix, maka if affix == "maka-/naka-" else UNDERGOER)
                if "N" in digits:  # §7.121: BN takes paN- with mu-, as AN does
                    add("maN-/naN-", UNDERGOER)
            elif letter == "C":
                for d, affix in (("1", "mag-/nag-"), ("2", "magka-/nagka-"), ("3", "makig-/nakig-")):
                    if d not in nums:
                        add(affix, TOGETHER)
            elif letter == "a":
                if "3" in nums:
                    add("ma-/na-", DIRECT)
                    if "1" not in nums:
                        add("ma-an/na-an", PLACE)
                    continue
                add("-on/gi-", "who is affected" if "4" in nums else DIRECT)
                if "1" not in nums:
                    add("-an/gi-an", PLACE)
                if "2" not in nums:
                    add("i-/gi-", USED)
            elif letter == "b":
                if "8" in nums:
                    add("ma-an/na-an", "who it is done to")
                    continue
                if nums == {"2"}:
                    add("hi-an", "who it happens to by accident")
                    continue
                sub = next((d for d in "134567" if d in nums), "")
                add("-an/gi-an", _LOCAL_SUBJECT[sub])
                if "2" in nums:
                    add("hi-an", "who it happens to by accident")
                if not paren and sub not in ("6",) and "(1)" not in digits:
                    add("i-/gi-", USED)
            else:  # c
                if "3" in nums:
                    add("ika-/gika-", "who it is done to")
                    continue
                add("i-/gi-", "the reason for it" if "5" in nums else "what is moved or conveyed")
                if "1" in nums:
                    add("-on/gi-", DIRECT)
                if "6" not in nums:
                    add("-an/gi-an", PLACE)
    return out
