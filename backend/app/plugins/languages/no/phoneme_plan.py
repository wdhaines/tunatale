"""Norwegian chunk planner — sub-word IPA via the NST lexicon.

Stage 2d of lexicon adoption: resolves a sub-word chunk's source word through
the NST lexicon, converts to IPA, and returns the IPA for the chunk's syllable
range. Whole phrases and whole words are the TTS's job; only sub-word fragments
benefit from lexicon-backed IPA.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.languages import LexiconOutcome
from app.plugins.languages.no.lexicon import BUILD_COMMAND, DB_PATH, NstLexicon, nst_lexicon_installed
from app.plugins.languages.no.norwegian_breakdown import flat_syllables
from app.plugins.languages.no.sampa import UnknownSegmentError, ipa_syllables, sampa_to_ipa, strip_tone

logger = logging.getLogger(__name__)


class NorwegianPhonemePlanner:
    """Chunk planner backed by the NST pronunciation lexicon.

    Probes for the built database ONCE, on first use, and holds ONE lexicon
    thereafter: ``plan_chunk`` is called per sub-word chunk, and re-probing
    (or reopening the database) per call would put a filesystem stat and a
    connection on the render path hundreds of times per lesson.

    The database is a gitignored BUILD ARTIFACT, so its absence is normal on a
    fresh clone or a fresh deploy and must not break rendering:
    :func:`app.plugins.languages.no.lexicon.NstLexicon.resolve` raises
    ``FileNotFoundError`` when it is missing, so this class gates on the
    capability probe, warns ONCE, and degrades to plain synthesis forever after.
    """

    def __init__(self, db_path: Path = DB_PATH) -> None:
        self._db_path = db_path
        self._lexicon: NstLexicon | None = None
        self._installed: bool | None = None  # probed on first call, then cached

    def _lexicon_or_none(self) -> NstLexicon | None:
        """The lexicon, or ``None`` when the database was never built."""
        if self._installed is None:
            self._installed = nst_lexicon_installed(self._db_path)
            if not self._installed:
                logger.warning(
                    "NST pronunciation lexicon not built at %s — chunk planning is disabled. Build with: %s",
                    self._db_path,
                    BUILD_COMMAND,
                )
        if not self._installed:
            return None
        if self._lexicon is None:
            self._lexicon = NstLexicon(self._db_path)
        return self._lexicon

    def plan_chunk(self, source_word: str, span: tuple[int, int]) -> str | None:
        """Return IPA for a sub-word syllable range, or ``None`` for plain synthesis.

        The five gates (in order):
        1. Unbuilt lexicon → ``None``.
        2. Word not RESOLVED in the lexicon → ``None``.
        3. SAMPA→IPA conversion fails → ``None``.
        4. Repo vs lexicon syllable-count mismatch → ``None``.
        5. Whole-word span → ``None`` (the TTS's job).
        """
        lex = self._lexicon_or_none()
        if lex is None:
            return None

        resolution = lex.resolve(source_word.lower())
        if resolution.outcome is not LexiconOutcome.RESOLVED:
            return None

        try:
            ipa = sampa_to_ipa(resolution.transcription)
        except UnknownSegmentError:
            return None

        ipa = strip_tone(ipa)
        lex_syls = ipa_syllables(ipa)
        repo_syls = flat_syllables(source_word)

        if repo_syls is None or len(lex_syls) != len(repo_syls):
            return None

        start, stop = span
        if (start, stop) == (0, len(repo_syls)):
            return None

        return ".".join(lex_syls[start:stop])


def create_phoneme_planner() -> NorwegianPhonemePlanner:
    """Zero-arg factory registered on the plugin's ``LanguageConfig``.

    Now returns a chunk planner (stage 2d): ``plan_chunk`` replaces ``plan``.
    """
    return NorwegianPhonemePlanner(DB_PATH)
