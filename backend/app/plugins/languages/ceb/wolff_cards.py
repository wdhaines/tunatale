"""Wolff's dictionary on a Cebuano card: the Handbook spelling and three tiers (tunatale-h0q3.2).

The owner chose layout A with layers from the mockup (2026-10-17): the
Handbook spelling under the plain word, front and back, and as much of Wolff
as he gives, tiered like the Norwegian cards. The Cebuano field stays plain,
because it carries card identity and lesson matching.

========================  =========  ==========================================
note field                tier       what it holds
========================  =========  ==========================================
``Spelling``              headword   the Handbook spelling (``màáyo``)
``Meaning``               summary    the senses' glosses on one line
``Verb``                  summary    the affixes a verb takes (``mo-/mi-, ...``)
``Details``               details    numbered senses with an example each, who
                                     the subject is per affix, the root, related
                                     words, homographs, a short form's full form
``Dictionary entry``      deep       the paragraph verbatim, in Wolff's spelling
========================  =========  ==========================================

The data is ``data/wolff_lexicon.json.gz``, one record per dictionary
paragraph, built by ``scripts/build_cebuano_wolff_lexicon.py`` with
:func:`entry_record` (the spellings need the corpus and the loan lists, so
they are converted at build time, not here).

A card finds its entry by its word's letters and picks by its English: the
candidate whose glosses share the most words with the card's English. No
shared word, or a tie between different spellings, is a :class:`Miss`: the
card gets nothing and the word goes on the owner's list. A wrong spelling
under a word is worse than none.

A word Wolff never writes out (kabalo = ka- + baló 'know') can share its
letters with one he does (kabálo 'shame'). A hand row in ``wolff_decoded.tsv``
names the sub-entry with its sense, and is read before the letters.
"""

from __future__ import annotations

import difflib
import gzip
import html
import json
import re
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from functools import cache, cached_property
from pathlib import Path
from typing import NamedTuple

from app.cards.vocab_notetype import DICTIONARY_FIELDS
from app.plugins.languages.ceb import affixes
from app.plugins.languages.ceb.handbook_spelling import SpellingLexicon, convert_text, handbook_spelling
from app.plugins.languages.ceb.wolff_dictionary import (
    CAUSE,
    DIRECT,
    DOER,
    TOGETHER,
    UNDERGOER,
    USED,
    Entry,
    Form,
    decode_classes,
    is_affix_label,
    shifted,
    strip_marks,
)

LEXICON_PATH = Path(__file__).parent / "data" / "wolff_lexicon.json.gz"
RELATIONS_PATH = Path(__file__).parent / "data" / "english_relations.json.gz"
DECODED_PATH = Path(__file__).parent / "data" / "wolff_decoded.tsv"
SENSE_LEAD_PATH = Path(__file__).parent / "data" / "wolff_sense_lead.tsv"
LABELS_PATH = Path(__file__).parent / "data" / "affix_labels.tsv"
CITATION = "Wolff, <i>A Dictionary of Cebuano Visayan</i> (1972), in its own spelling"
FIELD_NAMES = DICTIONARY_FIELDS
_POS_NAMES = {"n": "noun", "v": "verb", "a": "adjective"}
_MEANING_SENSES = 6
#: The Details line of a card whose Spelling :meth:`WolffLexicon.by_rule` made.
RULE_NOTE = "<div>Not listed in Wolff: stressed by rule (a suffix puts the stress on the next-to-last syllable).</div>"
#: No corpus: a ruled spelling takes its vowels from the card word, not from the rules of e and o.
_NO_CORPUS = SpellingLexicon(freq={}, entries={}, loans={})
_RELATED = 12
_STOP = frozenset(
    [
        "a",
        "an",
        "the",
        "of",
        "to",
        "be",
        "is",
        "are",
        "am",
        "in",
        "on",
        "at",
        "for",
        "with",
        "by",
        "as",
        "or",
        "and",
        "s.t",
        "s.o",
        "k.o",
        "s.w",
    ]
)


# --- building a record ------------------------------------------------------------------------


def _form_record(form: Form, lex: SpellingLexicon) -> dict:
    senses = []
    for s in form.senses:
        ex = s.examples[0] if s.examples else None
        senses.append(
            {
                "pos": s.pos,
                "n": s.number,
                "cls": s.classes,
                "gloss": s.gloss,
                "phrase": convert_text(s.phrase, lex) if s.phrase else None,
                "ex": [convert_text(ex.ceb, lex), ex.en] if ex else None,
            }
        )
    return {
        "label": form.label,
        "wolff": form.form,
        "hb": handbook_spelling(form.form, lex).text if form.form else None,
        "short": [[w, handbook_spelling(w, lex).text] for w in form.short_forms],
        "xref": list(form.xref) if form.xref else None,
        "senses": senses,
    }


def entry_record(entry: Entry, lex: SpellingLexicon) -> dict:
    """One dictionary paragraph as the lexicon stores it: forms[0] is the root."""
    return {
        "id": entry.id,
        "heads": entry.heads,
        "forms": [_form_record(f, lex) for f in [entry.root, *entry.subs]],
        "text": entry.text,
    }


_TOKEN = re.compile(r"[^\W\d_]+(?:-[^\W\d_]+)*")


def add_attested(entries: Sequence[Entry], records: Sequence[dict], lex: SpellingLexicon) -> None:
    """Record, under each entry, the accented words its examples print that no entry lists.

    Wolff gives a regular derived word only as an affix label, or not at all
    (mainit = ma- + ínit), but prints it in full in his examples (maínit). That
    printed spelling is the attestation a card can take (tunatale-h0q3.21).
    Stored as ``[handbook spelling, count, example in Handbook spelling, its English]``.
    """
    listed = set()
    for rec in records:
        listed |= {letters_key(h) for h in rec["heads"]}
        for f in rec["forms"]:
            listed |= {letters_key(f["wolff"])} if f["wolff"] else set()
            listed |= {letters_key(w) for w, _ in f["short"]}
    for entry, rec in zip(entries, records, strict=True):
        found: dict[str, list] = {}
        for form in [entry.root, *entry.subs]:
            for ex in (ex for sense in form.senses for ex in sense.examples):
                for tok in _TOKEN.findall(ex.ceb):
                    if tok == strip_marks(tok) or letters_key(tok) in listed:
                        continue
                    hb = handbook_spelling(tok.lower(), lex).text
                    if hb in found:
                        found[hb][0] += 1
                    else:
                        found[hb] = [1, convert_text(ex.ceb, lex), ex.en]
        if found:
            rec["attested"] = [[hb, *rest] for hb, rest in found.items()]


# --- reading it -------------------------------------------------------------------------------


def letters_key(word: str) -> str:
    """What a card's word and the dictionary's share: no marks or hyphen, e/o as i/u."""
    w = strip_marks(word).lower().replace("-", "")
    w = w.replace("e", "i").replace("o", "u")
    # Wolff writes one consonant + y/w as Cy, Cw (§3.24: syudad); cards write siyudad
    return re.sub(r"(?<=[^aiu])(?:iy|uw)(?=[aiu])", lambda m: m.group()[1], w)


_EO = str.maketrans("eo", "iu")
_MARKS = frozenset("\u0300\u0301\u0302")  # grave (glottal), acute (stress), circumflex (both)


def onto_card(card: str, surface: str) -> str:
    """The card word's own letters, with *surface*'s stress, glottal and hyphen marks.

    The card word is the course spelling, which wins over Wolff's letters
    (tunatale-h0q3.21: Martes, not Mártis). Letters align with e = i and o = u;
    a hyphen in *surface* the card lacks is a glottal stop and is carried over.
    When the words are too different, or a mark would be lost, *surface* stands.
    """
    chars = [unicodedata.normalize("NFD", c) for c in unicodedata.normalize("NFC", surface)]
    bare = "".join(c[0] for c in chars)
    a, b = card.lower().translate(_EO), bare.lower().translate(_EO)
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    if matcher.ratio() < 0.75:
        return surface
    out = []
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            out += [card[i] + chars[j][1:] for i, j in zip(range(i1, i2), range(j1, j2), strict=True)]
        elif op == "insert":
            out += ["-"] if bare[j1:j2] == "-" else []
        else:  # the card's own letters where the two differ
            out += list(card[i1:i2])
    result = unicodedata.normalize("NFC", "".join(out))
    marks = lambda w: sorted(m for m in unicodedata.normalize("NFD", w) if m in _MARKS)  # noqa: E731
    return result if marks(result) == marks(surface) else surface


#: How many pointers in a row :meth:`WolffLexicon._resolve` follows.
_POINTER_HOPS = 2
#: A gloss that is only Wolff's case label ('gen.', 'gen. form', 'preposed gen.',
#: 'gen.: = nákù'), which :meth:`WolffLexicon._senses` reads as the pronoun's
#: possessive. Group 1 is the label, group 2 the word it points at.
_CASE_LABEL = re.compile(r"((?:preposed )?gen\.(?: form)?)(?::\s*=\s*(\S+?))?\.?")
#: An English personal pronoun's possessive and the agent Wolff pairs with it.
_PRONOUN_CASES = {
    "i": ("my", "me"),
    "me": ("my", "me"),
    "we": ("our", "us"),
    "us": ("our", "us"),
    "you": ("your", "you"),
    "he": ("his", "him"),
    "him": ("his", "him"),
    "she": ("her", "her"),
    "they": ("their", "them"),
    "them": ("their", "them"),
}


def _is_affix(word: str) -> bool:
    return word.startswith("-") or word.endswith("-")


def _words(text: str) -> set[str]:
    return {w[:5] for w in re.findall(r"[a-z]+", strip_marks(text).lower()) if w not in _STOP}


# Words that relate to too much to count on their own: WordNet lists "kind"
# with "like", and Wolff's búut is "want, would like to". A phrase holding one
# ("get out") still relates.
LIGHT_WORDS = frozenset(
    [
        "like",
        "get",
        "give",
        "make",
        "do",
        "have",
        "go",
        "take",
        "put",
        "come",
        "set",
        "let",
        "way",
        "thing",
        "one",
        "out",
        "up",
        "off",
        "down",
        "over",
        "s",
        "t",
        "o",
        "k",
    ]
)


def english_tokens(text: str) -> list[str]:
    """The words of an English gloss, lower-cased, stop words out, ``O.K.`` as ``ok``."""
    text = re.sub(r"\b([a-z])\.([a-z])\.", r"\1\2", strip_marks(text).lower())
    return [w for w in re.findall(r"[a-z]+", text) if w not in _STOP]


def _phrases(text: str) -> set[str]:
    return {" ".join(t) for part in re.split(r"[;,/()]", text) if (t := english_tokens(part))}


@dataclass(frozen=True)
class EnglishRelations:
    """English word relations from WordNet (``scripts/build_english_relations.py``).

    ``base``: a word's dictionary form where it differs. ``related``: per word or
    two-word phrase of Wolff's glosses, its synonyms and the words in a kind-of
    relation with it. ``defined_by``: per common English word, the words of its
    WordNet definition that occur in Wolff's glosses.
    """

    base: dict[str, str]
    related: dict[str, frozenset[str]]
    defined_by: dict[str, frozenset[str]]

    @classmethod
    def empty(cls) -> EnglishRelations:
        return cls({}, {}, {})

    @classmethod
    def load(cls, path: Path = RELATIONS_PATH) -> EnglishRelations:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            raw = json.load(fh)
        return cls(
            raw["base"],
            {k: frozenset(v) for k, v in raw["related"].items()},
            {k: frozenset(v) for k, v in raw["defined_by"].items()},
        )

    def score(self, english: str, card: set[str], gloss: str) -> tuple[int, int, int]:
        """(card words in the gloss, card words related to it, card words whose definition it holds)."""
        gloss_words = _words(gloss)
        tokens = english_tokens(gloss)
        units = {t for t in tokens if t not in LIGHT_WORDS} | {
            f"{a} {b}" for a, b in zip(tokens, tokens[1:], strict=False)
        }
        bases = {self.base.get(t, t) for t in tokens if t not in LIGHT_WORDS}
        related = defined = 0
        for word in set(english_tokens(english)):
            if word[:5] in gloss_words:
                continue
            forms = {word, self.base.get(word, word)}
            if forms & bases or any(forms & self.related.get(u, frozenset()) for u in units):
                related += 1
            elif (self.defined_by.get(word, frozenset()) - LIGHT_WORDS) & bases:
                defined += 1
        return len(card & gloss_words), related, defined


class Miss(NamedTuple):
    reason: str  # not-in-dictionary | english-unmatched | ambiguous
    candidates: tuple[str, ...]  # the candidates' Handbook spellings


# ── a word glossed by its parts (tunatale-h0q3.26) ──────────────────────────────────────────
# The owner's wording (affix_labels.tsv, approved 2026-10-28): "form label + root", one short
# label per affix chosen by what Wolff's class code makes the subject, and the root's own
# meaning first, then the verb sense that licensed the affix. Silent rather than guessing.

# Irregular English past forms whose WordNet base is missing (morphy picks a noun/name
# first; measured 2026-11-25). tunatale-h0q3.37
_IRREGULAR_PAST: dict[str, str] = {
    "ate": "eat",
    "bent": "bend",
    "bit": "bite",
    "bore": "bear",
    "bound": "bind",
    "chosen": "choose",
    "drew": "draw",
    "drove": "drive",
    "drunk": "drink",
    "dug": "dig",
    "fed": "feed",
    "fell": "fall",
    "felt": "feel",
    "found": "find",
    "ground": "grind",
    "lay": "lie",
    "leant": "lean",
    "led": "lead",
    "left": "leave",
    "lent": "lend",
    "lit": "light",
    "lost": "lose",
    "rose": "rise",
    "rung": "ring",
    "sang": "sing",
    "sat": "sit",
    "saw": "see",
    "sent": "send",
    "shook": "shake",
    "shot": "shoot",
    "spat": "spit",
    "spoke": "speak",
    "stole": "steal",
    "stunk": "stink",
    "sung": "sing",
    "swum": "swim",
    "thought": "think",
    "tore": "tear",
    "won": "win",
    "wound": "wind",
}


def _verb_base(english: str) -> str:
    """*english* with each irregular past replaced by its verb ('ate' -> 'eat'); unchanged when it has none."""
    return re.sub(r"[A-Za-z]+", lambda m: _IRREGULAR_PAST.get(m.group(0).lower(), m.group(0)), english)


class Part(NamedTuple):
    text: str  # the affix as written (mo-, gi-…-an), or the root in Handbook spelling
    gloss: str  # the affix's label, or the root's meaning
    affix: bool
    cite: str = ""  # an affix's Wolff sense, for the card's Details ('mu- 1, 2')


def _label_rows(path: Path = LABELS_PATH) -> list[list[str]]:
    return [line.split("\t") for line in path.read_text().splitlines() if line and not line.startswith("#")]


@cache
def _labels() -> dict[tuple[str, str], str]:
    """(decoder piece, root kind) -> the approved label."""
    return {(piece, kind): label for piece, kind, label, _ in _label_rows()}


@cache
def _cites() -> dict[tuple[str, str], str]:
    """(decoder piece, label) -> the Wolff sense(s) it stands for ('mu- 3 or mu- 3a')."""
    out: dict[tuple[str, str], list[str]] = {}
    for piece, _, label, wolff in _label_rows():
        out.setdefault((piece, label), [])
        if wolff not in out[(piece, label)]:
            out[(piece, label)].append(wolff)
    return {key: " or ".join(cites) for key, cites in out.items()}


# The chart pair a decoder piece belongs to, as decode_classes names it.
_PIECE_PAIRS: dict[str, tuple[str, ...]] = {
    **dict.fromkeys(("mu-", "mi-", "ni-"), ("mo-/mi-",)),
    **dict.fromkeys(("mag-", "nag-", "ga-"), ("mag-/nag-",)),
    **dict.fromkeys(("maka-", "naka-", "ka-"), ("maka-/naka-",)),
    **dict.fromkeys(("maN-", "naN-"), ("maN-/naN-",)),
    **dict.fromkeys(("-un", "-a"), ("-on/gi-",)),
    "gi-": ("-on/gi-", "i-/gi-"),
    **dict.fromkeys(("-an", "gi-an", "-i"), ("-an/gi-an",)),
    "i-": ("i-/gi-",),
    **dict.fromkeys(("ma-", "na-"), ("ma-/na-",)),
    **dict.fromkeys(("magka-", "nagka-"), ("magka-/nagka-",)),
}
# The decoder's spelling -> the Handbook's (Wolff writes u where the Handbook writes o).
_PIECE_TEXT = {"mu-": "mo-", "-un": "-on", "gi-an": "gi-…-an"}
# The pieces that only a verb carries. Wolff's derivational affixes make nouns with the same
# letters as ka- (maka-'s short form), -an and -on, and a bare pa-: kalinaw 'peace', tunghaan
# 'school', baybayon 'beach', paagi 'way'. Measured 2026-10-28 on the owner's 815 one-word
# cards: read as a verb's inflection, 30 of the 36 that got a line were wrong.
_VERBAL = frozenset(
    ("mu-", "mi-", "ni-", "mag-", "nag-", "ga-", "maka-", "naka-", "maN-", "naN-", "gi-", "gi-an", "i-", "ma-", "na-",
     "magka-", "nagka-", "-a", "-i")
)  # fmt: skip
_ROOT_FORMS = ("(←)", "(→)")  # a root's stress forms: other words with its letters

# ── -an 'having' on a noun root (tunatale-h0q3.53) ──────────────────────────────────────────
# Wolff's -an 4, sense 2: "added to nouns to form adjectives which mean 'having [so-and-so]'.
# Utúkan, Brainy. Kwartáhan, Moneyed." The decoder alone never triggers it (that was the
# 30-of-36 class above): the word must be Wolff's own sub-entry of the root. Measured
# 2026-11-25: 25 words in all of Wolff, 1 of the owner's 804 vocab cards (kúsgan).
_HAVING_KIND = "noun, made an adjective"  # affix_labels.tsv's root kind for this row
_HAVING_AFFIXES = ("-an", "-an(←)", "-an(→)")
# Wolff lists these as root + -an adjectives on a noun root, but 'having' reads them wrong:
# adláwan 'for it to be daylight' (ádlaw 'sun'), kulángan 'lacking a lot' (kúlang
# 'balance'). Skipped by name (owner, 2026-11-25).
_NOT_HAVING = frozenset({"adlawan", "kulangan"})


def _final_stress(spelling: str) -> bool:
    """Whether *spelling* is stressed on its last syllable: an acute or circumflex on its last
    vowel. That is Wolff's -an 4, sense 1, 'characterized by being' (talawan, gahian)."""
    vowels = [marks for ch, marks in _marked_letters(spelling) if ch in "aiu"]
    return bool(vowels) and bool(vowels[-1] & {"́", "̂"})


def _licenses(cls: str) -> set[tuple[str, str]]:
    """The (chart pair, subject) a sense's class code licenses, and the potential ma-/na- that
    every direct passive has (Wolff, -un_1: 'Potential forms: past: na-; future ... ma-').
    Wolff's general i- is licensed too, but affix_labels.tsv gives it no label."""
    out = set()
    for part in cls.split(";"):
        out |= set(decode_classes(part.strip()))
    if ("-on/gi-", DIRECT) in out:
        out.add(("ma-/na-", DIRECT))
    return out


_PLACEHOLDERS = {"s.t": "something", "s.o": "someone", "s.w": "somewhere", "k.o": "a kind of"}


def _is_idiom(sentence: str) -> bool:
    # Wolff's idiom: a Cebuano phrase with "—" standing for the head ("walay — illiterate"),
    # or a form suffix ("-ng tabúnun the Philippines"). A dash inside a word is no idiom.
    return sentence.startswith("-") or re.search(r"(?:^|\s)—(?=[\s,]|$)", sentence) is not None


def sense_before_idiom(gloss: str) -> str:
    """*gloss* up to an idiom Wolff runs on after the sense ('salt. walay — without substance'
    -> 'salt'; tunatale-h0q3.77: such idioms stay in Details, not Meaning). A gloss that IS an
    idiom ('sa — in former times') is its own sense and stays whole."""
    sentences = re.split(r"(?<=\.)\s+", gloss)
    cut = next((i for i, sentence in enumerate(sentences) if _is_idiom(sentence)), 0)
    if not cut:
        return gloss
    sense = " ".join(sentences[:cut])
    # the sentence's stop goes; an abbreviation's ('s.t.') stays
    return sense if re.search(r"\b[a-z]\.[a-z]\.$", sense) else sense.removesuffix(".")


def _plain(gloss: str) -> str:
    """Wolff's gloss with his placeholders said in words mid-phrase ('together with s.o. else'
    -> 'someone else') and dropped at the end of one ('write s.t.' -> 'write'), and no
    closing stop."""
    text = re.sub(r"\b(s\.t|s\.o|s\.w|k\.o)\.?(?= [a-z])", lambda m: _PLACEHOLDERS[m.group(1)], gloss)
    text = re.sub(r"\s+(?:s\.t|s\.o|k\.o|s\.w)\.?(?=[\s,;)]|$)", "", text)
    return re.sub(r"\s+", " ", text).strip().rstrip(".;, ")


def _marked_letters(text: str) -> list[tuple[str, frozenset[str]]]:
    """The letters of *text*, e/o folded to i/u, each with its accent marks (Wolff §3.2, §5.11)."""
    out: list[tuple[str, frozenset[str]]] = []
    for ch in unicodedata.normalize("NFD", text.lower()):
        if unicodedata.combining(ch):
            if out and ch in _MARKS:
                out[-1] = (out[-1][0], out[-1][1] | {ch})
            continue
        if ch.isalpha():
            out.append((ch.translate(_EO), frozenset()))
    return out


def _card_keeps_i(card: str, head: str) -> bool:
    """Whether *card* is *head* with an i where the Handbook chose a loan's e.

    Wolff writes i; the Handbook writes e only for a Spanish or English loan (§3.2), so a
    card word that writes i says the word is native (ibílin's root is bílin, not bélen). The
    u/o choice is Wolff's own rule and no evidence either way, so it is not compared.
    """
    a, b = strip_marks(card), strip_marks(head)
    return len(a) == len(b) and all(x == y or (y == "e" and x == "i") for x, y in zip(a, b, strict=True))


def _card_letters(word: str, root: str, affix: str) -> str | None:
    """The letters of *word* that spell *root*, marks and all, or None when they don't line up.

    ``affixes.letter_map`` says which of the word's letters belong to the root (the rest are
    the affix's, or a nasal that stands in for the root's first consonant).
    """
    tags = affixes.letter_map(affixes.wolff_letters(word), root, affix)
    if tags is None:
        return None
    chars: list[str] = []
    marks: list[list[str]] = []
    for ch in unicodedata.normalize("NFD", word.lower()):
        if unicodedata.combining(ch):
            if marks:
                marks[-1].append(ch)
        else:
            chars.append(ch)
            marks.append([])
    # tags has one entry per letter of strip_marks(word), so it lines up with the bases here
    out = [c + "".join(m) for c, m, tag in zip(chars, marks, tags, strict=True) if isinstance(tag, int)]
    return unicodedata.normalize("NFC", "".join(out)) if out else None


def _names(word: str, surface: str) -> bool:
    """Whether *word* carries *surface*'s letters and marks: 'nakakitâ' names the stress form
    'kitâ', not the root 'kítà' (§5.11). Letters alone fold e/o, so a card's spelling still
    meets Wolff's."""
    want = _marked_letters(surface)
    have = _marked_letters(word)
    return bool(want) and any(have[i : i + len(want)] == want for i in range(len(have) - len(want) + 1))


def _repeats_front(spelling: str, front: str) -> bool:
    """Whether *spelling* is the front's own forms letter for letter ('to' on 'to', 'oo' on 'oo, salamat')."""
    mine = [w.strip().casefold() for w in spelling.split(",")]
    theirs = [w.strip().casefold() for w in front.split(",")]
    return mine == theirs[: len(mine)]


def _built(affix: str, root: str, *, bold: bool = False) -> str:
    """'ka- + baló', but 'walâ + -y': a suffix follows its root. Escaped; *bold* wraps each part."""
    a, r = (f"<b>{html.escape(x)}</b>" if bold else html.escape(x) for x in (affix, root))
    return f"{r} + {a}" if affix.startswith("-") else f"{a} + {r}"


@dataclass(frozen=True)
class CardFields:
    spelling: str
    meaning: str
    verb: str
    details: str
    dictionary_entry: str

    def as_note_fields(self) -> dict[str, str]:
        """By Anki note field name (:data:`FIELD_NAMES`)."""
        return dict(
            zip(FIELD_NAMES, (self.spelling, self.meaning, self.verb, self.details, self.dictionary_entry), strict=True)
        )


class Decoded(NamedTuple):
    """A card word Wolff never writes out, and the sub-entry with its sense (``wolff_decoded.tsv``)."""

    word: str
    spelling: str  # in Handbook spelling
    head: str  # Wolff's head, as the lexicon stores it
    label: str  # the sub-entry's label ('hi-/ha-')
    affix: str  # what the word adds to the root
    note: str  # a sentence for the card, or ""
    reading: str = ""  # the card word's own meaning, which the Meaning field leads with


def load_sense_lead(path: Path = SENSE_LEAD_PATH) -> dict[str, tuple[tuple[str | None, str], ...]]:
    """Card word -> the ``(pos, n)`` senses that lead its Meaning (``wolff_sense_lead.tsv``).
    A ``-`` part of speech is a sense Wolff prints none for (a stress form's '(→) {3a} [A2; b6]')."""
    lead: dict[str, tuple[tuple[str | None, str], ...]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            word, pos, n = line.split("\t")
            lead[word] = (*lead.get(word, ()), (None if pos == "-" else pos, n))
    return lead


def load_decoded(path: Path = DECODED_PATH) -> list[Decoded]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [Decoded(*line.split("\t")) for line in lines if line and not line.startswith("#")]


# --- the verb, one row per affix (tunatale-h0q3.31) ---------------------------------------------
#
# The owner approved the wording on 2026-10-27 (https://claude.ai/artifact/HiSVj2PmJoiiGYgkaezEgG):
# the kind of verb in words, then one row per affix named by its row in the owner's affix chart
# (trigger · aspect, the terms settled 10-24/25: agent / patient / circumstance / instrument,
# perfective / progressive / distributive, potential, and stative as its own band), with its forms
# on this word as "not begun" / "begun", what it means here, and who the subject is.

_KINDS = {
    "A": "Action verb: someone does it.",
    "B": "Verb of state: it happens to someone; nobody does it.",
    "C": "Mutual action: done together.",
}
_AGENT_ASPECT = {
    "mo-/mi-": "perfective",
    "mag-/nag-": "progressive",
    "maka-/naka-": "potential",
    "maN-/naN-": "distributive",
}
_PASSIVE = {
    "-on/gi-": "Patient",
    "-an/gi-an": "Circumstance",
    "i-/gi-": "Instrument",
    "ma-/na-": "Patient · potential",
    "ma-an/na-an": "Circumstance · potential",
    "hi-an": "Circumstance · by accident",
    "ika-/gika-": "Instrument · potential",
}
_STATIVE_SUBJECTS = frozenset({UNDERGOER, CAUSE, f"{UNDERGOER}, or {CAUSE}"})
# What each affix means on this kind of verb. Agent: Wolff §6.1. Stative: §7.12 and the affix
# entries (mu- {3} 'come into [such-and-such] a state', mag- {3} 'be in [such-and-such] a
# state', magka- 'become more and more [so-and-so]'); maka-'s turns on its subject (§7.121).
_ROLE = {
    (DOER, "mo-/mi-"): "do it",
    (DOER, "mag-/nag-"): "be doing it",
    (DOER, "maka-/naka-"): "can, manage to, or happen to do it",
    (DOER, "maN-/naN-"): "do it to many, or again and again",
    (UNDERGOER, "mo-/mi-"): "come into the state, become so",
    (UNDERGOER, "mag-/nag-"): "be in the state",
    (UNDERGOER, "ma-/na-"): "get to be so",
    (UNDERGOER, "maka-/naka-"): "come to be so",
    (CAUSE, "maka-/naka-"): "cause to become so",
    (f"{UNDERGOER}, or {CAUSE}", "maka-/naka-"): "come to be so, or cause it to be",
    (UNDERGOER, "magka-/nagka-"): "become more and more so",
    (UNDERGOER, "maN-/naN-"): "come into the state, become so",
    (TOGETHER, "mag-/nag-"): "do it together",
    (TOGETHER, "magka-/nagka-"): "manage to do it together",
    (TOGETHER, "makig-/nakig-"): "do it with someone",
}
# Each affix pair as the prefixes that build its forms on the word: (not begun, begun). None:
# a suffix moves the stress unpredictably, so that form is shown only as Wolff prints it.
_PREFIXES: dict[str, tuple[str | None, str | None]] = {
    "mo-/mi-": ("mo", "mi"),
    "mag-/nag-": ("mag", "nag"),
    "maka-/naka-": ("maka", "naka"),
    "magka-/nagka-": ("magka", "nagka"),
    "makig-/nakig-": ("makig", "nakig"),
    "ma-/na-": ("ma", "na"),
    "-on/gi-": (None, "gi"),
    "i-/gi-": ("i", "gi"),
    "ika-/gika-": ("ika", "gika"),
}
# A suffixed form the decoder finds in Wolff's printing: (its row, 0 not begun / 1 begun).
_PRINTED_SLOT = {
    "-un": ("-on/gi-", 0),
    "-an": ("-an/gi-an", 0),
    "gi-an": ("-an/gi-an", 1),
    "ma-an": ("ma-an/na-an", 0),
    "na-an": ("ma-an/na-an", 1),
    "hi-an": ("hi-an", 0),
}
#: A short prefix and the pair it shortens (Wolff §6.1: ka- for maka-/naka-).
_SHORT_OF = {"ka-": "maka-/naka-"}


def verb_kinds(codes: Sequence[str]) -> list[str]:
    """The kinds of verb Wolff's class codes name, by their capital letters (§7.11-7.13)."""
    letters = {ch for code in codes for ch in re.findall(r"[ABC]", code)}
    return [_KINDS[ch] for ch in sorted(letters)]


def _row_label(affix: str, subject: str) -> str:
    if subject == DOER:
        return f"Agent · {_AGENT_ASPECT[affix]} · {affix}"
    if subject in _STATIVE_SUBJECTS:
        return f"Stative · {affix}"
    if subject == TOGETHER:
        return f"Mutual · {affix}"
    return f"{_PASSIVE[affix]} · {affix}"


def _prefixed(prefix: str, word: str) -> str:
    """A prefix on a word: a hyphen after a consonant before a vowel (mag-ampo), none otherwise."""
    hyphen = "-" if prefix[-1] not in "aeiou" and strip_marks(word[:1]).lower() in "aeiou" else ""
    return f"{prefix}{hyphen}{word}"


def _stopped(sentence: str) -> str:
    """A Cebuano example as a sentence. Wolff writes 'Cebuano, English.': the parser drops
    the comma between them, which leaves the Cebuano with no stop of its own."""
    return sentence if sentence.endswith((".", "?", "!", "…")) else f"{sentence}."


class _Row(NamedTuple):
    affix: str
    subject: str
    not_begun: list[str]
    begun: list[str]
    short: list[str]


class Attested(NamedTuple):
    """A word no entry lists, spelled as Wolff prints it in an example of *entry*."""

    entry: int
    surface: str
    example: str  # in Handbook spelling
    translation: str
    via: None = None


# A phrase that opens with a word before the root's dash ('{2a} sa — [gen.] nga …') is not
# read as one (wolff_dictionary.parse_entry knows only a dash that opens a sense), and Wolff's
# example then runs on into its gloss ('… about to [do]. (Sa) dáyun ku …, As I was …'). Such a
# sense is a phrase, which a card's Meaning leaves out. Only the run-in example marks it: una's
# 'sa — in former times' is an idiom glossed in its own right and stays. Measured 2026-11-25:
# one sense in all of Wolff, dáyun 2a (the owner's dayon and gilayon cards, tunatale-tofg.6).
_MISREAD_PHRASE = re.compile(r"[^\W\d_]+(?: [^\W\d_]+)? — .*?[.?!] \(?[A-ZÁÉÍÓÚ][^,]*, [A-Z]")


def _marked(word: str) -> str:
    """*word* as written, marks and all: one stress form (dayón), not its letters (dáyon too)."""
    return unicodedata.normalize("NFC", word.lower())


def _lead_key(word: str) -> str:
    """A sense-lead row's key: a word written with Wolff's marks names that stress form alone
    (dayón 'come in', not dáyon 'immediately'); one written without reaches every form with its
    letters (bay)."""
    return _marked(word) if _marked(word) != strip_marks(word).lower() else letters_key(word)


def _meanings(form: dict) -> list[dict]:
    """*form*'s senses without the two kinds that are not meanings: the affix a
    run of senses belongs to ('*lihug pa- {1} please'), and a pointer to another
    affix's form ('n {1} = -in-')."""
    return [s for s in form["senses"] if not is_affix_label(s["gloss"].removeprefix("= "))]


def _in_order(short: str, full: str) -> bool:
    """Every letter of *short* appears in *full*, in order ('ta' in 'kita', 'tu' in 'kadtu')."""
    rest = iter(full)
    return all(c in rest for c in short)


class _Hit(NamedTuple):
    entry: int
    form: int
    surface: str  # the card word's own Handbook spelling (a short form's, if it came through one)
    via: tuple[int, int] | None  # the pointer it came through ('náa = anáa', a short form)
    english_matched: bool = True  # False: the only candidate, taken on its letters
    decoded: Decoded | None = None  # came through a hand row, not the letters


class WolffLexicon:
    def __init__(
        self,
        entries: Sequence[dict],
        relations: EnglishRelations | None = None,
        decoded: Sequence[Decoded] = (),
        sense_lead: Mapping[str, Sequence[tuple[str, str]]] | None = None,
    ):
        self.entries = list(entries)
        self.relations = relations if relations is not None else EnglishRelations.empty()
        #: Every head spelling, marks and all: a stress form spelt like another word's head is
        #: that other word, not a variant (sákit 'torture' is also sákit 'socket').
        self._spellings = frozenset(h.lower() for rec in self.entries for h in rec["heads"])
        self._decoded_rows = tuple(decoded)
        self._decoded = self._index_decoded(self._decoded_rows)
        self._attested: dict[str, list[tuple[int, str, int, str, str]]] = {}
        for ei, rec in enumerate(self.entries):
            for hb, n, example, translation in rec.get("attested", ()):
                self._attested.setdefault(letters_key(hb), []).append((ei, hb, n, example, translation))
        self._index: dict[str, list[tuple[int, int, str | None]]] = {}
        self._heads: dict[str, list[int]] = {}
        for ei, rec in enumerate(self.entries):
            for h in rec["heads"]:
                self._heads.setdefault(strip_marks(h).lower(), []).append(ei)
            for fi, f in enumerate(rec["forms"]):
                if f["wolff"]:
                    self._index.setdefault(letters_key(f["wolff"]), []).append((ei, fi, None))
                for wolff, hb in f["short"]:
                    self._index.setdefault(letters_key(wolff), []).append((ei, fi, hb))
        self._sense_lead = self._index_sense_lead(sense_lead or {})

    def _index_sense_lead(
        self, lead: Mapping[str, Sequence[tuple[str, str]]]
    ) -> dict[str, tuple[tuple[str, str], ...]]:
        """The lead table by :func:`_lead_key`; a row naming no sense of the forms it reaches raises."""
        for word, keys in lead.items():
            key = _lead_key(word)
            reached = [h for h in self._hits(word) if key in (letters_key(word), _marked(h.surface))]
            have = {(s["pos"], str(s["n"])) for h in reached for s in self._senses(h)}
            for pos, n in keys:
                if (pos, n) not in have:
                    raise ValueError(f"wolff_sense_lead: {word} {pos} {n} names no sense of the entries it reaches")
        return {_lead_key(word): tuple(keys) for word, keys in lead.items()}

    def _index_decoded(self, decoded: Sequence[Decoded]) -> dict[str, _Hit]:
        """The hand rows by the card word's letters; a row naming no single sub-entry raises."""
        table: dict[str, _Hit] = {}
        for row in decoded:
            found = [
                (ei, fi)
                for ei, rec in enumerate(self.entries)
                if row.head in rec["heads"]
                for fi, f in enumerate(rec["forms"])
                if f["label"] == row.label
            ]
            if len(found) != 1:
                raise ValueError(f"wolff_decoded: {row.head} {row.label} names {len(found)} sub-entries, not one")
            table[letters_key(row.word)] = _Hit(*found[0], row.spelling, None, decoded=row)
        return table

    def variant(
        self,
        *,
        relations: EnglishRelations | None = None,
        decoded: Sequence[Decoded] | None = None,
        sense_lead: Mapping[str, Sequence[tuple[str, str]]] | None = None,
    ) -> WolffLexicon:
        """This lexicon's entries with other *relations* or hand rows (*decoded*); None keeps this one's.

        Shares everything the entries alone decide (the heads and form indexes, the
        affix decoder), which a fresh ``WolffLexicon(self.entries, ...)`` would rebuild:
        about 0.4 s for the committed lexicon, most of it the decoder.
        """
        new = object.__new__(WolffLexicon)
        new.entries = self.entries
        new.relations = self.relations if relations is None else relations
        new._spellings = self._spellings
        new._attested = self._attested
        new._index = self._index
        new._heads = self._heads
        new._decoded_rows = self._decoded_rows if decoded is None else tuple(decoded)
        new._decoded = self._decoded if decoded is None else new._index_decoded(new._decoded_rows)
        new.__dict__["_decoder"] = self._decoder  # the cached_property's slot: built once, shared
        new._sense_lead = self._sense_lead if sense_lead is None else new._index_sense_lead(sense_lead)
        return new

    def _resolve(self, ei: int, fi: int, word_key: str, depth: int = 0) -> tuple[int, int] | None:
        """Follow a form's '= x' / 'see x' / 'short for x' to the form it points at."""
        xref = self.entries[ei]["forms"][fi]["xref"]
        # A short form means what its full form means, even when it has usage
        # notes of its own ('ka short for ikaw'); other pointers with senses stand.
        if not xref or (self.entries[ei]["forms"][fi]["senses"] and xref[0] != "short for"):
            return None
        # '*líhug': the star marks a root never said bare, and is no part of its head
        target = strip_marks(re.split(r"[ ,]", xref[1])[0]).lower().lstrip("*").split("_")[0]
        for tei in sorted(self._heads.get(target, []), key=lambda t: t != ei):
            forms = self.entries[tei]["forms"]
            inside = [i for i, f in enumerate(forms) if f["wolff"] and letters_key(f["wolff"]) == word_key]
            tfi = inside[0] if xref[0] == "see" and inside else 0
            # A pointer to a bare pointer goes one step further: 'mu short for
            # nímu' lands on 'nímu see ikaw', and the senses are in ikaw's nímu.
            if (
                not forms[tfi]["senses"]
                and forms[tfi]["xref"]
                and depth < _POINTER_HOPS
                and (further := self._resolve(tei, tfi, letters_key(target), depth + 1)) is not None
            ):
                return further
            # A short form with notes of its own keeps them when the full form has
            # none to give ('mu short for nímu', where nímu is itself a pointer).
            if self.entries[ei]["forms"][fi]["senses"] and not forms[tfi]["senses"]:
                return None
            return tei, tfi
        return None

    def _hits(self, word: str) -> list[_Hit]:
        key = letters_key(word)
        hits: dict[tuple[int, int], _Hit] = {}
        for ei, fi, short_hb in self._index.get(key, []):
            surface = short_hb or self.entries[ei]["forms"][fi]["hb"]
            if _is_affix(surface) and not _is_affix(word):
                continue  # the prefix ka- is not the word ka (letters_key drops hyphens)
            via = None
            target = self._resolve(ei, fi, key)
            if target is not None:
                via, (ei, fi) = (ei, fi), target
            elif short_hb is not None:
                via = (ei, fi)
            old = hits.get((ei, fi))
            # one form reached two ways (náa = anáa, and anáa's short form náa): keep the
            # pointer paragraph, so the card shows both
            if old is None or (via is not None and via[0] != ei and (old.via is None or old.via[0] == ei)):
                hits[(ei, fi)] = _Hit(ei, fi, surface, via)
        return list(hits.values())

    def surfaces(self, word: str) -> list[str]:
        """Every Handbook spelling, marks and all, that *word* could be in Wolff."""
        return [h.surface for h in self._hits(word)]

    def _senses(self, hit: _Hit) -> list[dict]:
        senses = [
            s
            for s in _meanings(self.entries[hit.entry]["forms"][hit.form])
            if not s["phrase"] and not _MISREAD_PHRASE.match(s["gloss"])
        ]
        genitive = self._genitive(hit.entry)
        if genitive is None:
            return senses
        out = []
        for s in senses:
            m = _CASE_LABEL.fullmatch(s["gloss"])
            if m:
                pointer = f" (= {m.group(2)})" if m.group(2) else ""
                s = {**s, "gloss": f"{m.group(1)}: {genitive}{pointer}"}
            out.append(s)
        return out

    def _genitive(self, ei: int) -> str | None:
        """'our, by us' for an entry whose root is a personal pronoun ('we, us'), else None.

        Wolff glosses a possessive with its case label alone ('átù {1} gen.'),
        and his genitive is the possessive and the agent: nákù "gen.: my, by me".
        """
        root = self.entries[ei]["forms"][0]
        first = next((s["gloss"] for s in root["senses"] if s["gloss"]), "")
        cases = []
        for word in re.findall(r"[a-z]+", first.lower()):
            if word not in _PRONOUN_CASES:
                break
            cases.append(_PRONOUN_CASES[word])
        if not cases:
            return None
        owners = list(dict.fromkeys(o for o, _ in cases))
        agents = list(dict.fromkeys(a for _, a in cases))
        return f"{', '.join(owners)}, by {' or '.join(agents)}"

    def _printed(self, word: str) -> Attested | Miss:
        """The spelling Wolff prints, when at least nine in ten of his printings agree."""
        found = self._attested[letters_key(word)]
        counts = Counter()
        for _, hb, n, _, _ in found:
            counts[hb] += n
        hb, top = counts.most_common(1)[0]
        if top < 0.9 * counts.total():
            return Miss("ambiguous", tuple(counts))
        ei, _, _, example, translation = max((x for x in found if x[1] == hb), key=lambda x: x[2])
        return Attested(ei, hb, example, translation)

    def match(self, cebuano: str, english: str) -> _Hit | Attested | Miss:
        # A gloss naming two senses leads with the card's own ('I; my, mine' is
        # ako's subject card, R2), and matched whole the second can outscore it.
        if (row := self._decoded.get(letters_key(cebuano.split(",")[0].strip()))) is not None:
            return row
        first = english.split(";")[0]
        if first != english:
            hit = self._match(cebuano, first)
            if isinstance(hit, _Hit) and hit.english_matched:
                return hit
        return self._match(cebuano, english)

    def _match(self, cebuano: str, english: str) -> _Hit | Attested | Miss:
        word = cebuano.split(",")[0].strip()
        hits = self._hits(word) if word and " " not in word else []
        if not hits:
            return (
                self._printed(word) if word and letters_key(word) in self._attested else Miss("not-in-dictionary", ())
            )
        card = _words(english)
        gloss = {h: [s["gloss"] for s in self._senses(h)] for h in hits}
        # Tiers, in order: the same words, then a synonym or a kind of it, then
        # the card word's own definition (tunatale-h0q3.2, the owner: "allow
        # synonyms or close meanings").
        scored = [(self.relations.score(english, card, " ".join(gloss[h])), h) for h in hits]
        best = max(n for n, _ in scored)
        spellings = tuple(dict.fromkeys(h.surface for h in hits))
        if not any(best) and len(hits) == 1:
            return hits[0]._replace(english_matched=False)  # no homograph to choose between
        if not any(best):
            return Miss("english-unmatched", spellings)
        top = [h for n, h in scored if n == best]
        roots = [h for h in top if h.form == 0]
        if len(roots) == 1 and all(h.entry == roots[0].entry for h in top):
            top = roots  # a root beats its own derived forms (bátà 'child' over its (→) batà)
        if len({h.surface for h in top}) > 1:
            # Still tied: a sense whose gloss IS the card's English (kabáyò
            # 'horse' over kabayô 'ride, bring s.t. on a horse'). After the root
            # rule, which it would otherwise overturn (túbig, dûol).
            whole = {h: len(_phrases(english) & {p for g in gloss[h] for p in _phrases(g)}) for h in top}
            if most := max(whole.values()):
                top = [h for h in top if whole[h] == most]
        if len({h.surface for h in top}) > 1 and len({h.entry for h in top}) == 1:
            # One entry printed with and without a hyphen ('cousin' ig-ágaw, and igágaw
            # in its 'first cousin' sub-entries): the card word's own hyphen chooses.
            hyphen = [h for h in top if ("-" in h.surface) == ("-" in word)]
            if len({h.surface for h in hyphen}) == 1:
                top = hyphen
        if len({h.surface for h in top}) > 1:
            return Miss("ambiguous", tuple(dict.fromkeys(h.surface for h in top)))
        return top[0]

    def short_gloss(self, word: str) -> str:
        """Wolff's first sense of *word*, short enough for a card's English: its first
        alternative, without his s.t. / s.o. placeholders ('know s.t., how to do s.t.' ->
        'know'). "" when the letters alone do not settle the entry (tulo is three words).

        The stand-in when the model cannot gloss a new card (tunatale-iiwm.5)."""
        hit = self.match(word, "")
        senses = [s for s in self._senses(hit) if s["gloss"]] if isinstance(hit, _Hit) else []
        if not senses:
            return ""
        first = re.split(r",\s*(?![^()]*\))", senses[0]["gloss"])[0]
        return re.sub(r"\s+(?:s\.t|s\.o|k\.o|s\.w)\.?(?=\s|$)", "", first).strip()

    @cached_property
    def _decoder(self) -> affixes.Lexicon:
        return affixes.lexicon(self.entries, hosts=affixes.default_hosts())

    def by_rule(self, word: str, english: str = "") -> str | None:
        """*word* with its stress by rule, when the affix decoder's best analysis of it
        (ranked by *english*) carries a suffix: Wolff's §5.11 stress on the next-to-last
        syllable (tunatale-h0q3.28). None for any other word. A prefix-only word's stress
        is its root's, and a wrongly picked homograph root would move it. A root with no
        consonant gets no rule (aa 'hey')."""
        found = affixes.analyse(word, self._decoder, english)
        if not found or not found[0].affix or not affixes._split_affix(found[0].affix)[2]:
            return None
        root, affix, _ = found[0]
        core = affixes.strip_marks(root).replace("-", "")
        if core and all(c in "aeiou" for c in core):
            return None
        letters = affixes.wolff_letters(word)
        form = next(c.surface for c in affixes.generate(root, affix) if affixes.strip_marks(c.surface) == letters)
        return onto_card(word, handbook_spelling(shifted(form, "←"), _NO_CORPUS).text)

    def prefix(self, word: str) -> str | None:
        """*word*'s prefix as written, when the decoder's best analysis is a prefix alone and the
        root is the rest of the word letter for letter (mag + lisod), else None (tunatale-lat6).

        A key phrase's long word is built root first from it. A prefix that changes the root
        (maN- + sakay = manakay) or any suffix or infix gives None, and the word is built in
        pairs instead. Handbook o and Wolff's u are one letter here, as in the Handbook.
        """
        found = affixes.analyse(word, self._decoder, "")
        if not found or not found[0].affix:
            return None
        pre, inf, suf, _ = affixes._split_affix(found[0].affix)
        if not pre or inf or suf:
            return None
        flat = word.lower().replace("-", "")

        def letters(text: str) -> str:
            return affixes.strip_marks(text).replace("-", "").lower().replace("u", "o")

        root = letters(found[0].root)
        if not root or len(root) >= len(flat) or not letters(flat).endswith(root):
            return None
        return flat[: len(flat) - len(root)]

    def parts(self, word: str, english: str = "", *, root_rows: bool = True) -> list[Part] | None:
        """*word* as its affixes and root, each glossed, in the word's order; None when the
        decoder finds no affix, a piece has no approved label, or the readings differ.
        *root_rows* False reads with the (piece, root kind) labels alone."""
        settled = self._settled(word, english, root_rows=root_rows)
        if settled is None:
            having = self._having(word)
            if having is None:
                return None
            root, _, gloss = having
            label = _labels()[("-an", _HAVING_KIND)]
            return [Part(root["hb"] or root["wolff"], gloss, False), Part("-an", label, True, _cites()[("-an", label)])]
        found, pieces, inflections, (form, sense, shown, labels) = settled
        affix = found[0].affix
        by_piece = dict(zip(inflections, labels[: len(inflections)], strict=True))
        head = shown["hb"] or shown["wolff"]
        # The card's word is the course spelling, and its i where the Handbook chose a loan's e
        # wins: the root of ibílin is bílin, not bélen (the Handbook spelling of bilin's other
        # entry). A card that spells a different consonant (kinahánglan for kinahángan) or drops
        # one (manánghid, maN- in place of sánghid's s) is not evidence about the vowel.
        letters = _card_letters(word, found[0].root, affix)
        if not (letters and _card_keeps_i(letters, head)):
            letters = None
        root = Part(onto_card(letters, head) if letters else head, self._root_gloss(form, sense), False)
        before = [p for p in pieces if p.endswith("-")]
        after = [p for p in pieces if not p.endswith("-")]
        label = by_piece | ({"pa-": labels[-1]} if "pa-" in pieces else {})
        return [
            *(Part(_PIECE_TEXT.get(p, p), label[p], True, _cites()[(p, label[p])]) for p in before),
            root,
            *(Part(_PIECE_TEXT.get(p, p), label[p], True, _cites()[(p, label[p])]) for p in after),
        ]

    def chart_place(self, word: str, english: str = "") -> tuple[str, frozenset[tuple[str, str]]] | None:
        """*word*'s inflection as the card's verb table names it: the decoder's piece, and
        the (chart pair, subject) places Wolff's class code gives it; None when the parts
        line is silent or the word is a causative. The caller decides whether the places
        agree. Wolff's general i- is never among them: it labels nothing, so the parts line
        never reads it (gitugnaw is tugnaw's 'who is affected', not the general i-)."""
        settled = self._settled(word, english)
        if settled is None:
            return None
        _, pieces, inflections, (_, sense, _, labels) = settled
        if "pa-" in pieces or not (sense and sense["cls"]):
            return None  # the chart has no causative rows; a root with no verb sense has no code
        piece = inflections[0]
        places = {
            (pair, subject)
            for pair, subject in _licenses(sense["cls"])
            if pair in _PIECE_PAIRS.get(piece, ()) and (pair, subject) != ("i-/gi-", USED)
        }
        # The label the parts line chose says which subject it read, when it is a row by
        # subject (a row for one root names the root, not the subject).
        return piece, frozenset({p for p in places if _labels().get((piece, p[1])) == labels[0]} or places)

    def _settled(self, word: str, english: str, *, root_rows: bool = True) -> tuple | None:
        """The decoder's one settled reading of *word*: (its analyses, the pieces, the
        inflections, (form, licensing sense, form as spelled, labels)), or None."""
        found = affixes.analyse(word, self._decoder, english)
        if not found or not found[0].affix:
            return None
        affix = found[0].affix
        pieces = affix.split("+")
        if not _VERBAL & set(pieces):
            return None  # a derived noun's affix (ka-, -an, -on, a bare pa-), not a verb's
        if len([p for p in pieces if p != "pa-"]) > 1:
            return None  # a verb carries one inflection: ma- with -on makes an adjective (malipayon)
        if len(affixes.strip_marks(found[0].root).replace("-", "")) < 3:
            return None  # the decoder's, not Cebuano's: nawa 'may it be' is no na- + wa 'left hand'
        inflections = [p for p in pieces if p != "pa-"]
        # The decoder ranks the roots (by the English, then the entry), and its best root
        # decides: falling through to a lower one when it reads nothing is a guess (pasusoa
        # 'breastfeed' would read as sûsò 'pound'). Its own stress forms must then agree.
        if set(pieces) <= {"-a", "-i"} and self._first_pos(found[0].root) == "n":
            return None  # on a noun, -a is Wolff's 'that particular' (baláya), not the command
        readings = self._readings(found[0].root, pieces, inflections, english, word, root_rows=root_rows)
        # exactly one reading, and not one form that reads two ways (None)
        if len(readings) != 1 or None in readings:
            return None
        if "ma-" in pieces and self._first_pos(found[0].root) == "a":
            return None  # Wolff's ma-_2: on an adjective root, ma- makes the adjective (mainit 'hot')
        return found, pieces, inflections, next(iter(readings.values()))

    def _first_pos(self, root: str) -> str | None:
        """The part of speech *root*'s main entry opens with (ínit 'hot, warm': a)."""
        senses = next(r for r in self.entries if root in r["heads"])["forms"][0]["senses"]
        return senses[0]["pos"] if senses else None

    def _agreeing(self, senses: list[dict], english: str) -> list[tuple[dict, tuple[int, int, int]]]:
        """(sense, how the English names it) over *senses*: the same word, a related one, or
        one its definition holds (EnglishRelations, as a card's match uses). A lesson's 'see'
        rules out kítà's 'earn'; no English, no filter, and every sense is strong.

        An irregular past ('ate') falls back to its verb only when nothing agrees as written
        by word or relation, and then only a sense that names the verb itself counts
        (tunatale-h0q3.37)."""
        if not english.strip():
            return [(s, (1, 1, 1)) for s in senses]
        card = _words(english)
        scored = [(s, self.relations.score(english, card, s["gloss"])) for s in senses]
        out = [(s, t) for s, t in scored if any(t)]
        base = _verb_base(english)
        if base == english or any(t[0] or t[1] for _, t in out):
            return out
        card = _words(base)
        retry = [(s, self.relations.score(base, card, s["gloss"])) for s in senses]
        return [(s, t) for s, t in retry if t[0]] or out

    def _readings(
        self,
        root: str,
        pieces: list[str],
        inflections: list[str],
        english: str = "",
        word: str = "",
        *,
        root_rows: bool = True,
    ) -> dict:
        """reading -> (the form that gives it, its licensing sense, the form as the word spells
        it) over *root*'s forms and stress forms, a pointer ('suwat = sulat') followed. A root's
        part of speech stands in only when no form reads as a verb (abut 'arrive' is a verb;
        the homograph abut, a kind of grain, does not make mi- 'became')."""
        candidates = []
        for ei, rec in enumerate(self.entries):
            if root not in rec["heads"]:
                continue
            for fi, form in enumerate(rec["forms"]):
                if fi and form["label"] not in _ROOT_FORMS:
                    continue
                tei, tfi = self._resolve(ei, fi, letters_key(root)) or (ei, fi)
                candidates.append((self.entries[tei], self.entries[tei]["forms"][tfi], form, not fi))
        # The card's own stress names a word. A final-stress form it spells, and the root does
        # not, is the word ('nakakitâ' is kitâ, not kítà). When the card names the root, a
        # differently stressed sub-entry that is also another headword is not it (nagsakít is
        # sakít, not sákit 'torture'). Unnamed stress forms remain: two words still silence.
        named = [c for c in candidates if _names(word, c[2]["hb"] or c[2]["wolff"] or "")]
        finals = [c for c in named if not c[3] and c[2]["label"] == "(→)"]
        # The card names a stress form that two entries both spell: two words, and neither is
        # the word's (buhî is both 'alive''s 'raise' and 'set free''s, tunatale-30t0).
        if len({id(c[0]) for c in finals}) > 1:
            return {}
        final = [c for c in finals if c[2]["xref"] is None]
        if final and not any(c[3] for c in named):
            candidates = final
        elif named:
            candidates = [
                c for c in candidates if c[3] or c in named or (c[2]["hb"] or "").lower() not in self._spellings
            ]
        readings: dict = {}
        # The 'will become' kind of reading is a guess when the word came with an English
        # that no sense of the root agrees with (tunatale-30t0): it ignores the English,
        # and mosóod 'will enter' is not 'will become close in space or time'. A root the
        # English does name keeps it (mopulá 'becomes yellow-brown' is pulá 'red').
        kinds = (False, True)
        if english.strip():
            card = _words(english)
            agrees = any(
                any((t := self.relations.score(english, card, s["gloss"]))[0] or t[1] for s in form["senses"])
                for _, form, _, _ in candidates
            )
            kinds = (False, True) if agrees else (False,)
        for by_kind in kinds:
            for rec, form, shown, _ in candidates:
                reading = self._reading(rec, form, pieces, inflections, english, by_kind=by_kind, root_rows=root_rows)
                if reading is not None:
                    readings.setdefault(reading[0], (form, reading[1], shown, reading[2]))
            if readings:
                break
        return readings

    def _reading(
        self,
        rec: dict,
        form: dict,
        pieces: list[str],
        inflections: list[str],
        english: str = "",
        *,
        by_kind: bool,
        root_rows: bool = True,
    ):
        """(labels, licensing sense, labels with the root's own rows) of *form* for the pieces,
        (None, None, None) when a piece has two labels, or None when the form does not license them."""
        verbs = [s for s in form["senses"] if s["pos"] == "v" and s["cls"]]
        source = verbs
        if "pa-" in pieces:
            causative = next((f for f in rec["forms"] if "pa-" in re.split(r",\s*", f["label"])), None)
            source = [s for s in (causative or form)["senses"] if s["pos"] == "v" and s["cls"]] or verbs
        kind = None
        if by_kind:
            source = []
            kind = "adjective" if form["senses"] and form["senses"][0]["pos"] == "a" else None
        labels: list[str] = []
        worded: list[str] = []
        sense = None
        for piece in inflections:
            # Wolff lists a root's main sense first: the first sense that licenses the piece decides
            found: dict[str, dict | None] = {}
            stative = _labels().get((piece, UNDERGOER))
            best: tuple[int, int, int] | None = None
            for s, tier in self._agreeing(source, english):
                for pair, subject in _licenses(s["cls"]):
                    if pair not in _PIECE_PAIRS.get(piece, ()) or (piece, subject) not in _labels():
                        continue
                    label = _labels()[(piece, subject)]
                    # Wolff §7.12: a verb of state's ma-/na- is its own active. An English
                    # reached only through the definition does not make it a state's verb:
                    # píldi's 'die of a sickness' is not 'lost' (tunatale-30t0).
                    if piece in ("ma-", "na-") and label == stative and not (tier[0] or tier[1]):
                        continue
                    found.setdefault(label, s)
                if found:
                    # A sense Wolff lists before this one that the English names more exactly
                    # and that does not take this piece is the word the row names: ingón
                    # [A; b5c] 'say, tell' is 'tell him', not {1a} 'said to be' + -a. The row
                    # corroborates the affix when its English says what the affix means
                    # (magkítà 'will see each other': the label's 'each other' is the reciprocal
                    # that only kítà's 'meet with' takes, not 'see').
                    if best is not None and best > tier and not any(_words(english) & _words(code) for code in found):
                        return None
                    break
                best = tier if best is None else max(best, tier)
            if kind and not found and (piece, kind) in _labels():
                found[_labels()[(piece, kind)]] = None
            if len(found) > 1 and piece in ("ma-", "na-") and stative in found:
                # Wolff §7.12: a verb of state's ma-/na- is its own active, not a potential
                found = {stative: found[stative]}
            if not found:
                return None
            if len(found) > 1:
                return (None, None, None)
            ((label, licensing),) = found.items()
            labels.append(label)
            # A row the owner worded for this root (tunatale-tofg.16: molárga 'will', not the
            # state's 'will become'). It relabels only: the readings must first agree on the
            # plain labels, or two homographs (ingón 'say' and ingón 'head for') would agree
            # by both taking the root's row.
            worded.append(_labels().get((piece, f"root {rec['forms'][0]['hb']}"), label) if root_rows else label)
            sense = sense or licensing
        if "pa-" in pieces:
            pa = "verb" if verbs else kind
            if (("pa-", pa)) not in _labels():
                return None
            labels.append(_labels()[("pa-", pa)])
            worded.append(labels[-1])
            sense = verbs[0] if verbs else None
        return tuple(labels), sense, tuple(worded)

    def _root_gloss(self, form: dict, sense: dict | None) -> str:
        """The root's own first meaning, then the verb sense that licensed the affix, unless
        the first meaning already says it ('get and bring' covers 'get')."""
        first = next((s for s in form["senses"] if s["gloss"]), None)
        own = _plain(first["gloss"]) if first and first["pos"] != "v" else ""
        verb = _plain(sense["gloss"]) if sense else ""
        if verb and own and _words(verb) <= _words(own):
            verb = ""
        return " · ".join(filter(None, [own, verb])) or (_plain(first["gloss"]) if first else "")

    def _having(self, word: str) -> tuple[dict, dict, str] | None:
        """(the root's form, the word's sub-entry, the root's noun gloss) when *word* is Wolff's
        -an 4, sense 2 adjective, 'having [noun]': kúsgan = kusóg 'strength' + -an. Every part
        of the rule must hold, so the line stays silent rather than guess:

        * Wolff prints the word as its own sub-entry of the root, labelled with the word itself
          or with -an (kúsgan under kusug), and no other form has its letters;
        * the word is the root plus -an by Wolff's sound rules (kusug -> kusg-an);
        * the sub-entry is an adjective, and the root has a noun sense to build on;
        * the stress is not on the last syllable (that is -an 4, sense 1).

        The gloss is the root's first noun sense, stopped before any idiom Wolff runs on after it
        ('seed. makaagpas sa — …'), not its first sense: kúsgan is not 'fast'."""
        if letters_key(word) in _NOT_HAVING:
            return None
        hits = self._hits(word)
        # reached directly or by Wolff's own pointer ('kúkhan see kuku'); a short form never
        # passes the label test below, its letters being another word's
        if len(hits) != 1 or hits[0].form == 0:
            return None
        rec = self.entries[hits[0].entry]
        root, form = rec["forms"][0], rec["forms"][hits[0].form]
        label = form["label"].split(",")[0].strip()
        if not root["wolff"] or not (label in _HAVING_AFFIXES or letters_key(label) == letters_key(word)):
            return None
        pos = [s["pos"] for s in form["senses"] if s["pos"]]
        nouns = [s["gloss"] for s in root["senses"] if s["pos"] == "n" and s["gloss"]]
        if not pos or pos[0] != "a" or not nouns or _final_stress(form["hb"] or form["wolff"]):
            return None
        letters = affixes.wolff_letters(word)
        if all(affixes.letter_map(letters, root["wolff"], affix) is None for affix in _HAVING_AFFIXES):
            return None
        return root, form, _plain(re.split(r"\.\s", nouns[0])[0])

    def parts_line(self, word: str, english: str = "", *, root_rows: bool = True) -> str | None:
        """*word* glossed by its parts, as the card and the reader show it: 'mo- will + ádto …'."""
        parts = self.parts(word, english, root_rows=root_rows)
        return None if parts is None else " + ".join(f"{p.text} {p.gloss}" for p in parts)

    def _parts_fields(self, cebuano: str, english: str) -> tuple[str, str]:
        """The parts line for a card's Meaning and its Wolff senses for Details, or ("", "")."""
        word = cebuano.split(",")[0].strip()
        parts = self.parts(word, english) if word and " " not in word else None
        if parts is None:
            return "", ""
        line = " + ".join(f"{p.text} {p.gloss}" for p in parts)
        cites = "; ".join(f"{p.text} = {p.cite}" for p in parts if p.affix)
        return (
            f'<div class="wolff-parts">{html.escape(line)}</div>',
            f'<div class="wolff-parts-cite">Wolff\u2019s senses: {html.escape(cites)}</div>',
        )

    def card_fields(self, cebuano: str, english: str) -> CardFields | Miss:
        fields = self._card_fields(cebuano, english)
        if not isinstance(fields, Miss) and _repeats_front(fields.spelling, cebuano):
            # The line under the word says something only when Wolff marks it differently:
            # 'to' under 'to' is noise (the owner, 2026-11-16).
            fields = replace(fields, spelling="")
        lead, cite = self._parts_fields(cebuano, english)
        if not lead:
            return fields
        if isinstance(fields, Miss):
            if fields.reason != "not-in-dictionary":
                return fields
            # an inflected word Wolff does not list: the line is all the dictionary can say
            return CardFields(spelling="", meaning=lead, verb="", details=cite, dictionary_entry="")
        # A hand-decoded form (wolff_decoded.tsv) leads with its own approved reading; its one
        # row, kabalo, is ka- (maka-'s short form), which is no verb's inflection here, so the
        # two leads never meet.
        return replace(fields, meaning=lead + fields.meaning, details=fields.details + cite)

    def _card_fields(self, cebuano: str, english: str) -> CardFields | Miss:
        hit = self.match(cebuano, english)
        if hit == Miss("not-in-dictionary", ()):
            word = cebuano.split(",")[0].strip()
            ruled = self.by_rule(word, english) if word and " " not in word else None
            if ruled is not None:
                return CardFields(spelling=ruled, meaning="", verb="", details=RULE_NOTE, dictionary_entry="")
        if isinstance(hit, Miss):
            return hit
        if isinstance(hit, Attested):
            root = self.entries[hit.entry]["forms"][0]["hb"] or self.entries[hit.entry]["heads"][0]
            return CardFields(
                spelling=self._spelling(cebuano, hit.surface, hit.entry),
                meaning="",
                verb="",
                details=(
                    f"<div>Not listed in Wolff: spelled as he prints it, in the entry for <b>{html.escape(root)}</b>.</div>"
                    f'<div class="wolff-ex"><i>{html.escape(hit.example)}</i> {html.escape(hit.translation)}</div>'
                ),
                dictionary_entry=self._dictionary_entry(hit),
            )
        verb = self._verb(hit, _words(english))
        return CardFields(
            spelling=self._spelling(cebuano, hit.surface, hit.entry),
            meaning=self._meaning(hit, verb_shown=bool(verb)),
            verb=verb,
            details=self._details(hit, cebuano, subjects=not verb),
            dictionary_entry=self._dictionary_entry(hit),
        )

    def _spelling(self, cebuano: str, surface: str, entry: int) -> str:
        """The card word with *surface*'s marks, and each other form on a front such as
        'manong, nong' with its own (Wolff's short forms carry theirs: unyà, nyà).

        A form joins only when it is the same dictionary word: 'Oo, salamat' is two words,
        and the line is the card word's.
        """
        first, *rest = (w.strip() for w in cebuano.split(","))
        out = [onto_card(first, surface)]
        for word in rest:
            same = next((h.surface for h in self._hits(word) if h.entry == entry), None)
            if same is not None:
                out.append(onto_card(word, same))
        return ", ".join(out)

    # --- the tiers ----------------------------------------------------------------------------

    def _led_senses(self, hit: _Hit) -> list[dict]:
        """*hit*'s senses, the card word's lead senses first (``wolff_sense_lead.tsv``), else Wolff's order."""
        senses = self._senses(hit)
        if lead := self._sense_lead.get(_marked(hit.surface)) or self._sense_lead.get(letters_key(hit.surface)):
            first = [s for key in lead for s in senses if (s["pos"], str(s["n"])) == key]
            senses = first + [s for s in senses if s not in first]
        return senses

    def _meaning(self, hit: _Hit, *, verb_shown: bool = False) -> str:
        senses = self._led_senses(hit)
        if verb_shown and senses and senses[0]["pos"] != "v":
            senses = [s for s in senses if s["pos"] != "v"]  # the verb's own glosses lead its label
        glosses = list(dict.fromkeys(g for s in senses if (g := sense_before_idiom(s["gloss"] or ""))))
        more = "; …" if len(glosses) > _MEANING_SENSES else ""
        meaning = html.escape("; ".join(glosses[:_MEANING_SENSES])) + more
        row = hit.decoded
        if row is None or not row.reading:
            return meaning
        # An inflected card word leads with its own reading; the sense is Wolff's, under another word.
        rec = self.entries[hit.entry]
        affix = f"{row.affix} (short for {_SHORT_OF[row.affix]})" if row.affix in _SHORT_OF else row.affix
        return (
            f"<b>{html.escape(row.spelling)}</b> = {_built(affix, rec['forms'][0]['hb'] or '')}: "
            f"{html.escape(row.reading)}"
            f'<div class="wolff-under">Wolff, under {html.escape(rec["forms"][hit.form]["hb"] or "")}: {meaning}</div>'
        )

    def _verb(self, hit: _Hit, card: set[str]) -> str:
        """The verb, when the card is the verb: its first sense is one, or its English is a
        verb sense's (a noun card such as bátà 'child' gets no verb line). The kind of verb,
        then a row per affix, grouped by sense when senses take different affixes."""
        senses = self._led_senses(hit)
        verbs = [s for s in senses if s["pos"] == "v"]
        if not verbs or not (senses[0]["pos"] == "v" or any(card & _words(s["gloss"]) for s in verbs)):
            return ""
        lead = verb_kinds([s["cls"] or "" for s in verbs])
        if senses[0]["pos"] != "v":
            glosses = "; ".join(dict.fromkeys(s["gloss"] for s in verbs if s["gloss"]))
            lead.insert(0, f"Also a verb: {glosses.rstrip('.')}.")
        groups: dict[str, list[str]] = {}
        for s in verbs:
            if s["cls"]:
                groups.setdefault(s["cls"], []).append(s["gloss"])
        # A later sense shows only the rows the earlier ones lack; an affix with another
        # subject is another row (oras: mo- 'time s.t.' [A], and 'get to be [time]' [B]).
        seen: set[tuple[str, str]] = set()
        shown: list[tuple[str, list[_Row]]] = []
        for cls, glosses in groups.items():
            rows = [self._row(hit, a, subj) for a, subj in decode_classes(cls) if (a, subj) not in seen]
            seen |= {(r.affix, r.subject) for r in rows}
            if rows:
                # the general i- (for someone, at a time), which nearly every verb has, goes last
                rows.sort(key=lambda r: r.subject == USED)
                shown.append(("; ".join(dict.fromkeys(g for g in glosses if g)), rows))
        out = [f'<div class="wolff-kind">{html.escape(" ".join(lead))}</div>'] if lead else []
        examples = [tuple(s["ex"]) for s in verbs if s["ex"]]
        word = letters_key(hit.surface if hit.decoded is None else hit.decoded.spelling)
        forms: list[str] = []
        for gloss, rows in shown:
            if len(shown) > 1:
                forms.append(f'<div class="wolff-h">{html.escape(gloss)}</div>')
            forms.append(self._table(rows, examples, word))
        if forms:
            # Open, the tables took over the back of the card (tunatale-lvqq): the kind
            # line stays in view, the forms open on a tap. Class `dict` reuses the
            # notetype's disclosure style, so no notetype CSS has to change.
            out.append(
                '<details class="dict wolff-forms"><summary>Verb forms</summary>' + "".join(forms) + "</details>"
            )
        return "".join(out)

    def _table(self, rows: list[_Row], examples: list[tuple[str, str]], word: str) -> str:
        """One sense's rows as a table, so the forms line up in their columns across triggers;
        a trigger row names the subject once for the rows under it (owner's layout B3)."""
        out = ['<table class="wolff-verb"><thead><tr><th></th><th>not begun</th><th>begun</th></tr></thead><tbody>']
        under = None
        for row in rows:
            trigger, _, _ = _row_label(row.affix, row.subject).partition(" · ")
            if (trigger, row.subject) != under:
                under = (trigger, row.subject)
                out.append(
                    f'<tr class="wolff-trigger"><td colspan="3"><b>{html.escape(trigger)}</b>'
                    f" · subject: {html.escape(row.subject)}</td></tr>"
                )
            out.append(self._render_row(row, examples, word))
        out.append("</tbody></table>")
        return "".join(out)

    def _row(self, hit: _Hit, affix: str, subject: str) -> _Row:
        form = self.entries[hit.entry]["forms"][hit.form]
        base = form["hb"] or ""
        pre_not, pre_begun = _PREFIXES.get(affix, (None, None))
        row = _Row(
            affix,
            subject,
            [_prefixed(pre_not, base)] if pre_not and base else [],
            [_prefixed(pre_begun, base)] if pre_begun and base else [],
            [_prefixed("ka", base)] if affix == "maka-/naka-" and base else [],
        )
        for (row_affix, begun), printed in self._printed_forms(hit.entry, hit.form):
            if row_affix == affix and printed not in row.not_begun + row.begun:
                (row.begun if begun else row.not_begun).insert(0, printed)
        if affix == "-an/gi-an" and row.not_begun and not row.begun:
            row.begun.append("gi" + row.not_begun[0])
        if hit.decoded is not None and _SHORT_OF.get(hit.decoded.affix) == affix:
            row.short.append(hit.decoded.spelling)
        return row

    def _printed_forms(self, ei: int, fi: int) -> list[tuple[tuple[str, int], str]]:
        """The suffixed forms of *fi*'s word that Wolff prints in the entry, each with its row."""
        rec = self.entries[ei]
        form = rec["forms"][fi]
        root = letters_key(form["wolff"] or "")
        texts = [s["ex"][0] for s in form["senses"] if s["ex"]] + [a[0] for a in rec.get("attested", ())]
        out: list[tuple[tuple[str, int], str]] = []
        for tok in dict.fromkeys(t.lower() for text in texts for t in _TOKEN.findall(text)):
            found = affixes.analyse(tok, self._decoder, "")
            if found and letters_key(found[0].root) == root and found[0].affix in _PRINTED_SLOT:
                out.append((_PRINTED_SLOT[found[0].affix], tok))
        return out

    def _render_row(self, row: _Row, examples: list[tuple[str, str]], word: str) -> str:
        def mark(forms: list[str]) -> str:
            return ", ".join(f"{f} ← this card" if letters_key(f) == word else f for f in forms)

        here = word in {letters_key(f) for f in row.not_begun + row.begun + row.short}
        _, _, head = _row_label(row.affix, row.subject).partition(" · ")
        role = _ROLE.get((row.subject, row.affix), "")
        cell = f"<b>{html.escape(head)}</b>"
        cell += f'<div class="wolff-role">{html.escape(role)}</div>' if role else ""
        not_begun = html.escape(mark(row.not_begun))
        not_begun += f'<div class="wolff-short">short {html.escape(mark(row.short))}</div>' if row.short else ""
        cls = " wolff-here" if here else " wolff-dim" if row.subject == USED else ""
        out = f'<tr class="wolff-row{cls}"><td>{cell}</td><td>{not_begun}</td><td>{html.escape(mark(row.begun))}</td></tr>'
        forms = {letters_key(f) for f in row.not_begun + row.begun + row.short}
        example = next((ex for ex in examples if forms & {letters_key(t) for t in _TOKEN.findall(ex[0])}), None)
        if example is not None:
            examples.remove(example)
            out += (
                '<tr class="wolff-ex"><td colspan="3"><details><summary>Example</summary>'
                f'<div class="wolff-ceb">{html.escape(_stopped(example[0]))}</div>'
                f'<div class="wolff-en">{html.escape(example[1])}</div></details></td></tr>'
            )
        return out

    def _points_at_root(self, hit: _Hit) -> bool:
        """'palíhug see *líhug': the pointer lands on a root never said bare."""
        pointer = self.entries[hit.via[0]]["forms"][hit.via[1]]["xref"] if hit.via is not None else None
        return pointer is not None and pointer[1].startswith("*")  # a short-form list has no pointer

    def _full_form(self, hit: _Hit) -> str | None:
        """The word *hit* is Wolff's short form of, or None.

        A pointer is a short form ('náa = anáa') only when what it points at
        is spelled differently. 'maáyu see áyu' lands on the sub-entry màáyo,
        the card's own spelling, which the root line already shows.
        'palíhug see *líhug' lands on a root never said bare: that is where the
        word comes from, not what it is short for ('ta see kita' is short for).
        """
        form = self.entries[hit.entry]["forms"][hit.form]
        if hit.via is None or form["hb"] == hit.surface or self._points_at_root(hit) or not form["hb"]:
            return None
        # Wolff says so ('ka short for ikaw'), or the full word lists it among its short
        # forms (anáa 'short form: náa'), or a 'see' pointer lands on a word it is a
        # shortening of ('ta see kita': fewer letters, in order). Every other pointer is a
        # spelling ('pwídi = puydi', 'saysinta' too) or a root the word is built on
        # ('kawhaan see duha', 'gilayun see dáyun'); tunatale-iiwm.11 found cards calling
        # those short forms.
        via = self.entries[hit.via[0]]["forms"][hit.via[1]]
        short = letters_key(via["wolff"] or hit.surface)
        full = letters_key(form["wolff"] or form["hb"])
        kind = via["xref"][0] if via["xref"] else None
        listed = {letters_key(w) for w, _ in form["short"]} & {short, letters_key(hit.surface)}
        if kind == "short for" or listed or (kind == "see" and len(short) < len(full) and _in_order(short, full)):
            return form["hb"]
        return None

    def full_form(self, cebuano: str, english: str) -> str | None:
        """The word Wolff says the card *cebuano* (glossed *english*) is short for, or None
        (tunatale-iiwm.11): the card's Short form line where the tables say nothing."""
        hit = self.match(cebuano, english)
        return self._full_form(hit) if isinstance(hit, _Hit) else None

    def _details(self, hit: _Hit, cebuano: str, *, subjects: bool = True) -> str:
        rec = self.entries[hit.entry]
        form = rec["forms"][hit.form]
        out: list[str] = []
        if hit.decoded is not None:
            note = f" {html.escape(hit.decoded.note)}" if hit.decoded.note else ""
            out.append(
                f"<div>Not written out in Wolff: "
                f"{_built(hit.decoded.affix, rec['forms'][0]['hb'] or '', bold=True)}. He gives the meaning under "
                f"<b>{html.escape(form['hb'] or '')}</b>.{note}</div>"
            )
        to_root = self._points_at_root(hit)
        full = self._full_form(hit)
        if full is not None:
            out.append(f"<div>Short form of <b>{html.escape(full)}</b></div>")
        if (hit.form != 0 or to_root) and rec["forms"][0]["hb"]:
            root = rec["forms"][0]
            gloss = next((s["gloss"] for s in _meanings(root) if s["gloss"]), "")
            # a 'having' word builds on the root's noun sense, and says so here as in its line
            having = self._having(cebuano.split(",")[0].strip())
            if having is not None and having[1] is form:
                gloss = having[2]
            out.append(f"<div>From the root <b>{html.escape(root['hb'])}</b> {html.escape(gloss)}</div>")
        out.append(self._sense_list(form))
        # The verb's rows name each affix's subject; a card with no verb line keeps the list.
        listed = self._subjects(form) if subjects else ""
        if listed:
            out.append('<div class="wolff-h">Who is the subject</div><ul>' + listed + "</ul>")
        related = [
            (f["hb"] or f["label"], next((s["gloss"] for s in f["senses"] if s["gloss"]), ""))
            for i, f in enumerate(rec["forms"])
            if i not in (0, hit.form) and f["senses"]
        ][:_RELATED]
        if related:
            items = "".join(f"<li><b>{html.escape(w)}</b> {html.escape(g)}</li>" for w, g in related)
            out.append('<div class="wolff-h">Related words</div><ul>' + items + "</ul>")
        others = [
            (h.surface, gloss)
            for h in self._hits(cebuano.split(",")[0].strip())
            if (h.entry, h.form) != (hit.entry, hit.form)
            # a † addendum adds senses to the same word, not another word (tunatale-h0q3.76)
            and not self.entries[h.entry]["text"].startswith("†")
            # a pointer whose target gives no gloss has nothing to tell apart
            and (gloss := next((s["gloss"] for s in self._senses(h) if s["gloss"]), ""))
        ]
        if others:
            items = "".join(f"<li><b>{html.escape(w)}</b> {html.escape(g)}</li>" for w, g in others)
            out.append('<div class="wolff-h">Not to be confused with</div><ul>' + items + "</ul>")
        return "".join(out)

    def _sense_list(self, form: dict) -> str:
        out, pos, items = [], None, []

        def flush():
            if items:
                label = _POS_NAMES.get(pos or "", pos or "")
                out.append(f'<div class="wolff-pos">{html.escape(label)}</div><ol>' + "".join(items) + "</ol>")
                items.clear()

        for s in _meanings(form):
            if s["pos"] != pos:
                flush()
                pos = s["pos"]
            if s["phrase"]:
                text = f"<b>{html.escape((form['hb'] or '') + ' ' + s['phrase'])}</b> {html.escape(s['gloss'])}"
            else:
                text = html.escape(s["gloss"])
            if s["ex"]:
                text += f"<br><i>{html.escape(s['ex'][0])}</i> {html.escape(s['ex'][1])}"
            items.append(f"<li>{text}</li>")
        flush()
        return "".join(out)

    def _subjects(self, form: dict) -> str:
        by_subject: dict[str, list[str]] = {}
        for s in form["senses"]:
            if s["pos"] == "v":
                for affix, subject in decode_classes(s["cls"] or ""):
                    if affix not in by_subject.setdefault(subject, []):
                        by_subject[subject].append(affix)
        return "".join(f"<li>{html.escape(', '.join(a))}: {html.escape(subj)}</li>" for subj, a in by_subject.items())

    def _dictionary_entry(self, hit: _Hit) -> str:
        texts = [self.entries[hit.via[0]]["text"]] if hit.via and hit.via[0] != hit.entry else []
        texts.append(self.entries[hit.entry]["text"])
        body = "".join(f"<p>{html.escape(t)}</p>" for t in texts)
        return f'<div class="wolff-cite">{CITATION}</div>{body}'


@cache
def load_lexicon() -> WolffLexicon:
    """The committed lexicon."""
    with gzip.open(LEXICON_PATH, "rt", encoding="utf-8") as fh:
        return WolffLexicon(json.load(fh)["entries"], EnglishRelations.load(), load_decoded(), load_sense_lead())
