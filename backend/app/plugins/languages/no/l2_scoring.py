"""Which characters mark a field as Norwegian rather than English.

⚠️ ``æ ø å`` are the whole point. Before tunatale-yaan there was no Norwegian
scorer at all and the Slovene one was used: ``ø`` and ``å`` were in no set, so
``snøm`` scored 0.0 and lost to an example sentence that scored 0.5 on the
single ``æ`` in ``være`` — ``æ`` counted only because it is also an IPA symbol.
The sentence was then written to Anki as a headword.
"""

from __future__ import annotations

from app.cards.l2_scoring import make_l2_scorer

#: The three Norwegian letters, both cases. Nothing else: Norwegian otherwise
#: shares the English alphabet, so adding accented Latin letters here would score
#: loanwords and English text alike.
NORWEGIAN_L2_CHARS = frozenset("æøåÆØÅ")

score_norwegian_l2 = make_l2_scorer(NORWEGIAN_L2_CHARS)
