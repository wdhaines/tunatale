"""The one answer to "is this word DRAWN rather than searched for, and how?".

Three families of word have a picture that is right by construction: numbers
(``app.cards.number_picture`` — a heap, a clock, coins and bills), spatial words
(``app.cards.spatial_picture`` — a box and a ball) and personal pronouns
(``app.cards.pronoun_picture`` — a conversation with the referent filled). Every
path that gives a word its image asks here FIRST — the add-time fetch, the sync
that mints a new card, the production pre-stage, the mint's closed-class fork and
the redraw repair — so adding a family is one line here rather than five call
sites.

A number is checked first and ignores the gloss: its value is its meaning. A
spatial word or a pronoun needs the gloss to confirm its sense (see the guard in
each), because both families contain homographs the router cannot see.
"""

from __future__ import annotations

from app.cards.number_picture import NumberPicture, number_picture
from app.cards.pronoun_picture import pronoun_picture
from app.cards.spatial_picture import spatial_picture


def drawn_picture(text: str, language_code: str, gloss: str) -> NumberPicture | None:
    """The drawn picture for *text*, or ``None`` to take the route it was on."""
    return (
        number_picture(text, language_code)
        or spatial_picture(text, language_code, gloss)
        or pronoun_picture(text, language_code, gloss)
    )
