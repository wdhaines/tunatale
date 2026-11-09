"""Which words of a key phrase are broken up, and how (tunatale-lat6, the owner 2026-11-09).

A key phrase's build-up (:func:`section_builder.build_word_breakdown_spans`)
walks its words from the last. Each word is said as syllable pieces and
growing joins ending on the word itself, and after it comes a backward rung
joining it to the words already built: ``baay``, ``ilang baay?``,
``niy ilang baay?`` ... the phrase. That full build ran 10:14 on Wolff 2b's
ten key phrases, against a 2:16 dialogue. The owner tuned the rules below in a
workbench page, hearing the result in minutes:

- names and loanwords are never broken (``skip_words``);
- only words of at least ``min_syllables`` are broken: 3 for a Wolff lesson,
  2 for a generated one ("except for the 2-syllable limit");
- a broken word of 3+ syllables is built root first when its prefix comes off
  cleanly (``lisod``, ``maglisod``), else two syllables at a time from the
  end (``ila``, ``ilaila``, ``ipailaila``). A 2-syllable word keeps its
  single syllables;
- a word is broken once per lesson (``built`` carries what earlier key phrases
  broke);
- the backward rungs always stay: "if a word doesn't get broken down, it still
  contributes to the backwards buildup of the full phrase". An unbroken word's
  lone rung goes, since the next rung says it, except the last word's: the
  build starts there.

:func:`reshape_breakdown` only ever KEEPS chunks of the full build, so a
reshaped lesson asks the TTS for no text the full build did not, and every
kept piece still carries the provenance the slicer cuts it by. When the chunks
do not read as the shape above, it returns them unchanged: a lesson with its
full build is a longer lesson, not a broken one.

Whether a word is KNOWN is the deck's to say and is decided at publish
(``known_buildups``), where the deck is.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from app.generation.drill_lines import fold
from app.models.breakdown import BreakdownChunk

logger = logging.getLogger(__name__)

# A word ends a sentence when it ends in one of these, so the next word may be
# capitalized for that reason alone.
_SENTENCE_END = re.compile(r"[.!?…]['\"”’)]*$")


@dataclass(frozen=True)
class BuildupRules:
    """The rules a lesson's key phrases are built under."""

    min_syllables: int = 2
    # Folded words never broken: names and loanwords.
    skip_words: frozenset[str] = field(default_factory=frozenset)


def capitalized_names(phrase: str) -> frozenset[str]:
    """The folded words of *phrase* written with a capital where no sentence starts.

    A generated story marks no names, but its writer capitalizes them: a capital
    after the first word of a sentence is a name (or a title such as Doktor,
    which is never worth breaking up either).
    """
    names: set[str] = set()
    starts = True
    for token in phrase.split():
        word = fold(_strip(token))
        if word and not starts and token.lstrip("\"'“‘(¿¡")[:1].isupper():
            names.add(word)
        if word:
            starts = bool(_SENTENCE_END.search(token))
    return frozenset(names)


@dataclass
class _Word:
    key: str  # folded
    chunks: list[BreakdownChunk]  # its pieces and joins, ending on the word itself


def _strip(token: str) -> str:
    return token.strip(".,!?;:…\"'“”‘’()¿¡—–")


def _read(phrase: str, chunks: Sequence[BreakdownChunk]) -> list[tuple[_Word, BreakdownChunk | None]] | None:
    """The build read as (word, the rung after it) from the last word to the first, or None.

    Each rung is checked against the words it should join, so a build that is not
    the full build's shape is refused rather than misread.
    """
    words = phrase.split()
    keys = [fold(_strip(w)) for w in words]
    if not words or not all(keys) or not chunks or chunks[0].text != phrase:
        return None
    body = list(chunks[1:])
    if len(words) == 1:
        # One word: its pieces, then the phrase itself closes the build.
        if not body or body[-1].text != phrase:
            return None
        return [(_Word(keys[0], body), None)]
    out: list[tuple[_Word, BreakdownChunk | None]] = []
    pos = 0
    for j in range(len(words) - 1, -1, -1):
        start = pos
        while pos < len(body) and fold(body[pos].text) != keys[j]:
            if " " in body[pos].text.strip():
                return None  # a rung before the word was said
            pos += 1
        if pos == len(body):
            return None
        pos += 1
        rung = None
        if j < len(words) - 1:
            if pos == len(body) or body[pos].text != " ".join(words[j:]):
                return None
            rung = body[pos]
            pos += 1
        out.append((_Word(keys[j], body[start : pos - (rung is not None)]), rung))
    return out if pos == len(body) else None


def _syllables(word: _Word) -> int:
    return max((c.span[1] for c in word.chunks if c.span), default=1)


def _grouped(word: _Word, n: int, prefix: str | None) -> list[BreakdownChunk] | None:
    """A broken word of 3+ syllables: root first, else pairs from the end. None if a rung is missing."""
    whole = word.chunks[-1]
    # The rungs that end on the word's last syllable; a root of one syllable is its last piece.
    joins = {c.span[0]: c for c in word.chunks[:-1] if c.span and c.span[1] == n and c.span[0] > 0}
    pieces = {c.span[0]: c.text for c in word.chunks if c.span and c.span[1] - c.span[0] == 1}
    starts: list[int] = []
    if prefix:
        letters = ""
        for i in range(1, n):
            letters += pieces.get(i - 1, "\0")
            if fold(letters) == fold(prefix):
                starts = [i]
                break
    if not starts:
        starts = list(range(n - 2, 0, -2))
    if any(s not in joins for s in starts):
        return None
    return [*(joins[s] for s in starts), whole]


def reshape_breakdown(
    phrase: str,
    chunks: Sequence[BreakdownChunk],
    *,
    rules: BuildupRules,
    built: set[str],
    prefix_fn: Callable[[str], str | None] | None,
) -> list[BreakdownChunk]:
    """*chunks* (the phrase's full build) kept to what *rules* break. Adds what it broke to *built*."""
    read = _read(phrase, chunks)
    if read is None:
        logger.warning("Build-up not reshaped (unexpected chunks): %r", phrase)
        return list(chunks)
    skip = rules.skip_words | capitalized_names(phrase)
    out: list[BreakdownChunk] = [chunks[0]]
    broke: set[str] = set()
    for index, (word, rung) in enumerate(read):
        last = index == 0
        n = _syllables(word)
        breaks = n >= 2 and n >= rules.min_syllables and word.key not in skip and word.key not in built
        kept: list[BreakdownChunk] | None = None
        if breaks and n >= 3:
            prefix = prefix_fn(word.chunks[-1].source_word or word.key) if prefix_fn else None
            kept = _grouped(word, n, prefix)
            if kept is None:
                logger.warning("Build-up kept whole (no rung to group by): %r in %r", word.key, phrase)
                kept = list(word.chunks)
        elif breaks:
            kept = list(word.chunks)
        elif last or len(read) == 1:
            kept = [word.chunks[-1]]
        else:
            kept = []
        if breaks:
            broke.add(word.key)
        out.extend(kept)
        if rung is not None:
            out.append(rung)
    built |= broke
    # Cuts can leave the same text twice in a row (a one-word phrase said whole).
    deduped = [out[0]]
    for chunk in out[1:]:
        if chunk.text != deduped[-1].text:
            deduped.append(chunk)
    return deduped
