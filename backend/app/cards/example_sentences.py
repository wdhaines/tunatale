"""Parse an Anki 'Example sentences' field into usable (l2, gloss) pairs.

Segmentation splits on ``<br>`` variants and literal newlines.  A segment is
usable iff it contains exactly one balanced ``(<i>…</i>)`` gloss group that is
the last non-whitespace content, and the text before the gloss is
markup-free.  Segments that fail any check are silently skipped — a wrong
cloze lands on a card the learner studies and is far more expensive than a
missing one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: The gloss's inner text is captured directly, so there is no second pass that
#: could fail on a shape this pattern already guaranteed. The trailing `\.?` is
#: what discards the period some rows place AFTER the gloss group; the sentence's
#: own terminator stays inside `l2`.
_SEGMENT_RE = re.compile(
    r"^(?P<l2>.+?)\s*"
    r"\(<i>(?P<gloss>.+)</i>\)"
    r"\s*\.?\s*$",
    re.DOTALL,
)
_SEGMENT_SPLIT_RE = re.compile(r"<br\s*/?>|\n", re.IGNORECASE)


@dataclass(frozen=True)
class ExampleSentence:
    """A single target-language sentence with its English gloss."""

    l2: str
    gloss: str


def parse_example_sentences(raw: str) -> list[ExampleSentence]:
    """Every segment of *raw* that can be read confidently, in source order."""
    segments = _SEGMENT_SPLIT_RE.split(raw)
    results: list[ExampleSentence] = []

    for segment in segments:
        segment = segment.strip()
        if not segment:
            continue

        if segment.count("<i>") != segment.count("</i>"):
            continue

        if segment.count("<i>") != 1:
            continue

        match = _SEGMENT_RE.match(segment)
        if not match:
            continue

        # `l2` cannot be empty here and is not re-checked: the segment was
        # stripped above, so its first character is non-whitespace, and `.+?`
        # consumes at least that character.
        l2 = match.group("l2").strip()

        # Residual markup in the sentence half means the gloss group we matched is
        # not the only markup in the segment — e.g. `2 < 3 (<i>…</i>)`. Refuse
        # rather than emit a sentence with a stray angle bracket in it.
        if "<" in l2 or ">" in l2:
            continue

        gloss = match.group("gloss").strip()

        # `l2` needs no punctuation surgery. The optional `.` that must be
        # dropped is the one AFTER the gloss group, which the pattern already
        # consumes separately; the sentence's own terminator sits inside `l2` and
        # is part of it (`Hvilket tegn er det?` keeps its `?`). An earlier version
        # here stripped `.` and then re-appended the original last character,
        # which left a `?`-terminated sentence with two of them.
        results.append(ExampleSentence(l2=l2, gloss=gloss))

    return results
