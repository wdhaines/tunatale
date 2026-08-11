"""Reduce a verb card's English gloss to its bare dictionary form.

The ``å lyve`` bug: the bulk auto-create-from-lesson path
(``app.api.srs.mark_lesson_listened``) builds the card front from the lemma —
``å lyve`` — but takes the back from ``token_glosses``, which hold the
*in-context* meaning of the surface ``lyver`` in "Noen lyver" — "Someone is
lying". The back read "is lying" and contradicted its own front. The click
path (``create_base_card``) avoids this by re-glossing VERB lemmas through
``generate_word_gloss``; this module is the cheap, LLM-free fallback for
lessons generated before that map exists (i.e. every lesson in the DB today).

The transformation is English-side, so it is deliberately NOT
registry-dispatched: every L2 whose verb cards front with a dictionary form
wants the same treatment — Slovene's ``pokazati`` → "show" is the identical
defect.

Direction matters, as it does for ``gloss_definiteness.py``: only ever REMOVE
conjugation framing the headword cannot support — a leading "to", subject
pronouns and auxiliaries, then ``-ing``/``-s`` inflection. A form the rules do
not recognise passes through unchanged, degrading to today's behaviour rather
than to a confidently wrong card ("wrought havoc" stays "wrought havoc"; "a
building" is a noun gloss, not a verb). Candidate base forms are confirmed
against wordfreq so the rules only fire when they can name a real word —
"billing" → "bill" is kept, never collapsed to "bil".
"""

from __future__ import annotations

# Leading framing that carries no verb meaning of its own, each with its
# trailing space so a prefix match cannot eat a word ("is" ≠ "island").
_TO = "to "
_PRONOUNS = ("he ", "she ", "it ", "they ", "we ", "you ", "i ")
_AUXILIARIES = (
    "is ",
    "am ",
    "are ",
    "was ",
    "were ",
    "be ",
    "been ",
    "being ",
    "has ",
    "have ",
    "had ",
    "do ",
    "does ",
    "did ",
    "will ",
    "would ",
    "shall ",
    "should ",
    "can ",
    "could ",
    "may ",
    "might ",
    "must ",
)
_ARTICLES = ("a ", "an ", "the ")

# Finite and -ing forms the structural rules cannot recover: the -ie → -y
# verbs ("lying" → "lie"), the rare doubled-consonant base ("fibbing" → "fib",
# whose base sits below the wordfreq floor), and the finite forms of be/have/do
# that the -s/-ing rules would either mangle or miss.
_IRREGULARS = {
    "is": "be",
    "was": "be",
    "has": "have",
    "does": "do",
    "lying": "lie",
    "dying": "die",
    "tying": "tie",
    "vying": "vie",
    "fibbing": "fib",
}

# wordfreq membership floor: a candidate base must clear this to count as an
# English word, so the -ing/-s rules only fire on forms they can confirm.
#
# ⚠️ It does NOT cleanly separate real words from noise, and cannot — measured
# 2026-08-11, the two populations interleave:
#
#     dig   1.66e-05   real
#     swim  1.62e-05   real
#     th    1.58e-05   NOISE, sitting between them
#     ski   1.35e-05   real
#
# So no floor admits `swim`/`dig`/`ski` while rejecting `th`. 3e-05 is chosen to
# sit ABOVE the noise, accepting that a handful of ordinary verbs fall below it:
# a miss is a pass-through (today's behaviour), while admitting "th" would turn
# "thing" into "th" — a card whose back contradicts its front, i.e. the exact
# bug this module exists to fix. Measured hit rate on 20 common verbs: 19/20,
# `swim` the lone miss.
#
# DO NOT lower this to catch a specific missing verb. Add it to _IRREGULARS
# instead — that is what the table is for.
_WORD_FLOOR = 3e-05


def _is_english_word(word: str) -> bool:
    import wordfreq

    return wordfreq.word_frequency(word.casefold(), "en") >= _WORD_FLOOR


def _retitle(original: str, base: str) -> str:
    """Return *base* in the capitalisation *original* arrived with."""
    if original.isupper():
        return base.upper()
    if original[:1].isupper():
        return base[:1].upper() + base[1:]
    return base


def _strip_framing(text: str) -> str:
    """Remove a leading infinitive marker, subject pronoun, or auxiliary."""
    while True:
        lowered = text.casefold()
        if lowered.startswith(_TO):
            text = text[len(_TO) :]
            continue
        for prefix in _PRONOUNS:
            if lowered.startswith(prefix):
                text = text[len(prefix) :]
                break
        else:
            for prefix in _AUXILIARIES:
                if lowered.startswith(prefix):
                    text = text[len(prefix) :]
                    break
            else:
                return text


def _reduce_ing(word: str) -> str:
    stem = word.casefold()[:-3]
    if len(stem) < 2:
        return word
    if stem[-1] == stem[-2] and stem[-1] not in "aeiouy":
        # Doubled final consonant: running -> runn -> run; billing -> bill.
        for candidate in (stem[:-1], stem):
            if _is_english_word(candidate):
                return _retitle(word, candidate)
        return word
    if stem[-1] not in "aeiouy" and stem[-2] in "aeiou":
        # Silent-e restoration: making -> mak -> make; eating -> eat.
        for candidate in (stem + "e", stem):
            if _is_english_word(candidate):
                return _retitle(word, candidate)
        return word
    # Plain -ing: going -> go; thing -> th (not a word, so pass through).
    if _is_english_word(stem):
        return _retitle(word, stem)
    return word


def _reduce_s(word: str) -> str:
    key = word.casefold()
    if key.endswith("ies") and len(key) > 3:
        candidate = key[:-3] + "y"
        if _is_english_word(candidate):
            return _retitle(word, candidate)
    if key.endswith("es") and len(key) > 2:
        candidate = key[:-2]
        if _is_english_word(candidate):
            return _retitle(word, candidate)
    if key.endswith("s") and len(key) > 2:
        candidate = key[:-1]
        if _is_english_word(candidate):
            return _retitle(word, candidate)
    return word


def _reduce_verb_word(word: str) -> str:
    key = word.casefold()
    base = _IRREGULARS.get(key)
    if base is not None:
        return _retitle(word, base)
    if key.endswith("ing"):
        return _reduce_ing(word)
    if key.endswith("s"):
        return _reduce_s(word)
    return word


def _align_alternative(part: str) -> str:
    if not part:
        return part
    lead = part[: len(part) - len(part.lstrip())]
    body = part[len(lead) :]
    trail = body[len(body.rstrip()) :]
    core = body.rstrip()
    if not core:
        return part
    stripped = _strip_framing(body)
    if stripped.casefold().startswith(_ARTICLES):
        return part
    words = stripped.split()
    if not words:
        return part
    reduced = _reduce_verb_word(words[-1])
    if reduced == words[-1] and stripped == core:
        return part
    return lead + " ".join(words[:-1] + [reduced]) + trail


def align_gloss_verb_form(gloss: str) -> str:
    """Reduce *gloss* to its bare English dictionary form.

    Applied per slash-separated alternative, because glosses arrive as
    ``"is lying / is fibbing"`` as often as a single phrase — mirroring
    ``align_gloss_definiteness``. An alternative that is already bare, or that
    the rules cannot confirm, passes through unchanged: only framing the
    headword cannot support is ever removed.
    """
    if not gloss:
        return gloss
    parts = gloss.split("/")
    aligned = [_align_alternative(part) for part in parts]
    if aligned == parts:
        return gloss
    return "/".join(aligned)
