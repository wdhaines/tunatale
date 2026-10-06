"""An Enunciated line: the natural line, cut into the words a voice pauses between.

The Enunciated sections say each dialogue line one word at a time. How the pause
is ASKED FOR is the provider's business (markup for one, an instruction for the
other; see the adapters), and what both need first is the same: where the words
are, and where inside a long word the language wants a shorter cut. That is this
module, and it runs at RENDER time, so a stored lesson is said the new way after
a re-render and never needs rebuilding.

Decided by the user's ear on 2026-10-06 (tunatale-tyfk), replacing a text
device: the line used to be stored and sent as ``Dober ... dan``, and measured,
" ... " barely separates words on one provider and only sometimes on the other.
Lessons stored that way are still read here (:func:`plan_line`), because a
stored section is a snapshot and nothing rewrites it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.audio.ports import Enunciation
from app.generation.section_builder import _SENTENCE_PUNCTUATION
from app.models.lesson import SectionType

# The sections whose target-language lines are enunciated. Their English lines,
# titles and scene labels are not: nobody is learning to hear those.
ENUNCIATED_SECTIONS = frozenset({SectionType.SLOW_SPEED, SectionType.SLOW_TRANSLATED, SectionType.SLOW_EN_TRANSLATED})

# What a language's slow-word function writes where a word is cut
# (``fly, plassen``). It is that function's return notation and nothing else:
# it is split out here and never reaches a provider as a comma.
PART_SEPARATOR = ", "

# How a line was stored before the cut moved to render time: this between the
# words, and each word already passed through the slow-word function.
_STORED_WORD_SEPARATOR = " ... "


@dataclass(frozen=True)
class EnunciatedLine:
    """One line, ready for a provider.

    ``text`` is the line as written, which is what an instruction-led voice is
    given and what a cache key names. ``words`` is how it is said: each word as
    the parts a short pause separates, one part for a word said whole.
    """

    text: str
    words: Enunciation


def plan_line(text: str, slow_word: Callable[[str], str] | None = None) -> EnunciatedLine | None:
    """Cut *text* into words, and each word where *slow_word* cuts it.

    ``None`` when there is nothing to separate — a single unsplit word, or no
    word at all — so the caller says the line plainly, from the very clip the
    natural-speed section already made.

    A line stored in the old notation is recognised by its `` ... `` and read as
    it was written: a ``, `` inside one of its words is a cut made when it was
    stored, and is kept rather than made again. A stored word with no cut is
    still offered to *slow_word*, because a language may have gained its rule
    after the lesson was stored.

    Punctuation standing alone (a dash between clauses) joins the word beside
    it; given a pause of its own it would be a silence between two silences.
    """
    stored = _STORED_WORD_SEPARATOR in text
    tokens = text.split(_STORED_WORD_SEPARATOR) if stored else text.split()

    natural: list[str] = []
    words: list[tuple[str, ...]] = []
    opening = ""
    for token in (t.strip() for t in tokens):
        if not token:
            continue
        if not any(c.isalnum() for c in token):
            if words:
                natural[-1] = f"{natural[-1]} {token}"
                words[-1] = (*words[-1][:-1], f"{words[-1][-1]} {token}")
            else:
                opening += f"{token} "
            continue
        if stored and PART_SEPARATOR in token:
            parts = tuple(token.split(PART_SEPARATOR))
            written = "".join(parts)
        else:
            spoken = slow_word(token) if slow_word is not None else token
            parts = tuple(p for p in spoken.split(PART_SEPARATOR) if p) or (token,)
            written = token
        natural.append(opening + written)
        words.append((opening + parts[0], *parts[1:]))
        opening = ""

    if len(words) < 2 and all(len(parts) == 1 for parts in words):
        return None
    return EnunciatedLine(text=" ".join(natural), words=tuple(words))


def line_phonemes(line: EnunciatedLine, planner: object | None) -> dict[str, str] | None:
    """Every word of *line* as IPA, keyed by the bare lowercase word, or ``None``.

    For a voice that is told how a line sounds rather than marked up word by
    word. The key is the word without its sentence punctuation, which is how the
    adapter finds it again; a word said twice is one entry, since the reading is
    looked up and not counted off.

    ``None`` when *planner* cannot read a word inside a line at all
    (``plan_line_word`` — a lexicon-backed planner has no such method and is
    never asked), and when it cannot read ONE of the words: a line with a gap in
    its reading is a line whose reading is wrong, and no reading is better.
    """
    plan_line_word = getattr(planner, "plan_line_word", None)
    if plan_line_word is None:
        return None
    readings: dict[str, str] = {}
    for token in line.text.split():
        bare = token.strip(_SENTENCE_PUNCTUATION)
        if not any(c.isalnum() for c in bare):
            continue
        ipa = plan_line_word(bare)
        if not ipa:
            return None
        readings[bare.lower()] = ipa
    return readings
