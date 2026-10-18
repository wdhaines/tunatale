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
"""

from __future__ import annotations

import gzip
import html
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import NamedTuple

from app.cards.vocab_notetype import DICTIONARY_FIELDS
from app.plugins.languages.ceb.handbook_spelling import SpellingLexicon, convert_text, handbook_spelling
from app.plugins.languages.ceb.wolff_dictionary import Entry, Form, decode_classes, strip_marks

LEXICON_PATH = Path(__file__).parent / "data" / "wolff_lexicon.json.gz"
RELATIONS_PATH = Path(__file__).parent / "data" / "english_relations.json.gz"
CITATION = "Wolff, <i>A Dictionary of Cebuano Visayan</i> (1972), in its own spelling"
FIELD_NAMES = DICTIONARY_FIELDS
_POS_NAMES = {"n": "noun", "v": "verb", "a": "adjective"}
_MEANING_SENSES = 6
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


# --- reading it -------------------------------------------------------------------------------


def letters_key(word: str) -> str:
    """What a card's word and the dictionary's share: no marks or hyphen, e/o as i/u."""
    w = strip_marks(word).lower().replace("-", "")
    w = w.replace("e", "i").replace("o", "u")
    # Wolff writes one consonant + y/w as Cy, Cw (§3.24: syudad); cards write siyudad
    return re.sub(r"(?<=[^aiu])(?:iy|uw)(?=[aiu])", lambda m: m.group()[1], w)


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


class _Hit(NamedTuple):
    entry: int
    form: int
    surface: str  # the card word's own Handbook spelling (a short form's, if it came through one)
    via: tuple[int, int] | None  # the pointer it came through ('náa = anáa', a short form)
    english_matched: bool = True  # False: the only candidate, taken on its letters


class WolffLexicon:
    def __init__(self, entries: Sequence[dict], relations: EnglishRelations | None = None):
        self.entries = list(entries)
        self.relations = relations if relations is not None else EnglishRelations.empty()
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

    def _resolve(self, ei: int, fi: int, word_key: str) -> tuple[int, int] | None:
        """Follow a form's '= x' / 'see x' / 'short for x' to the form it points at."""
        xref = self.entries[ei]["forms"][fi]["xref"]
        if not xref or self.entries[ei]["forms"][fi]["senses"]:
            return None
        target = strip_marks(re.split(r"[ ,]", xref[1])[0]).lower().split("_")[0]
        for tei in self._heads.get(target, []):
            forms = self.entries[tei]["forms"]
            inside = [i for i, f in enumerate(forms) if f["wolff"] and letters_key(f["wolff"]) == word_key]
            return tei, (inside[0] if xref[0] == "see" and inside else 0)
        return None

    def _hits(self, word: str) -> list[_Hit]:
        key = letters_key(word)
        hits: dict[tuple[int, int], _Hit] = {}
        for ei, fi, short_hb in self._index.get(key, []):
            surface = short_hb or self.entries[ei]["forms"][fi]["hb"]
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

    def _senses(self, hit: _Hit) -> list[dict]:
        return [s for s in self.entries[hit.entry]["forms"][hit.form]["senses"] if not s["phrase"]]

    def match(self, cebuano: str, english: str) -> _Hit | Miss:
        word = cebuano.split(",")[0].strip()
        hits = self._hits(word) if word and " " not in word else []
        if not hits:
            return Miss("not-in-dictionary", ())
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
        if len({h.surface for h in top}) > 1:
            return Miss("ambiguous", tuple(dict.fromkeys(h.surface for h in top)))
        return top[0]

    def card_fields(self, cebuano: str, english: str) -> CardFields | Miss:
        hit = self.match(cebuano, english)
        if isinstance(hit, Miss):
            return hit
        return CardFields(
            spelling=hit.surface,
            meaning=self._meaning(hit),
            verb=self._verb(hit, _words(english)),
            details=self._details(hit, cebuano),
            dictionary_entry=self._dictionary_entry(hit),
        )

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
        # A pointer is a short form ('náa = anáa') only when what it points at
        # is spelled differently. 'maáyu see áyu' lands on the sub-entry màáyo,
        # the card's own spelling, which the root line below already shows.
        if hit.via is not None and form["hb"] != hit.surface:
            out.append(f"<div>Short form of <b>{html.escape(form['hb'] or '')}</b></div>")
        if hit.form != 0 and rec["forms"][0]["hb"]:
            root = rec["forms"][0]
            gloss = next((s["gloss"] for s in root["senses"] if s["gloss"]), "")
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
        others = [h for h in self._hits(cebuano.split(",")[0].strip()) if (h.entry, h.form) != (hit.entry, hit.form)]
        if others:
            items = "".join(
                f"<li><b>{html.escape(h.surface)}</b> "
                f"{html.escape(next((s['gloss'] for s in self._senses(h) if s['gloss']), ''))}</li>"
                for h in others
            )
            out.append('<div class="wolff-h">Not to be confused with</div><ul>' + items + "</ul>")
        return "".join(out)

    def _sense_list(self, form: dict) -> str:
        out, pos, items = [], None, []

        def flush():
            if items:
                label = _POS_NAMES.get(pos or "", pos or "")
                out.append(f'<div class="wolff-pos">{html.escape(label)}</div><ol>' + "".join(items) + "</ol>")
                items.clear()

        for s in form["senses"]:
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
        return WolffLexicon(json.load(fh)["entries"], EnglishRelations.load())
