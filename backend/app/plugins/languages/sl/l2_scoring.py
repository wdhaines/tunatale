"""Which characters mark a field as Slovene rather than English."""

from __future__ import annotations

from app.cards.l2_scoring import make_l2_scorer

#: Slovene-specific letters plus the dictionary stress diacritics that appear in
#: pronunciation hints (besêda, oblákov). These are the marks that distinguish
#: Slovene text from an English gloss on the same card.
SLOVENE_L2_CHARS = frozenset("čšžđćČŠŽĐĆáàâäéèêëíìîïóòôöúùûüŕÁÀÂÄÉÈÊËÍÌÎÏÓÒÔÖÚÙÛÜŔ")

score_slovene_l2 = make_l2_scorer(SLOVENE_L2_CHARS)
