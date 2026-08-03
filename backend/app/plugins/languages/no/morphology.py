"""Norwegian inflectional morphology: definiteness + lemma plausibility.

Two small checks that keep a generated vocab card from contradicting itself.
TT builds a card's front from the lemmatizer's lemma and its back from the LLM's
gloss; nothing used to check the two agreed, so `morder` (indefinite) shipped
glossed "the murderer" (definite would be `morderen`).

Bokmål attaches the definite article as a suffix, so definiteness is readable off
the surface:

    hus   → huset      sete → setet     (neuter sg: -et, or -t after an e-stem)
    bil   → bilen      snø  → snøen     (masc sg: -en, or -n after an e-stem)
    jente → jenta                       (fem sg: -a)
    bil   → bilene                      (definite pl: -ene / -ane)

Core reaches both helpers through the registry (``get_definite_form_checker`` /
``get_lemma_plausible``) — never by importing this module, per the
no-hardcoded-language-logic rule.
"""

from __future__ import annotations

# Longest first: -ene must win over -en, and -et over -t.
_DEFINITE_SUFFIXES: tuple[str, ...] = ("ene", "ane", "et", "en", "a")

# NOTE: the e-final short forms (sete → sete+t, hage → hage+n) need no separate
# rule. A stem ending in `e` plus `t` always spells `-et`, and plus `n` always
# spells `-en`, so the suffixes above already match them; the stem-length floor
# is what then separates `setet` (stem `set`, definite) from `set` (stem `s`,
# too short to be a noun — and indeed `set` is not a word, it is the truncated
# lemma Stanza returned for `Setet`).
_MIN_STEM = 2


def is_definite_form(word: str) -> bool:
    """True when *word* carries a Norwegian definite-article suffix.

    Deliberately conservative: a false *negative* only means the gloss aligner
    keeps a leading "the ", i.e. today's behavior, while a false *positive*
    would leave a contradictory card in place. When in doubt, say False.
    """
    w = word.casefold()
    if len(w) < _MIN_STEM + 1:
        return False
    return any(w.endswith(suffix) and len(w) - len(suffix) >= _MIN_STEM for suffix in _DEFINITE_SUFFIXES)
