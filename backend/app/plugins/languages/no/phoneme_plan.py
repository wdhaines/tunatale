"""Norwegian chunk planner — sub-word IPA via the NST lexicon.

Stage 2d of lexicon adoption: resolves a sub-word chunk's source word through
the NST lexicon, converts to IPA, and returns the IPA for the chunk's syllable
range. Whole phrases and whole words are the TTS's job; only sub-word fragments
benefit from lexicon-backed IPA.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from app.languages import LexiconOutcome
from app.plugins.languages.no.lexicon import BUILD_COMMAND, DB_PATH, NstLexicon, nst_lexicon_installed
from app.plugins.languages.no.norwegian_breakdown import flat_syllables
from app.plugins.languages.no.sampa import UnknownSegmentError, ipa_syllables, sampa_to_ipa, strip_tone

logger = logging.getLogger(__name__)

# Primary/secondary stress. Two readings that differ ONLY here say the same
# thing about which sounds the syllable contains.
_STRESS = re.compile(r"[ˈˌ]")


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

    def plan_chunk(self, source_word: str, span: tuple[int, int], upos: str | None = None) -> str | None:
        """Return IPA for a sub-word syllable range, or ``None`` for plain synthesis.

        The gates, in order:
        1. Word not syllabifiable → ``None``.
        2. Whole-word span → ``None`` (the TTS's job).
        3. Unbuilt lexicon, or word neither RESOLVED nor AMBIGUOUS_NO_POS → ``None``.
        4. SAMPA→IPA conversion fails, for any candidate → ``None``.
        5. Repo vs lexicon syllable-count mismatch → ``None``. Without agreement
           on the count there is no lexicon syllable corresponding to this
           chunk, and a positional slice would be a different part of the word.
        6. Candidate readings disagree at THIS span (beyond stress) → ``None``.
        """
        # Cheap, word-only gates first: a whole-word chunk never needs the
        # lexicon, so this also avoids opening the database for one.
        repo_syls = flat_syllables(source_word)
        if not repo_syls:
            return None
        start, stop = span
        if (start, stop) == (0, len(repo_syls)):
            return None

        lex = self._lexicon_or_none()
        if lex is None:
            return None

        word = source_word.lower()
        resolution = lex.resolve(word, upos)
        if resolution.outcome is LexiconOutcome.RESOLVED:
            candidates = [resolution.transcription]
        elif resolution.outcome in (LexiconOutcome.AMBIGUOUS_NO_POS, LexiconOutcome.AMBIGUOUS_POS_DIDNT_HELP):
            # An ambiguity that does not touch THIS span is not an ambiguity for
            # this chunk. Measured over the stored lessons: 4 of 8 ambiguous
            # sub-word chunks are rescued this way — all of them first
            # syllables, where the readings differ only in stress. The other 4
            # are the regular Norwegian -et split (definite noun /ə/ vs past
            # participle /ət/), where the readings genuinely disagree at exactly
            # the syllable being asked for, and those still fall back. Resolving
            # the WHOLE word stays a refusal to guess.
            #
            # AMBIGUOUS_POS_DIDNT_HELP: the POS tag narrowed but left multiple
            # readings — fall through to span-agreement, which is the fallback
            # for when no tag is available. A tag that does not narrow must not
            # be *worse* than no tag.
            candidates = sorted(lex.candidate_transcriptions(word, upos))
        else:
            return None

        pieces: set[str] = set()
        for transcription in candidates:
            try:
                ipa = strip_tone(sampa_to_ipa(transcription))
            except UnknownSegmentError:
                return None
            lex_syls = ipa_syllables(ipa)
            if len(lex_syls) != len(repo_syls):
                return None
            pieces.add(".".join(lex_syls[start:stop]))

        if len({_STRESS.sub("", piece) for piece in pieces}) != 1:
            return None
        # They say the same thing; prefer the marked form, since a fragment
        # spoken alone is heard as a citation form.
        return max(pieces)


def create_phoneme_planner() -> NorwegianPhonemePlanner:
    """Zero-arg factory registered on the plugin's ``LanguageConfig``.

    Now returns a chunk planner (stage 2d): ``plan_chunk`` replaces ``plan``.
    """
    return NorwegianPhonemePlanner(DB_PATH)
