"""Short forms, a first-class concept in Cebuano (tunatale-0rlg, the owner 2026-10-29).

Every word in a short/long pair says so on one line, the card's ``Short form``
field. One rule decides how many cards a pair has: interchangeable forms share a
card (``dili`` and ``di``), and that card names its short form; a short form with
its own grammar has its own card (the pronoun clitics, Wolff, Beginning Cebuano
3 B 4), and that card names the word it shortens, while the long word names it
back.

The pairs come from two tables: ``spelling_variants.tsv`` (``kind`` short or long)
and the pronoun table (``short_forms``). The marks are Wolff's: a pair spelled as
one comma front comes back marked ('dili, di' -> 'dílì, dì').
"""

from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path

from app.cards.pronoun_picture import load_senses, load_short_forms
from app.languages import ShortFormPair

_CODE = "ceb"
_VARIANTS = Path(__file__).parent / "data" / "spelling_variants.tsv"


@lru_cache(maxsize=1)
def short_form_pairs() -> tuple[ShortFormPair, ...]:
    """Every pair: the shared cards' from the spelling table, then the pronouns' own."""
    with open(_VARIANTS, encoding="utf-8") as fh:
        rows = list(csv.DictReader((line for line in fh if not line.startswith("#")), delimiter="\t"))
    shared = [
        ShortFormPair(short=row["variant"], long=row["main"], shares_card=True)
        if row["kind"] == "short"
        else ShortFormPair(short=row["main"], long=row["variant"], shares_card=True)
        for row in rows
        if row["kind"] in ("short", "long")
    ]
    own = [
        ShortFormPair(short=short, long=long, shares_card=False, short_key=short_key, long_key=long_key)
        for short, short_key, long, long_key in load_short_forms(_CODE)
    ]
    return (*shared, *own)


def _marked(pair: ShortFormPair, english: str) -> tuple[str, str]:
    """``(long, short)`` with Wolff's marks; as the table spells them when he does not settle the pair."""
    from app.plugins.languages.ceb.wolff_cards import Miss, load_lexicon

    fields = load_lexicon().card_fields(f"{pair.long}, {pair.short}", english)
    parts = () if isinstance(fields, Miss) else tuple(p.strip() for p in fields.spelling.split(","))
    return (parts[0], parts[1]) if len(parts) == 2 and all(parts) else (pair.long, pair.short)


def short_form_line(word: str, english: str, disambig: str = "") -> str | None:
    """What the card ``(word, disambig)`` says about its partner, or None when it is in no pair.

    A comma front ('dili, di', not yet renamed) is its long form. A key that names
    no second sense, such as a word class a deck put in the field, is the word's
    own sense.
    """
    text = word.split(",")[0].strip().casefold()
    key = disambig.strip()
    if (text, key) not in load_senses(_CODE):
        key = ""
    for pair in short_form_pairs():
        if (pair.long, pair.long_key) == (text, key):
            return f"short form: {_marked(pair, english)[1]}"
    for pair in short_form_pairs():
        if not pair.shares_card and (pair.short, pair.short_key) == (text, key):
            return f"short for {_marked(pair, english)[0]}"
    return None
