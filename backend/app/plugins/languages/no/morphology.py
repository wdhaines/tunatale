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

import contextlib
from collections.abc import Iterator
from pathlib import Path

from app.plugins.languages.no.lexicon import DB_PATH, NstLexicon, nst_lexicon_installed
from app.plugins.languages.no.norwegian_breakdown import _INFLECTIONS, _load_ranked_lexicon

#: Appended to a lemma to ask "is a real word one character away?". Order is the
#: order they are tried; the first hit rejects.
_NEIGHBOUR_SUFFIXES: tuple[str, ...] = ("e", "n", "t")


@contextlib.contextmanager
def _open_nst(db_path: Path | None = None) -> Iterator[NstLexicon | None]:
    """Yield a lexicon, or ``None`` when the database has not been built.

    ⚠️ Opened and closed PER CALL rather than cached in a module-level singleton.
    A cached one cannot survive this repo's test isolation: conftest's autouse
    ``_autoclose_sqlite_connections`` closes every connection a test opens,
    directly rather than through ``NstLexicon.close()``, so a cached object keeps
    a non-None handle to a dead connection and every later caller gets "Cannot
    operate on a closed database". Closing here also satisfies tunatale-a5p2,
    whose warning was about NOT closing. MEASURED: 0.12 ms per call, 24 ms across
    a 208-lemma lesson.

    ``db_path`` exists so the not-built path is reachable with a real absent file
    instead of a patch — the mock-boundary checker rejects
    ``patch("app.…nst_lexicon_installed")``, correctly: a capability gate is
    testable by handing it an absent capability.
    """
    path = db_path if db_path is not None else DB_PATH
    if not nst_lexicon_installed(path):
        yield None
        return
    lexicon = NstLexicon(path)
    try:
        yield lexicon
    finally:
        lexicon.close()


def _better_lemma_one_char_away(word: str, lexicon: NstLexicon | None) -> bool:
    """True when *word* is absent from NST but a one-char extension of it is there.

    The signature of a truncated lemma: `mapp` is not a word, `mappe` is.

    ⚠️ BOTH halves are required, and the second is what makes this usable. A bare
    "absent from NST" screen false-alarms on COMPOUNDS — `vårkonsert`,
    `billettautomat`, `åttitall` are real indefinite singulars a fixed
    pronunciation lexicon cannot enumerate, because Norwegian compounding is
    productive. Those have no neighbour either, so requiring one exempts the whole
    class. Measured over the live `lemma_analysis_cache`: 8 artifacts caught, zero
    compounds touched.

    ``lexicon`` of ``None`` (not built) returns False — never a guess.
    """
    if lexicon is None or lexicon.all_transcriptions(word):
        return False
    return any(lexicon.all_transcriptions(word + suffix) for suffix in _NEIGHBOUR_SUFFIXES)


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

    ⚠️ "A lemma that is not a truncation of the surface is always accepted" was
    true until tunatale-q1ir and is NOT any more: ``_better_lemma_one_char_away``
    runs first and rejects `rør` -> `rure`, which is not a truncation of anything.
    It also overrides the sub-2-character drop that `gluten` -> `glute` used to
    slip through. Outside those, the rule below is still deliberately narrow.
    Neither error is free, so the rule fires
    only where the evidence is strong. Rejecting makes callers key the card on
    the surface as it appeared, so a false *positive* mints a card for a
    non-word, while a false *negative* mints one on an inflected form (`trøtt`
    rather than a dictionary headword) — wrong shape, but a real word from the
    sentence, and recoverable by editing the card. Note this is NOT "leave
    today's behaviour alone": today's behaviour is the lemma, so a rejection
    always changes the headword.

    Widened for tunatale-wum6. The rule was once "a full trailing
    doubled-consonant drop", which could not see `snømenn` → `snøm`: the
    dropped tail is `enn`, not a doubled pair, so it returned True before ever
    reaching the common-word gate. The signature is now the ENDING itself — a
    truncation whose lemma is not a common word is implausible unless what was
    peeled is a real Norwegian inflection. Measured over 1264 (surface, lemma)
    pairs from the live `lemma_analysis_cache`: exactly ONE flips to reject
    (`snømenn` → `snøm`, the bug) and NOTHING regresses the other way.

    Consequence worth stating plainly: `setet` → `set` still gets through, and
    still by decision rather than oversight — `set` ranks 6184, inside the
    common-word band, so the first gate accepts it. See tunatale-s7f.2, whose
    whole acceptance table this function still satisfies.
    """
    w = lemma.casefold()
    s = surface.casefold()
    if not w or w == s:
        return True
    # ⚠️ MUST run before the early return below. Two of the three shapes this
    # catches ARE that early return: `gluten` -> `glute` is a one-character drop,
    # and `rure` is not a prefix of `rør` at all.
    with _open_nst() as lexicon:
        if _better_lemma_one_char_away(w, lexicon):
            return False
    if not s.startswith(w) or len(s) - len(w) < 2:
        return True
    rank = _load_ranked_lexicon().get(w)
    if rank is not None and rank <= _MAX_PLAUSIBLE_RANK:
        return True
    # Not a common word, so the peeled tail has to carry the evidence. A real
    # lemmatization peels a real ENDING: `en`, `et`, `ene`, `er`, `t`. Stanza's
    # fragments peel something that is not an ending at all — `snømenn` -> `snøm`
    # drops `enn`, which is no Norwegian inflection (tunatale-wum6).
    tail = s[len(w) :]
    # Stem geminate: a stem doubles its final consonant before a suffix
    # (`rom` -> `rommet`), so the raw tail reads `met`. Peel the doubled
    # consonant back off before judging the ending. Without this step
    # `avhørsrommet` -> `avhørsrom` is the one legitimate pair the widening
    # breaks — measured over the live cache, not hypothesised.
    if tail[0] == w[-1]:
        tail = tail[1:]
    return tail in _INFLECTIONS


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
