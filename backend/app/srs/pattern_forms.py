"""Which affix pattern's forms a text says: the one reader the pace gate and Grade's suggestions share.

The pace gate (``app.generation.pattern_gate``) counts a word pattern's evidence as the sentence
cards whose blank says one of its forms (tunatale-ve4p.36). Grade suggests those forms as blanks
(tunatale-ve4p.37, design-sentence-mining-2026-11.md § 8). Both read a text here, so a suggestion
the gate would not count cannot exist: the text in the language's plain spelling
(``A1Morphology.plain_text``), through its lemmatizer, each token's A1 feature (``to_feature``).

Reads only.
"""

from __future__ import annotations

from collections.abc import Callable

from app.languages import get_a1_morphology
from app.models.lesson import Lesson
from app.srs.a1_morphology import AffixPattern
from app.srs.lemmatizer import get_lemmatizer


class FormReader:
    """The A1 features a text says, in one language; each text read once."""

    def __init__(self, language_code: str) -> None:
        bundle = get_a1_morphology(language_code)
        self.language_code = language_code
        self._to_feature = bundle.to_feature
        self._plain: Callable[[str], str] = bundle.plain_text or str
        self._lemmatizer = get_lemmatizer(language_code)
        self._read: dict[str, frozenset[str]] = {}

    def said(self, text: str) -> frozenset[str]:
        """The A1 features of the words of *text*."""
        if text not in self._read:
            tokens = self._lemmatizer.analyze_sentence(self._plain(text), self.language_code)
            self._read[text] = frozenset(f for token in tokens if (f := self._to_feature(token)) is not None)
        return self._read[text]

    def form(self, word: str) -> str:
        """*word* as one form: its plain spelling, case aside (Wolff's ``Mobálik`` is ``mobalik``)."""
        return self._plain(word).casefold()


def lesson_word_pattern(lesson: Lesson) -> AffixPattern | None:
    """The word pattern *lesson* drills (``generation_metadata.affix_drill``), or None.

    A sentence pattern (on-gi) is None too: its evidence is its focus cards, so a sentence blank
    saying one of its forms would count for nothing.
    """
    recorded = (lesson.generation_metadata or {}).get("affix_drill")
    bundle = get_a1_morphology(lesson.language_code)
    if not recorded or bundle is None:
        return None
    pattern = next((p for p in bundle.patterns if p.key == recorded.get("pattern")), None)
    return pattern if pattern is not None and not pattern.sentences else None
