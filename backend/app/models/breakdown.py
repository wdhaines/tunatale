"""One step of a Pimsleur-style word breakdown, plus where its audio comes from."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BreakdownChunk:
    """A breakdown step: what to say, and which syllables of which word it is.

    ``text`` is the *spoken* form, which is not always the raw substring: a
    fragment the voice would misread as a word is respelled for isolated
    synthesis (``de`` → ``deh``, ``bus`` → ``buss``, and geminate lengthening
    like ``et`` → ``ett``). ``span`` indexes the **raw** syllables of
    ``source_word``. They disagree on purpose — ``text`` feeds the fallback TTS
    path, ``span`` feeds the slicer.

    ``source_word`` is the whole word to render and cut from, never a piece of
    one: the point of slicing is to avoid asking the voice for a fragment. Both
    provenance fields are ``None`` when the chunk cannot be sliced — multi-word
    partials, monosyllabic words, and words whose syllables do not rejoin their
    surface form — and a ``None`` span always means "synthesize ``text``".
    """

    text: str
    source_word: str | None = None
    span: tuple[int, int] | None = None
