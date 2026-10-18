"""A card's dictionary layers as TT extras, for a deck that has no Anki behind it.

The owner's cards get the layers through Anki: the note's dictionary fields
(``vocab_notetype.DICTIONARY_FIELDS``), then the pull's ``extract_back_fields``.
A learner with no Anki has no note to pull from, so TT builds the same extras
itself, through the same profile, so a card shows the same thing either way
(tunatale-h0q3.2).
"""

from __future__ import annotations

from app.cards.field_map import get_profile
from app.languages import dictionary_note_fields, get_vocab_notetype
from app.models.syntactic_unit import BackField


def dictionary_extras(code: str, word: str, english: str) -> tuple[BackField, ...]:
    """The extras the pull would make for *word* / *english*; ``()`` when there are none."""
    vocab = get_vocab_notetype(code)
    profile = get_profile(vocab.name, code) if vocab is not None and vocab.dictionary else None
    if profile is None:
        return ()
    fields = dictionary_note_fields(code, word, english)
    return tuple(
        BackField(label=spec.label, html=fields[spec.field_name], tier=spec.tier)
        for spec in profile.back_fields
        if fields.get(spec.field_name)
    )
