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

from app.plugins.languages.no.norwegian_breakdown import _load_ranked_lexicon

# Longest first: -ene must win over -en, and -et over -t.
_DEFINITE_SUFFIXES: tuple[str, ...] = ("ene", "ane", "et", "en", "a")

# NOTE: the e-final short forms (sete → sete+t, hage → hage+n) need no separate
# rule. A stem ending in `e` plus `t` always spells `-et`, and plus `n` always
# spells `-en`, so the suffixes above already match them; the stem-length floor
# is what then separates `setet` (stem `set`, definite) from `set` (stem `s`,
# too short to be a noun — and indeed `set` is not a word, it is the truncated
# lemma Stanza returned for `Setet`).
_MIN_STEM = 2

# A lemma is only trusted as a real word when it ranks inside the top 40k of
# the bundled 50k wordlist. Real inflected→lemma relations (morder=17102,
# jordbær=7550, set=6184) sit far under the floor; Stanza's truncated fragments
# sit at the noise tail (trø=49800), with a clean empty gap between. The floor
# mirrors _MAX_STEM_RANK's argument (norwegian_breakdown.py): a frequency floor
# in the gap separates real words from junk with no hand-maintained blocklist.
_MAX_PLAUSIBLE_RANK = 40000


def is_lemma_plausible(surface: str, lemma: str) -> bool:
    """True when *lemma* is a plausible headword for *surface*.

    Stanza occasionally strips an inflectional ending that isn't there and
    returns a fragment that is a prefix of the surface (`trøtt` → `trø`). The
    shape to suspect is a full trailing doubled-consonant drop: Norwegian
    inflections append (`stor` → `store`) and the neuter -t merely doubles the
    final consonant (`søtt` → `søt`), so a whole pair vanishing is rare — the
    legitimate `nytt` → `ny` is the exception. A full-pair drop is therefore
    accepted only when the lemma is itself a common word (rank ≤
    ``_MAX_PLAUSIBLE_RANK``): that separates the real `nytt` → `ny` (rank 195)
    from the fragment `trøtt` → `trø` (rank 49800).

    Deliberately narrow outside that signature: a lemma that is not a truncation
    of the surface is always accepted. Neither error is free, so the rule fires
    only where the evidence is strong. Rejecting makes callers key the card on
    the surface as it appeared, so a false *positive* mints a card for a
    non-word, while a false *negative* mints one on an inflected form (`trøtt`
    rather than a dictionary headword) — wrong shape, but a real word from the
    sentence, and recoverable by editing the card. Note this is NOT "leave
    today's behaviour alone": today's behaviour is the lemma, so a rejection
    always changes the headword.

    Consequence worth stating plainly: truncations that do NOT take the
    doubled-consonant shape still get through. `setet` → `set` is the known
    example, and it is out of scope by decision, not oversight — see
    tunatale-s7f.2.
    """
    w = lemma.casefold()
    s = surface.casefold()
    if w == s or not s.startswith(w) or len(s) - len(w) < 2:
        return True
    tail = s[len(w) :]
    if tail[0] != tail[1]:
        return True
    rank = _load_ranked_lexicon().get(w)
    return rank is not None and rank <= _MAX_PLAUSIBLE_RANK


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
