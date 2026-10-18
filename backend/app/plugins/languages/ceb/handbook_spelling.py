"""Wolff's dictionary spelling -> the Handbook spelling the course uses (tunatale-h0q3.2).

John Wolff's *Dictionary of Cebuano Visayan* (1972) writes three vowels
(``i u a``) and marks a long vowel with an acute, a syllable-final glottal stop
with a grave, and post-consonantal glottal stop with a hyphen. Stress is left to
rule, and so is the glottal stop between vowels (intro §3.0, §3.21-3.22). The
owner's course, Wolff's *Beginning Cebuano* respelled to Raisanen & Raisanen's
*A Handbook of Cebuano Visayan* (2016), marks both, because the learner cannot
see either otherwise:

* **stress** gets an acute on every word of two or more syllables. The
  dictionary's rule finds it: on the long vowel; else on a closed penult; else
  on the final syllable. A wedge marks a stressed final after a closed penult
  (``mandǎr``). A vowel pair (``maay``, ``buuk``, ``diay``) has a glottal stop
  between, which opens the second syllable and leaves the first open.
* **glottal stop** is marked on the vowel before it, everywhere except word
  start: a grave, or a circumflex where that vowel is also stressed
  (``maáyu`` -> ``màáyo``, ``náa`` -> ``nâa``, ``walà`` -> ``walâ``). After a
  consonant it stays a hyphen (``tán-aw``).
* **e and o** follow the Raisanen rules (owner, 2026-10-16): native words write
  ``i``; ``u`` is ``o`` in the root's last syllable and in the affixes ``mo-``
  and ``-on``; a Spanish or English loan keeps the vowels Cebuano writers use,
  judged from the news corpus.

The vowel rules are ported from the owner's book tool
(``~/Books/wolff/tools/raisanen.py``), which respelled the 1966 course. The
data it reads is passed in as a :class:`SpellingLexicon`, so this module does no
I/O; ``scripts/wolff_handbook_oracle.py`` builds one from the real sources.
"""

from __future__ import annotations

import itertools
import re
import unicodedata
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import NamedTuple

COURSE_SPELLINGS = Path(__file__).parent / "data" / "handbook_course_spellings.tsv"

ACUTE, GRAVE, CIRCUMFLEX, CARON, MACRON = "́", "̀", "̂", "̌", "̄"
# A mark that makes its vowel the stressed one. Wolff's macron is length, and
# the one long vowel of a word carries its stress (intro §3.0).
_STRESS_MARKS = {ACUTE, CIRCUMFLEX, CARON, MACRON}
_GLOTTAL_MARKS = {GRAVE, CIRCUMFLEX}
_VOWELS = set("aeiou")

# Spanish by spelling, English by sound; the caps are raisanen.py's. Rank
# caps alone do not separate loans from coincidences (cuchara #12568 and tiza
# #30281 are loans; velo and Belén #9400 are not): the corpus check and the
# preference for Wolff's own roots do that.
ES_CAP, EN_CAP = 20000, 30000

# Whole words settled outside the rules, marks included: oo is long, not
# glottal (owner, 2026-10-16); ug 'and'/og 'object marker' and kun 'or'/kon 'if'
# depend on the sense, so the caller decides.
WHOLE_WORDS = {"uu": "oo", "ug": "ug", "kun": "kun"}
# Vowels settled outside the rules; stress and glottal stop are still marked.
# gud and gung keep u (owner, Lesson 3); gyud is the corpus's spelling of
# Wolff's dyud; Místir is Wolff's title 'Mr.' as he spells it, and uy the
# interjection (owner, Lesson 1). maw stays maw: the course gives it as the
# short form of màó (Wolff's mau).
VOWEL_OVERRIDES = {
    "gud": "gud",
    "gung": "gung",
    "dyud": "gyud",
    "mistir": "mistir",
    "uy": "uy",
}

_PREFIXES = sorted(
    [
        "nakig",
        "makig",
        "pakig",
        "nagpa",
        "magpa",
        "pagpa",
        "gipa",
        "ipa",
        "mapa",
        "napa",
        "nagka",
        "magka",
        "pagka",
        "naka",
        "maka",
        "paka",
        "nahi",
        "mahi",
        "nag",
        "mag",
        "pag",
        "mang",
        "nang",
        "pang",
        "man",
        "nan",
        "pan",
        "mam",
        "nam",
        "pam",
        "ka",
        "ma",
        "na",
        "mi",
        "mu",
        "ni",
        "gi",
        "hi",
        "i",
        "pa",
        "ig",
        "inig",
        "ini",
        "taga",
        "tag",
        "ika",
        "maki",
        "naki",
    ],
    key=len,
    reverse=True,
)
_DERIV = ["anan", "hanan", "an", "han", "un", "hun", "a", "ha", "i", "hi"]
_CLITIC = ["ng", "y", "s", "g"]  # linker, existential/subject y, contracted sa / ug


class Loan(NamedTuple):
    template: str  # the word in Cebuano sound spelling, with the source's e/o
    source: str  # "es" or "en"
    word: str  # the Spanish or English word


@dataclass(frozen=True)
class SpellingLexicon:
    freq: Mapping[str, int]  # news-corpus counts by spelling
    entries: Mapping[str, str]  # Wolff headword, accent-free -> entry text, accent-free lowercase
    loans: Mapping[str, Loan]  # sound key -> loan
    # Wolff spelling (lowercase NFC) -> the course's spelling, where the two books differ
    course: Mapping[str, str] = field(default_factory=dict)


class HandbookSpelling(NamedTuple):
    text: str
    how: str  # how the vowels were chosen: native, loan-es, loan-en, override, ...


def strip_accents(word: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", word) if unicodedata.category(c) != "Mn")


def sound_key(word: str) -> str:
    """The spelling with e/o folded into i/u: what a Spanish word and Wolff's spelling share."""
    return word.replace("e", "i").replace("o", "u")


def spanish_to_cebuano(word: str) -> str | None:
    """Spanish word -> Cebuano sound spelling with its e/o kept; None if a letter can't be mapped."""
    w = strip_accents(word.lower()).replace("ü", "w")
    if not re.fullmatch(r"[a-zñ]+", w):
        return None
    rules = [
        ("ch", "ts"),
        ("ll", "ly"),
        ("ñ", "ny"),
        ("qu", "k"),
        ("gue", "Ge"),
        ("gui", "Gi"),
        ("cu(?=[aeio])", "kw"),
        ("gu(?=[ao])", "gw"),
        ("c(?=[ei])", "s"),
        ("c", "k"),
        ("z", "s"),
        ("v", "b"),
        ("j", "h"),
        ("g(?=[ei])", "h"),
        ("h", ""),
        ("x", "ks"),
        ("f", "p"),
        ("(?<=[^aeiou])ie", "ye"),
        ("(?<=[^aeiou])ue", "we"),
        ("y$", "i"),
        ("rr", "r"),
    ]
    for a, b in rules:
        w = re.sub(a, b, w)
    return w.replace("G", "g")


def build_lexicon(
    *,
    freq: Mapping[str, int],
    entries: Mapping[str, str],
    es_words: Sequence[str],
    en_sounds: Iterable[tuple[str, str, int]],
    english_common: Collection[str],
    course_spellings: Mapping[str, str] | None = None,
) -> SpellingLexicon:
    """Assemble the lexicon. ``es_words`` is a frequency-ordered Spanish word list;
    ``en_sounds`` holds (English word, Cebuano sound spelling, frequency rank);
    ``english_common`` is the common English vocabulary used to drop English words
    that wordfreq's Spanish list carries (``book``, which made buuk 'piece' bòók)."""
    loans: dict[str, Loan] = {}
    es_words = list(es_words[:ES_CAP])
    es_set = set(es_words)
    for w in es_words:
        if w.endswith("s") and (w[:-1] in es_set or w[:-2] in es_set):
            continue  # plurals collide with Cebuano contracted -s (iskínas = eskina sa)
        c = spanish_to_cebuano(w)
        if not c or len(c) < 4 or (w in english_common and c == w):
            continue
        loans.setdefault(sound_key(c), Loan(c, "es", w))
    for word, template, rank in en_sounds:
        if rank < EN_CAP and len(template) >= 3 and "-" not in template:
            loans.setdefault(sound_key(template), Loan(template, "en", word))
    return SpellingLexicon(freq=freq, entries=entries, loans=loans, course=dict(course_spellings or {}))


@cache
def load_course_spellings() -> dict[str, str]:
    """The committed table of words where the course's spelling wins."""
    out = {}
    for line in COURSE_SPELLINGS.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            wolff, course, _note = line.split("\t")
            out[unicodedata.normalize("NFC", wolff)] = unicodedata.normalize("NFC", course)
    return out


# --- vowels: e/o ------------------------------------------------------------------------------


def _loan_ok(root: str, lex: SpellingLexicon) -> bool:
    """A sound match with an English word counts only if the root isn't a Wolff
    headword, or its entry names the English word: kirí 'this' sounds like 'Kerry',
    írì 'letter r' like 'eerie'."""
    loan = lex.loans.get(sound_key(root))
    if loan is None:
        return False
    if loan.source == "es":
        return True
    entry = lex.entries.get(root)
    if entry is None and root.endswith("r"):
        entry = lex.entries.get(root[:-1] + "d")
    if entry is None:
        return len(root) >= 4  # not in Wolff at all: a loan, unless too short to tell (ikú ~ 'echo')
    return re.search(rf"\b{re.escape(loan.word)}", entry) is not None


def _root_ok(root: str, lex: SpellingLexicon) -> tuple[bool, bool] | None:
    """(is one of Wolff's headwords, is a loan sound-match), or None for neither."""
    if len(root) < 2:
        return None
    wolff = root in lex.entries or (root.endswith("r") and root[:-1] + "d" in lex.entries)
    loan = len(root) >= 3 and _loan_ok(root, lex)
    return (wolff, loan) if (wolff or loan) else None


def _analyse(base: str, lex: SpellingLexicon):
    """base (lowercase, no accents) -> (prefix, root, suffix, clitic, kind, doubled), or None."""
    best = None
    for cl in ["", *_CLITIC]:
        if cl and not base.endswith(cl):
            continue
        b1 = base[: len(base) - len(cl)]
        for dv in ["", *_DERIV]:
            if dv and not b1.endswith(dv):
                continue
            b2 = b1[: len(b1) - len(dv)]
            pres = [""]
            for p1 in _PREFIXES:
                if b2.startswith(p1):
                    pres.append(p1)
                    pres.extend(p1 + "|" + p2 for p2 in _PREFIXES if b2[len(p1) :].startswith(p2))
            for pre in pres:
                r = b2[len(pre.replace("|", "")) :]
                hy = ""
                if pre and r.startswith("-"):
                    r, hy = r[1:], "-"
                doubled = False
                ok = _root_ok(r, lex)
                if not ok:
                    m = re.fullmatch(r"(.+?)-?\1", r)
                    if m and (ok := _root_ok(m.group(1), lex)):
                        doubled = True
                if ok and cl in ("s", "g") and not ok[0]:
                    ok = None  # contracted sa / ug follows real words (kwártus), not loan look-alikes
                if ok:
                    kind = "loan" if ok[1] else "wolff"
                    # a split whose root is one of Wolff's headwords beats a longer
                    # Spanish-only match (librung = libru + ng, not 'lebrón' + g)
                    score = (ok[0], len(r), -len(pre), -len(dv) - len(cl))
                    if best is None or score > best[0]:
                        best = (score, (pre + hy, r, dv, cl, kind, doubled))
    return best[1] if best else None


def _last_syllable_o(s: str) -> str:
    """u -> o in the last vowel run (Raisanen: o only in the root's final syllable).
    In a run like 'ua' only the last vowel belongs to the final syllable."""
    m = re.search(r"([aiu]+)([^aiu]*)$", s)
    if not m:
        return s
    run = m.group(1)
    return s[: m.start()] + run[:-1] + ("o" if run[-1] == "u" else run[-1]) + m.group(2)


def _apply_template(root: str, template: str) -> str:
    return "".join(t if t in "eo" else c for c, t in zip(root, template, strict=True))


def _affix_o(pre: str, dv: str) -> tuple[str, str]:
    hy = "-" if pre.endswith("-") else ""
    pre = pre.rstrip("-")
    pre2 = "".join("mo" if p == "mu" else p for p in pre.split("|")) + hy if pre else ""
    return pre2, {"un": "on", "hun": "hon"}.get(dv, dv)


def choose_vowels(base: str, lex: SpellingLexicon) -> tuple[str, str]:
    """Choose e/o in an accent-free lowercase base. -> (new base, how)."""
    if base in VOWEL_OVERRIDES:
        return VOWEL_OVERRIDES[base], "override"
    a = _analyse(base, lex)
    if not a:
        # No Wolff or loan root: follow the corpus where it clearly attests a spelling.
        rule = _last_syllable_o(base)
        alts = {
            "".join(p) for p in itertools.product(*[(c, {"i": "e", "u": "o"}[c]) if c in "iu" else (c,) for c in base])
        }
        best = max(sorted(alts), key=lambda c: lex.freq.get(c, 0))
        n = lex.freq.get(best, 0)
        if n >= 20 and n > 3 * lex.freq.get(rule, 0) and len(alts) <= 256:
            return best, "unknown-corpus"
        return rule, "unknown"
    pre, r, dv, cl, kind, doubled = a
    pre2, dv2 = _affix_o(pre, dv)
    if kind == "loan" and not doubled:
        loan = lex.loans[sound_key(r)]
        sp, nat = _apply_template(r, loan.template), _last_syllable_o(r)
        f_sp, f_nat = lex.freq.get(sp, 0), lex.freq.get(nat, 0)
        # Judge the whole word where the corpus knows it: ibilin 71 vs ibelen 0, though
        # the bare root loses to the name Belen.
        full_sp, full_nat = pre2 + sp + dv2 + cl, pre2 + nat + dv2 + cl
        if lex.freq.get(full_sp, 0) or lex.freq.get(full_nat, 0):
            f_sp, f_nat = lex.freq.get(full_sp, 0), lex.freq.get(full_nat, 0)
        if f_sp >= 3 and f_sp * 3 >= f_nat:
            r2, how = sp, f"loan-{loan.source}"  # writers use the source's vowels
        elif f_nat >= 5:
            r2, how = nat, f"loan-{loan.source}-convention"  # writers settled on i/u (pulis)
        elif loan.source == "en" or len(r) >= 5:
            r2, how = sp, f"loan-{loan.source}-source"  # corpus doesn't know it: source's vowels
        else:
            r2, how = nat, "native"  # short Spanish look-alike, no evidence (karí, ikú)
    elif doubled:
        half = re.fullmatch(r"(.+?)(-?)\1", r)
        r2 = _last_syllable_o(half.group(1)) + half.group(2) + _last_syllable_o(half.group(1))
        how = "native+doubled"
    else:
        r2, how = _last_syllable_o(r), "native"
    return pre2 + r2 + dv2 + cl, how


# --- stress and glottal stop ------------------------------------------------------------------


def _letters(token: str) -> list[tuple[str, set[str]]]:
    out: list[tuple[str, set[str]]] = []
    for ch in unicodedata.normalize("NFD", token):
        if unicodedata.category(ch) == "Mn":
            if out:
                out[-1][1].add(ch)
        else:
            out.append((ch, set()))
    return out


def _consonant_weight(between: str) -> int:
    """Consonants between two vowels; ng is one (/ŋ/), a hyphen is a glottal stop."""
    return len(between.replace("ng", "N"))


def _stress_and_glottals(low: list[str], marks: list[set[str]]) -> tuple[int | None, set[int]]:
    nuclei = [i for i, c in enumerate(low) if c in _VOWELS]
    glottal = {i for i in nuclei if marks[i] & _GLOTTAL_MARKS or (i + 1 < len(low) and low[i + 1] in _VOWELS)}
    if len(nuclei) < 2:
        return None, glottal
    marked = [i for i in nuclei if marks[i] & _STRESS_MARKS]
    if marked:
        return marked[0], glottal
    penult, final = nuclei[-2], nuclei[-1]
    between = "".join(low[penult + 1 : final])
    weight = _consonant_weight(between) + (1 if penult in glottal and between else 0)
    return (penult if weight >= 2 else final), glottal


def handbook_spelling(token: str, lex: SpellingLexicon) -> HandbookSpelling:
    """One word in Wolff's dictionary spelling -> its Handbook spelling."""
    token = token.split("_")[0]  # Wolff's homograph number (mísa_1)
    course = lex.course.get(unicodedata.normalize("NFC", token.lower()))
    if course:
        return HandbookSpelling(course[0].upper() + course[1:] if token[:1].isupper() else course, "course")
    plain = strip_accents(token).lower()
    if plain in WHOLE_WORDS:
        new = WHOLE_WORDS[plain]
        return HandbookSpelling(new.capitalize() if token[:1].isupper() else new, "override")
    letters = _letters(token)
    low = [c.lower() for c, _ in letters]
    marks = [m for _, m in letters]
    base = "".join(low)
    if "e" in base or "o" in base:
        new_base, how = base, "as-written"  # not Wolff's three vowels (a name such as José)
    else:
        new_base, how = choose_vowels(base, lex)
    stress, glottal = _stress_and_glottals(low, marks)
    out = []
    for i, (c, _) in enumerate(letters):
        n = new_base[i].upper() if c.isupper() else new_base[i]
        mark = ""
        if i == stress:
            mark = CIRCUMFLEX if i in glottal else ACUTE
        elif i in glottal:
            mark = GRAVE
        out.append(unicodedata.normalize("NFC", n + mark))
    return HandbookSpelling("".join(out), how)


_WORD = re.compile(r"[^\W\d_][\w\u0300-\u036f\-]*")


def convert_text(text: str, lex: SpellingLexicon) -> str:
    """A Cebuano sentence in Wolff's spelling -> Handbook spelling, word by word.
    A capitalized word after the first is taken for a name and left as written."""
    out, last = [], 0
    nfd = unicodedata.normalize("NFD", text)
    for i, m in enumerate(_WORD.finditer(nfd)):
        word = unicodedata.normalize("NFC", m.group())
        out.append(unicodedata.normalize("NFC", nfd[last : m.start()]))
        sentence_start = i == 0 or re.search(r"[.?!]\s*$", nfd[: m.start()]) is not None
        name = word[:1].isupper() and not sentence_start
        out.append(word if name else handbook_spelling(word, lex).text)
        last = m.end()
    out.append(unicodedata.normalize("NFC", nfd[last:]))
    return "".join(out)
