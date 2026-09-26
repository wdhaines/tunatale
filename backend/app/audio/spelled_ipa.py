"""IPA read off a word's SPELLING, for a chunk no pronunciation lexicon covers.

Norwegian and Tagalog resolve a fragment from a lexicon of real readings, and
Cebuano has neither. A language whose spelling is close enough to phonemic can
be read letter by letter instead, which is what this class does: the plugin
supplies the letter and digraph tables, and the shared logic — where a syllable
opens on a glottal stop, that a digraph is one sound, that a lone syllable
carries a primary-stress mark — lives here so no language has to restate it.

It is core, not a plugin, for the same reason the registry is: a plugin may not
import another plugin, so the shared logic cannot live inside either of them.
What is per-language and therefore NOT here: the tables, and the syllabifier.
The syllabifier must be the SAME function whose output ``BreakdownChunk.span``
indexes (``app.generation.syllabify.syllabify_word`` for a language with no
syllabifier of its own), or a span names the wrong letters and the audio
transcribes a syllable the caption never showed.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from app.generation.section_builder import _SENTENCE_PUNCTUATION


def _bare(text: str) -> str:
    """*text* without leading and trailing sentence punctuation.

    The set is the one the breakdown strips, and NOT ``\\W``: ``-`` and ``'`` are
    letters in Cebuano and Tagalog rather than punctuation on a word — the
    hyphen marks a glottal stop (kanus-a, pag-anhi) and the apostrophe is a
    contraction (napulo'g) — so stripping them would change what the learner
    hears, not merely how the word is cut up.
    """
    return text.strip(_SENTENCE_PUNCTUATION)


class SpelledPhonemePlanner:
    """IPA for sub-word chunks, read off the spelling of a language's word.

    Implements :class:`app.languages.PhonemePlanner`. A whole word is NOT
    special: unlike the lexicon-backed planners there is nothing to look the
    word up in, so a span covering all of its syllables is planned like any
    other, and only a refusal returns ``None``. :meth:`plan_word` is that span
    written out for the caller that has a whole word and no span.
    """

    def __init__(
        self,
        letter_ipa: Mapping[str, str],
        digraph_ipa: Mapping[str, str],
        syllabify: Callable[[str], list[str]],
        onset_vowels: str = "aeiou",
        whole_word_ipa: Mapping[str, str] | None = None,
    ) -> None:
        """
        Args:
            letter_ipa: one letter to its IPA. A character absent from the map
                contributes nothing.
            digraph_ipa: two letters to one IPA sound, tried BEFORE the single
                letters, so ``ng`` is one sound and not ``nɡ``.
            syllabify: cuts the stripped word into the pieces a span indexes.
            onset_vowels: the letters a syllable may start with in this
                language. A piece starting with one of them opens on a glottal
                stop, which is a sound the spelling does not write down.
            whole_word_ipa: lowercase whole words whose reading the spelling
                cannot produce (a written form that omits sounds). Used only
                when a span covers the WHOLE word, and returned verbatim;
                a span inside such a word is still spelled.
        """
        self._letter_ipa = letter_ipa
        self._digraph_ipa = digraph_ipa
        self._syllabify = syllabify
        self._onset_vowels = onset_vowels
        self._whole_word_ipa = dict(whole_word_ipa or {})

    def _spell(self, syllable: str) -> str:
        """The IPA of one syllable, written out. Never called on an empty piece
        (:meth:`plan_chunk` refuses those first)."""
        first = syllable[:1]
        out = "ʔ" if first in self._onset_vowels else ""
        i = 0
        while i < len(syllable):
            pair = syllable[i : i + 2]
            if pair in self._digraph_ipa:
                out += self._digraph_ipa[pair]
                i += 2
            else:
                out += self._letter_ipa.get(syllable[i], "")
                i += 1
        return out

    def plan_chunk(
        self,
        source_word: str,
        span: tuple[int, int],
        upos: str | None = None,
        chunk_text: str | None = None,
    ) -> str | None:
        """IPA for syllables ``span`` of *source_word*, or ``None`` for plain synthesis.

        ``None`` when the span does not fit the word, when the syllabifier
        returns an empty piece, and when *chunk_text* names other letters than
        the span now does (a lesson stored before the boundaries moved: IPA for
        the new split would play a syllable its caption does not name).

        *upos* is accepted and ignored. It picks among a whole word's REAL
        readings, and there are none here to pick between.
        """
        pieces = [p.lower() for p in self._syllabify(_bare(source_word))]
        start, stop = span
        if not 0 <= start < stop <= len(pieces) or not all(pieces):
            return None
        if chunk_text is not None and _bare(chunk_text).lower() != "".join(pieces[start:stop]):
            return None
        if (start, stop) == (0, len(pieces)) and (override := self._whole_word_ipa.get("".join(pieces))) is not None:
            return override
        spelled = "".join(self._spell(p) for p in pieces[start:stop])
        return "ˈ" + spelled if stop - start == 1 else spelled

    def plan_word(self, word: str) -> str | None:
        """The IPA of the WHOLE *word*, or ``None`` for plain synthesis.

        :meth:`plan_chunk` needs a span, and a caller holding a whole word has
        none to pass — a breakdown chunk knows which syllables of its source word
        it is, and a multi-word drill step does not. So this is the same span,
        ``(0, every syllable)``, filled in from the same strip and the same
        split :meth:`plan_chunk` will do: a word planned through different
        letters than its own chunks are would give two readings of one word.

        Nothing new is decided here. A whole word was never special for this
        class (unlike the lexicon-backed planners there is nothing to look it up
        in), so the whole-word overrides apply as they always did, and an empty
        or all-punctuation word refuses exactly as an empty span does.
        """
        return self.plan_chunk(word, (0, len(self._syllabify(_bare(word)))))
