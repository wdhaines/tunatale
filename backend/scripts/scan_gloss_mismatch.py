#!/usr/bin/env python3
"""Deterministic gloss-mismatch scanner (m3q-family hunt), read-only.

For every stored lesson, compare the generator's IN-CONTEXT gloss
(``generation_metadata.token_glosses``, written at story-authoring time)
against the ANKI CARD gloss (``collocations.translation``) for each surface
that resolves to a card. A mismatch is a card whose stored translation does
not say what the word means in this lesson — the ``tunatale-m3q`` class
(German/Greek ``gangen`` resolved to the homographic card instead of the
story's ``time`` meaning).

The naive rule — flag when the stopword-filtered English word bags of the two
glosses are disjoint — drowns in morphological noise (``gikk`` card='go'
gen='went', ``fant`` card='find' gen='found'). The discriminator collapses
English inflection (irregular-verb map + irregular plurals + articles + a
light suffix stemmer) before comparing, so genuine disagreements survive and
noise does not.

Corpus definition (deliberate, measured): for each lesson in
``created_at`` order, for every surface token of every NATURAL_SPEED L2
phrase, resolve the surface to a card by the transcript's own resolution
order — (1) exact-surface inflection cloze, (2) base row by lemma, (3)
spelling-variant list, (4) the deck's Inflections table — then require an
in-context gloss for the surface or its lemma. Collocations 3113-3116 (the
m3q fix) are excluded by id so the deck is scanned as it was before m3q.
This is a per-OCCURRENCE corpus (the m3q recall oracle counts 9 ``gang``
tokens across days 6-9, and a dict-keys walk sees only 8).

Read-only by design: the DB is opened with a ``mode=ro`` URI and nothing is
ever written. The output is a review list for a human; nothing is edited.

Usage::

    uv run python backend/scripts/scan_gloss_mismatch.py [--db PATH]

Exit 0 = scanned; the summary and review list go to stdout.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

from app.cards.cloze_source import parse_inflection_forms
from app.cards.field_map import inflection_labels
from app.languages import card_surface_variants, get_variant_separator
from app.models.lesson import Lesson, SectionType
from app.models.syntactic_unit import deserialize_extras
from app.srs.tokenizer import tokenize

# The m3q-fix collocations, excluded so the scan sees the deck as it was
# before the fix. Multi-word rows could not match a single surface anyway
# (their lemma column is unset), but the exclusion is explicit and faithful
# to the recall oracle.
EXCLUDED_COLLOCATION_IDS = {3113, 3114, 3115, 3116}

_DEFAULT_DB = Path(__file__).resolve().parent.parent / "tunatale_no.db"

_WORD_RE = re.compile(r"[a-z']+")

# Naive control's stopword filter: core English function words. It is a
# definition of ours (the pinned 28.6% / 384-of-1341 control turned out to
# depend on an unrecorded corpus definition and is not reconstructable; the
# control here is the SAME naive rule measured on the SAME corpus as the
# discriminator, so the comparison is valid).
_NAIVE_STOPWORDS = frozenset(
    [
        "the",
        "a",
        "an",
        "and",
        "or",
        "but",
        "nor",
        "so",
        "yet",
        "if",
        "then",
        "else",
        "when",
        "while",
        "where",
        "which",
        "who",
        "whom",
        "whose",
        "of",
        "in",
        "on",
        "at",
        "by",
        "with",
        "without",
        "from",
        "to",
        "for",
        "against",
        "between",
        "into",
        "through",
        "during",
        "before",
        "after",
        "about",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "am",
        "do",
        "does",
        "did",
        "done",
        "doing",
        "have",
        "has",
        "had",
        "having",
        "i",
        "you",
        "he",
        "she",
        "it",
        "we",
        "they",
        "me",
        "him",
        "her",
        "us",
        "them",
        "my",
        "your",
        "his",
        "its",
        "our",
        "their",
        "mine",
        "yours",
        "hers",
        "ours",
        "theirs",
        "this",
        "that",
        "these",
        "those",
        "there",
        "here",
        "what",
        "why",
        "how",
        "not",
        "no",
        "yes",
    ]
)

# Definiteness collapse: the article is subtracted before comparing, so
# card='the hall' vs gen='in the hall' agree. Possessives are too load-bearing
# to drop on sight.
_DETERMINERS = frozenset({"the", "a", "an"})

# Irregular English verbs — present/preterite/participle collapsed to the
# citation form so `gikk` card='go' gen='went' and `lå` card='lie' gen='lay'
# do not flag (the named noise class).
_IRREGULAR_VERBS = {
    "am": "be",
    "is": "be",
    "are": "be",
    "was": "be",
    "were": "be",
    "been": "be",
    "being": "be",
    "went": "go",
    "gone": "go",
    "goes": "go",
    "going": "go",
    "found": "find",
    "finds": "find",
    "finding": "find",
    "became": "become",
    "becomes": "become",
    "becoming": "become",
    "sat": "sit",
    "sits": "sit",
    "sitting": "sit",
    "lay": "lie",
    "lain": "lie",
    "lying": "lie",
    "lies": "lie",
    "took": "take",
    "taken": "take",
    "takes": "take",
    "taking": "take",
    "gave": "give",
    "given": "give",
    "gives": "give",
    "giving": "give",
    "came": "come",
    "comes": "come",
    "coming": "come",
    "ran": "run",
    "runs": "run",
    "running": "run",
    "saw": "see",
    "seen": "see",
    "sees": "see",
    "seeing": "see",
    "said": "say",
    "says": "say",
    "saying": "say",
    "made": "make",
    "makes": "make",
    "making": "make",
    "had": "have",
    "has": "have",
    "having": "have",
    "did": "do",
    "does": "do",
    "done": "do",
    "doing": "do",
    "got": "get",
    "gotten": "get",
    "gets": "get",
    "getting": "get",
    "knew": "know",
    "known": "know",
    "knows": "know",
    "knowing": "know",
    "thought": "think",
    "thinks": "think",
    "thinking": "think",
    "wrote": "write",
    "written": "write",
    "writes": "write",
    "writing": "write",
    "held": "hold",
    "holds": "hold",
    "holding": "hold",
    "kept": "keep",
    "keeps": "keep",
    "keeping": "keep",
    "felt": "feel",
    "feels": "feel",
    "feeling": "feel",
    "left": "leave",
    "leaves": "leave",
    "leaving": "leave",
    "lost": "lose",
    "loses": "lose",
    "losing": "lose",
    "met": "meet",
    "meets": "meet",
    "meeting": "meet",
    "paid": "pay",
    "pays": "pay",
    "paying": "pay",
    "spent": "spend",
    "spends": "spend",
    "spending": "spend",
    "sent": "send",
    "sends": "send",
    "sending": "send",
    "stood": "stand",
    "stands": "stand",
    "standing": "stand",
    "understood": "understand",
    "understands": "understand",
    "understanding": "understand",
    "began": "begin",
    "begun": "begin",
    "begins": "begin",
    "beginning": "begin",
    "spoke": "speak",
    "spoken": "speak",
    "speaks": "speak",
    "speaking": "speak",
    "broke": "break",
    "broken": "break",
    "breaks": "break",
    "breaking": "break",
    "drove": "drive",
    "driven": "drive",
    "drives": "drive",
    "driving": "drive",
    "ate": "eat",
    "eaten": "eat",
    "eats": "eat",
    "eating": "eat",
    "fell": "fall",
    "fallen": "fall",
    "falls": "fall",
    "falling": "fall",
    "heard": "hear",
    "hears": "hear",
    "hearing": "hear",
    "slept": "sleep",
    "sleeps": "sleep",
    "sleeping": "sleep",
    "led": "lead",
    "leads": "lead",
    "leading": "lead",
    "blew": "blow",
    "blown": "blow",
    "blows": "blow",
    "blowing": "blow",
    "told": "tell",
    "tells": "tell",
    "telling": "tell",
    "sold": "sell",
    "sells": "sell",
    "selling": "sell",
    "caught": "catch",
    "catches": "catch",
    "catching": "catch",
    "bought": "buy",
    "buys": "buy",
    "buying": "buy",
    "brought": "bring",
    "brings": "bring",
    "bringing": "bring",
    "taught": "teach",
    "teaches": "teach",
    "teaching": "teach",
    "built": "build",
    "builds": "build",
    "building": "build",
    "fought": "fight",
    "fights": "fight",
    "fighting": "fight",
    "won": "win",
    "wins": "win",
    "winning": "win",
    "wore": "wear",
    "worn": "wear",
    "wears": "wear",
    "wearing": "wear",
    "forgot": "forget",
    "forgotten": "forget",
    "forgets": "forget",
    "forgetting": "forget",
    "froze": "freeze",
    "frozen": "freeze",
    "freezes": "freeze",
    "freezing": "freeze",
    "chose": "choose",
    "chosen": "choose",
    "chooses": "choose",
    "choosing": "choose",
    "rode": "ride",
    "ridden": "ride",
    "rides": "ride",
    "riding": "ride",
    "rose": "rise",
    "risen": "rise",
    "rises": "rise",
    "rising": "rise",
    "hid": "hide",
    "hidden": "hide",
    "hides": "hide",
    "hiding": "hide",
    "drank": "drink",
    "drunk": "drink",
    "drinks": "drink",
    "drinking": "drink",
    "swam": "swim",
    "swum": "swim",
    "swims": "swim",
    "swimming": "swim",
    "sang": "sing",
    "sung": "sing",
    "sings": "sing",
    "singing": "sing",
    "threw": "throw",
    "thrown": "throw",
    "throws": "throw",
    "throwing": "throw",
    "flew": "fly",
    "flown": "fly",
    "flies": "fly",
    "flying": "fly",
    "grew": "grow",
    "grown": "grow",
    "grows": "grow",
    "growing": "grow",
    "could": "can",
}

# Close derivational pair the generator's glosses actually use; everything a
# step looser than this is synonymy, which is precisely the noise a POS signal
# would NOT fix.
_DERIVED_PAIRS = {"darkness": "dark", "pressure": "press"}

# Irregular plurals, collapsed to the singular citation form.
_IRREGULAR_PLURALS = {
    "people": "person",
    "men": "man",
    "women": "woman",
    "children": "child",
    "feet": "foot",
    "teeth": "tooth",
    "geese": "goose",
    "mice": "mouse",
}


def _candidate_forms(token: str) -> frozenset[str]:
    """Morphological candidates for *token*: the citation form of an irregular
    verb/plural, or the stem plus every plausible suffix-removal variant.

    Multiple candidates are cheap — the comparison only needs ANY pair of
    families to overlap — so ambiguous orthography (``movies`` → ``movie`` via
    ``movie``+``s``, ``candies`` → ``candy`` via ``candi``+``es``) is resolved
    by emitting both readings rather than picking one.
    """
    base = _IRREGULAR_VERBS.get(token, _DERIVED_PAIRS.get(token, _IRREGULAR_PLURALS.get(token)))
    if base is not None:
        return frozenset({base})
    forms = {token}
    if "'" in token:
        return frozenset(forms)
    if token.endswith("ies") and len(token) > 4:
        forms.update({token[:-3] + "y", token[:-1]})
    elif token.endswith("ied") and len(token) > 4:
        forms.add(token[:-3] + "y")
    elif token.endswith("es") and len(token) > 4:
        stem = token[:-2]
        forms.add(stem if stem.endswith(("s", "x", "z", "ch", "sh")) else stem + "e")
        forms.add(token[:-1])
    elif token.endswith("ing") and len(token) >= 6:
        forms.update({token[:-3], token[:-3] + "e"})
    elif token.endswith("ly") and len(token) > 5:
        forms.add(token[:-2])
    elif token.endswith("ed") and len(token) > 4:
        forms.update({token[:-2], token[:-1]})
        base = token[:-2]
        if len(base) > 1 and base[-1] == base[-2]:
            forms.add(base[:-1])
    elif token.endswith("s") and not token.endswith("ss") and len(token) > 3:
        forms.add(token[:-1])
    return frozenset(forms)


def naive_words(gloss: str) -> set[str]:
    """Raw English word bag of *gloss*, stopword-filtered (the naive control)."""
    return {t for t in _WORD_RE.findall(gloss.casefold()) if t not in _NAIVE_STOPWORDS}


def family_words(gloss: str) -> set[str]:
    """Morphology-collapsed English word families of *gloss* (the discriminator)."""
    out: set[str] = set()
    for t in _WORD_RE.findall(gloss.casefold()):
        if t in _DETERMINERS:
            continue
        out |= _candidate_forms(t)
    return out


def naive_flag(card_gloss: str, gen_gloss: str) -> bool:
    """Naive rule: stopword-filtered word bags are disjoint."""
    return naive_words(card_gloss).isdisjoint(naive_words(gen_gloss))


def discriminator_flag(card_gloss: str, gen_gloss: str) -> bool:
    """Discriminator rule: morphology-collapsed word bags are disjoint."""
    return family_words(card_gloss).isdisjoint(family_words(gen_gloss))


def is_gang_surface(surface: str) -> bool:
    """True for the m3q recall token class (card 1789 'gang'='hall')."""
    return surface.casefold() in {"gang", "gangen"}


@dataclass(frozen=True)
class Card:
    id: int
    text: str
    gloss: str
    lemma: str | None
    card_type: str
    inflection_forms: tuple[str, ...]


@dataclass(frozen=True)
class Triple:
    surface: str
    day: int
    card: Card
    gen_gloss: str


class CardResolver:
    """Resolves a surface to its card in the transcript's own order.

    Mirrors ``app.srs.transcript``: (1) exact-surface morphology cloze,
    (2) base row by lemma, (3) spelling-variant list, (4) the deck's
    Inflections table. Forms claimed by more than one card are dropped, not
    arbitrated, exactly as ``_build_inflection_index`` does.
    """

    def __init__(self, cards: list[Card], language_code: str) -> None:
        self._cloze_by_surface: dict[str, Card] = {}
        self._lemma_by_key: dict[str, Card] = {}
        self._variant_by_surface: dict[str, Card] = {}
        self._inflection_by_surface: dict[str, Card] = {}

        cloze_claims: dict[str, set[int]] = {}
        for card in cards:
            if card.card_type == "cloze" and card.lemma:
                cloze_claims.setdefault(card.lemma.casefold(), set()).add(card.id)
        for lemma, ids in cloze_claims.items():
            if len(ids) != 1:
                continue
            winner = next(c for c in cards if c.id == next(iter(ids)))
            self._cloze_by_surface[lemma] = winner

        for card in cards:
            if card.lemma and card.card_type != "cloze" and card.lemma not in self._lemma_by_key:
                self._lemma_by_key[card.lemma.casefold()] = card

        sep = get_variant_separator(language_code)
        if sep:
            for card in cards:
                if card.card_type == "cloze":
                    continue
                variants = card_surface_variants(language_code, card.text)
                if len(variants) <= 1:
                    continue
                for variant in variants:
                    self._variant_by_surface.setdefault(variant.casefold(), card)

        claims: dict[str, set[int]] = {}
        for card in cards:
            for form in card.inflection_forms:
                claims.setdefault(form.casefold(), set()).add(card.id)
        for form, ids in claims.items():
            if len(ids) == 1:
                self._inflection_by_surface[form] = next(c for c in cards if c.id == next(iter(ids)))

    def resolve(self, surface: str, lemma: str) -> Card | None:
        key = lemma
        if key in self._cloze_by_surface:
            return self._cloze_by_surface[key]
        if key in self._lemma_by_key:
            return self._lemma_by_key[key]
        if surface.casefold() in self._variant_by_surface:
            return self._variant_by_surface[surface.casefold()]
        if surface.casefold() in self._inflection_by_surface:
            return self._inflection_by_surface[surface.casefold()]
        return None


def load_cards(conn: sqlite3.Connection, language_code: str, excluded_ids: set[int]) -> list[Card]:
    """Load the deck's collocations for *language_code*, minus *excluded_ids*."""
    conn.row_factory = sqlite3.Row
    cards: list[Card] = []
    for row in conn.execute(
        "SELECT id, text, translation, lemma, card_type, extras FROM collocations WHERE language_code = ?",
        (language_code,),
    ):
        if row["id"] in excluded_ids:
            continue
        forms: tuple[str, ...] = ()
        if row["extras"]:
            for label in inflection_labels():
                html = next(
                    (e.html for e in deserialize_extras(row["extras"]) if e.label == label),
                    "",
                )
                forms += tuple(parse_inflection_forms(html))
        cards.append(
            Card(
                id=int(row["id"]),
                text=row["text"],
                gloss=row["translation"] or "",
                lemma=row["lemma"],
                card_type=row["card_type"],
                inflection_forms=tuple(dict.fromkeys(f.casefold() for f in forms)),
            )
        )
    return cards


def build_triples(
    conn: sqlite3.Connection,
    cards: list[Card],
    language_code: str,
) -> list[Triple]:
    """Run the occurrence walk over all lessons (see module docstring)."""
    conn.row_factory = sqlite3.Row
    resolver = CardResolver(cards, language_code)
    triples: list[Triple] = []
    for lesson_row in conn.execute("SELECT day, data_json FROM lessons ORDER BY created_at, id"):
        lesson = Lesson.from_json(lesson_row["data_json"])
        if lesson.language_code != language_code:
            continue
        gloss_map = (lesson.generation_metadata or {}).get("token_glosses", {})
        for section in lesson.sections:
            if section.section_type is not SectionType.NATURAL_SPEED:
                continue
            for phrase in section.phrases:
                if phrase.language_code != language_code:
                    continue
                for surface in tokenize(phrase.text):
                    lemma = surface.casefold()
                    card = resolver.resolve(surface, lemma)
                    if card is None:
                        continue
                    gen = gloss_map.get(surface.lower()) or gloss_map.get(lemma)
                    if not gen:
                        continue
                    triples.append(Triple(surface=surface, day=int(lesson_row["day"]), card=card, gen_gloss=gen))
    return triples


def open_read_only(db_path: Path) -> sqlite3.Connection:
    """Open *db_path* read-only; nothing in this script may ever write."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def gang_recall(triples: list[Triple]) -> tuple[int, int]:
    """(flagged, total) of the m3q recall class among *triples*."""
    total = flag = 0
    for t in triples:
        if is_gang_surface(t.surface):
            total += 1
            if discriminator_flag(t.card.gloss, t.gen_gloss):
                flag += 1
    return flag, total


def summarize(triples: list[Triple]) -> None:
    """Print the summary + deduplicated review list (see module docstring)."""
    naive = [t for t in triples if naive_flag(t.card.gloss, t.gen_gloss)]
    flagged = [t for t in triples if discriminator_flag(t.card.gloss, t.gen_gloss)]
    g_flag, g_total = gang_recall(triples)

    print(f"corpus: {len(triples)} triples (occurrence walk, natural-speed L2, lowercase lemmatizer)")
    print(f"naive (stopword-filtered disjoint word bags): {len(naive)} flagged ({len(naive) / len(triples):.1%})")
    print(
        f"discriminator (morphology-collapsed): {len(flagged)} flagged"
        f" ({len(flagged) / len(triples):.1%}); collapse removed {len(naive) - len(flagged)}"
    )
    print(f"gang recall (days 6-9, collocations 3113-3116 excluded): {g_flag}/{g_total}")

    if not flagged:
        return
    print("\nreview list (deduplicated by surface; occ = occurrences, flags = flagged):")
    by_surface: dict[str, list[Triple]] = {}
    for t in flagged:
        by_surface.setdefault(t.surface.casefold(), []).append(t)
    for surface, row_triples in sorted(by_surface.items()):
        seen: set[tuple[int, int, str, str]] = set()
        for t in row_triples:
            seen.add((t.day, t.card.id, t.card.gloss, t.gen_gloss))
        total = sum(1 for x in triples if x.surface.casefold() == surface)
        details = "; ".join(f"d{days} #{cid} {card!r}→{gen!r}" for days, cid, card, gen in seen)
        print(f"  {surface} (occ {len(row_triples)}/{total}): {details}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scan stored lessons for in-context gloss vs card-gloss mismatches.")
    parser.add_argument("--db", type=Path, default=_DEFAULT_DB, help="read-only SQLite deck path")
    parser.add_argument("--language-code", default="no", help="deck language to scan")
    args = parser.parse_args(argv)

    if not args.db.exists():
        print(f"FAIL: no such deck: {args.db}", file=sys.stderr)
        return 1
    conn = open_read_only(args.db)
    try:
        cards = load_cards(conn, args.language_code, EXCLUDED_COLLOCATION_IDS)
        triples = build_triples(conn, cards, args.language_code)
    finally:
        conn.close()
    if not triples:
        print("no triples — nothing to compare", file=sys.stderr)
        return 1
    summarize(triples)
    return 0


if __name__ == "__main__":
    sys.exit(main())
