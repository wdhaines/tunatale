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
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache, cached_property
from pathlib import Path
from typing import NamedTuple

from app.cards.vocab_notetype import DICTIONARY_FIELDS
from app.plugins.languages.ceb import affixes
from app.plugins.languages.ceb.handbook_spelling import SpellingLexicon, convert_text, handbook_spelling
from app.plugins.languages.ceb.wolff_dictionary import (
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


def load_decoded(path: Path = DECODED_PATH) -> list[Decoded]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [Decoded(*line.split("\t")) for line in lines if line and not line.startswith("#")]


class Attested(NamedTuple):
    """A word no entry lists, spelled as Wolff prints it in an example of *entry*."""

    entry: int
    surface: str
    example: str  # in Handbook spelling
    translation: str
    via: None = None


def _meanings(form: dict) -> list[dict]:
    """*form*'s senses without the two kinds that are not meanings: the affix a
    run of senses belongs to ('*lihug pa- {1} please'), and a pointer to another
    affix's form ('n {1} = -in-')."""
    return [s for s in form["senses"] if not is_affix_label(s["gloss"].removeprefix("= "))]


class _Hit(NamedTuple):
    entry: int
    form: int
    surface: str  # the card word's own Handbook spelling (a short form's, if it came through one)
    via: tuple[int, int] | None  # the pointer it came through ('náa = anáa', a short form)
    english_matched: bool = True  # False: the only candidate, taken on its letters
    decoded: Decoded | None = None  # came through a hand row, not the letters


class WolffLexicon:
    def __init__(
        self, entries: Sequence[dict], relations: EnglishRelations | None = None, decoded: Sequence[Decoded] = ()
    ):
        self.entries = list(entries)
        self.relations = relations if relations is not None else EnglishRelations.empty()
        self._decoded: dict[str, _Hit] = {}
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
            self._decoded[letters_key(row.word)] = _Hit(*found[0], row.spelling, None, decoded=row)
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

    def _resolve(self, ei: int, fi: int, word_key: str, depth: int = 0) -> tuple[int, int] | None:
        """Follow a form's '= x' / 'see x' / 'short for x' to the form it points at."""
        xref = self.entries[ei]["forms"][fi]["xref"]
        # A short form means what its full form means, even when it has usage
        # notes of its own ('ka short for ikaw'); other pointers with senses stand.
        if not xref or (self.entries[ei]["forms"][fi]["senses"] and xref[0] != "short for"):
            return None
        # '*líhug': the star marks a root never said bare, and is no part of its head
        target = strip_marks(re.split(r"[ ,]", xref[1])[0]).lower().lstrip("*").split("_")[0]
        for tei in self._heads.get(target, []):
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
        senses = [s for s in _meanings(self.entries[hit.entry]["forms"][hit.form]) if not s["phrase"]]
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

    @cached_property
    def _decoder(self) -> affixes.Lexicon:
        return affixes.lexicon(self.entries)

    def by_rule(self, word: str, english: str = "") -> str | None:
        """*word* with its stress by rule, when the affix decoder's best analysis of it
        (ranked by *english*) carries a suffix: Wolff's §5.11 stress on the next-to-last
        syllable (tunatale-h0q3.28). None for any other word. A prefix-only word's stress
        is its root's, and a wrongly picked homograph root would move it."""
        found = affixes.analyse(word, self._decoder, english)
        if not found or not found[0].affix or not affixes._split_affix(found[0].affix)[2]:
            return None
        root, affix, _ = found[0]
        letters = affixes.wolff_letters(word)
        form = next(c.surface for c in affixes.generate(root, affix) if affixes.strip_marks(c.surface) == letters)
        return onto_card(word, handbook_spelling(shifted(form, "←"), _NO_CORPUS).text)

    def card_fields(self, cebuano: str, english: str) -> CardFields | Miss:
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
        return CardFields(
            spelling=self._spelling(cebuano, hit.surface, hit.entry),
            meaning=self._meaning(hit),
            verb=self._verb(hit, _words(english)),
            details=self._details(hit, cebuano),
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

    def _meaning(self, hit: _Hit) -> str:
        glosses = list(dict.fromkeys(s["gloss"] for s in self._senses(hit) if s["gloss"]))
        more = "; …" if len(glosses) > _MEANING_SENSES else ""
        return html.escape("; ".join(glosses[:_MEANING_SENSES])) + more

    def _verb(self, hit: _Hit, card: set[str]) -> str:
        """The affixes, when the card is the verb: its first sense is one, or its English
        is a verb sense's (a noun card such as bátà 'child' gets no verb line)."""
        senses = self._senses(hit)
        verbs = [s for s in senses if s["pos"] == "v"]
        if not verbs or not (senses[0]["pos"] == "v" or any(card & _words(s["gloss"]) for s in verbs)):
            return ""
        affixes: dict[str, str] = {}
        for s in verbs:
            for affix, subject in decode_classes(s["cls"] or ""):
                affixes.setdefault(affix, subject)
        return ", ".join(affixes)

    def _details(self, hit: _Hit, cebuano: str) -> str:
        rec = self.entries[hit.entry]
        form = rec["forms"][hit.form]
        out: list[str] = []
        if hit.decoded is not None:
            note = f" {html.escape(hit.decoded.note)}" if hit.decoded.note else ""
            out.append(
                f"<div>Not written out in Wolff: <b>{html.escape(hit.decoded.affix)}</b> + "
                f"<b>{html.escape(rec['forms'][0]['hb'] or '')}</b>. He gives the meaning under "
                f"<b>{html.escape(form['hb'] or '')}</b>.{note}</div>"
            )
        # A pointer is a short form ('náa = anáa') only when what it points at
        # is spelled differently. 'maáyu see áyu' lands on the sub-entry màáyo,
        # the card's own spelling, which the root line below already shows.
        # 'palíhug see *líhug' lands on a root never said bare: that is where the
        # word comes from, not what it is short for ('ta see kita' is short for).
        pointer = self.entries[hit.via[0]]["forms"][hit.via[1]]["xref"] if hit.via is not None else None
        to_root = pointer is not None and pointer[1].startswith("*")  # a short-form list has no pointer
        if hit.via is not None and form["hb"] != hit.surface and not to_root:
            out.append(f"<div>Short form of <b>{html.escape(form['hb'] or '')}</b></div>")
        if (hit.form != 0 or to_root) and rec["forms"][0]["hb"]:
            root = rec["forms"][0]
            gloss = next((s["gloss"] for s in _meanings(root) if s["gloss"]), "")
            out.append(f"<div>From the root <b>{html.escape(root['hb'])}</b> {html.escape(gloss)}</div>")
        out.append(self._sense_list(form))
        subjects = self._subjects(form)
        if subjects:
            out.append('<div class="wolff-h">Who is the subject</div><ul>' + subjects + "</ul>")
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
        return WolffLexicon(json.load(fh)["entries"], EnglishRelations.load(), load_decoded())
