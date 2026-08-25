"""Norwegian phrase planner — per-token IPA mapping via the NST lexicon.

Stage 2c of lexicon adoption: resolves each L2 phrase's words through the
NST lexicon, converts to IPA, and hands the per-token mapping to the
``<phoneme>`` seam in Azure TTS. Whole phrases only; provenance-carrying
chunks are stage 2d.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from pathlib import Path

from app.languages import LexiconOutcome
from app.plugins.languages.no.lexicon import BUILD_COMMAND, DB_PATH, NstLexicon, nst_lexicon_installed
from app.plugins.languages.no.sampa import UnknownSegmentError, sampa_to_ipa, strip_tone

logger = logging.getLogger(__name__)

# Same language-agnostic tokeniser as 2b put in azure_tts.py.
_WORD_RE = re.compile(r"[^\W\d_]+")


class NorwegianPhonemePlanner:
    """Phrase planner backed by the NST pronunciation lexicon.

    Probes for the built database ONCE, on first use, and holds ONE lexicon
    thereafter: ``plan`` is called per phrase, and re-probing (or reopening the
    database) per phrase would put a filesystem stat and a connection on the
    render path hundreds of times per lesson.

    The database is a gitignored BUILD ARTIFACT, so its absence is normal on a
    fresh clone or a fresh deploy and must not break rendering:
    :func:`app.plugins.languages.no.lexicon.NstLexicon.resolve` raises
    ``FileNotFoundError`` when it is missing, so this class gates on the
    capability probe, warns ONCE, and degrades to plain synthesis forever after.
    """

    def __init__(self, db_path: Path = DB_PATH) -> None:
        self._db_path = db_path
        self._lexicon: NstLexicon | None = None
        self._installed: bool | None = None  # probed on first plan(), then cached

    def _lexicon_or_none(self) -> NstLexicon | None:
        """The lexicon, or ``None`` when the database was never built."""
        if self._installed is None:
            self._installed = nst_lexicon_installed(self._db_path)
            if not self._installed:
                logger.warning(
                    "NST pronunciation lexicon not built at %s — phrase planning is disabled. Build with: %s",
                    self._db_path,
                    BUILD_COMMAND,
                )
        if not self._installed:
            return None
        if self._lexicon is None:
            self._lexicon = NstLexicon(self._db_path)
        return self._lexicon

    def plan(self, text: str) -> Mapping[str, str] | None:
        """Map each surface token to IPA, or ``None`` for plain synthesis.

        All-or-nothing: any token that is not RESOLVED, or whose transcription
        raises UnknownSegmentError, sinks the whole phrase.
        """
        lex = self._lexicon_or_none()
        if lex is None:
            return None

        tokens = _WORD_RE.findall(text)
        if not tokens:
            return None

        mapping: dict[str, str] = {}
        for token in tokens:
            key = token.lower()
            if key in mapping:
                continue
            resolution = lex.resolve(key)
            if resolution.outcome is not LexiconOutcome.RESOLVED:
                return None
            try:
                ipa = sampa_to_ipa(resolution.transcription)
            except UnknownSegmentError:
                return None
            mapping[key] = strip_tone(ipa)
        return mapping


def create_phoneme_planner() -> NorwegianPhonemePlanner:
    """Zero-arg factory registered on the plugin's ``LanguageConfig``."""
    return NorwegianPhonemePlanner(DB_PATH)
