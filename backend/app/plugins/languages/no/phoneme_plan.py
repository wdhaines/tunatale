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
from app.plugins.languages.no.lexicon_syllables import lexicon_reading
from app.plugins.languages.no.norwegian_breakdown import flat_syllables, resolved_buildup_units
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

    ``db_path`` governs BOTH halves — the transcriptions read through
    :meth:`_lexicon_or_none` and the syllable boundaries read through
    :func:`lexicon_reading`. It used to govern only the first, so a planner
    built over any non-default database took its boundaries from the installed
    lexicon and its phonemes from the given one — the exact crossing
    ``lexicon_syllables`` forbids ("boundaries and phonemes must come from the
    SAME source"). Latent in production, where both were ``DB_PATH``
    (tunatale-na4l).

    ⚠️ Passing it does not make the two sources incapable of disagreeing.
    :func:`lexicon_reading` is ``@lru_cache``'d at MODULE level on
    ``(word, db_path)``; this lexicon is held per INSTANCE. A rebuild at the
    same path leaves the cache serving the old split to a resolver reading the
    new file, which is what ``_plan_against``'s absent / unknown-segment /
    syllable-count gates exist to catch. They fire zero times in production and
    are not dead — see ``TestTheTwoLexiconSourcesCanStillDesync``.

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

    def plan_chunk(
        self,
        source_word: str,
        span: tuple[int, int],
        upos: str | None = None,
        chunk_text: str | None = None,
    ) -> str | None:
        """Return IPA for a sub-word syllable range, or ``None`` for plain synthesis.

        Two lookups, in this order: the whole ``source_word``, and — only when
        that declines — the compound buildup part that HOSTS the span. See
        :meth:`_plan_against` for the gates each lookup runs, and
        :meth:`_plan_via_part` for why the descent is a fallback rather than a
        replacement.

        The word-level gates run first and outside both lookups:
        1. Word not syllabifiable → ``None``.
        2. Whole-word span → ``None`` (the TTS's job). Asked about the SOURCE
           WORD, never about a part: a chunk that fills its host part exactly
           ('etter' in etterforskningsteamet) is still a sub-word chunk, and
           refusing it would delete the largest rung of every compound buildup.
        3. Unbuilt lexicon → ``None``.
        """
        # Cheap, word-only gates first: a whole-word chunk never needs the
        # phoneme lexicon, so this also avoids opening the database for one.
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
        planned = self._plan_against(lex, word, repo_syls, span, upos, chunk_text)
        if planned is not None:
            return planned
        return self._plan_via_part(lex, word, span, upos, chunk_text)

    def _plan_via_part(
        self,
        lex: NstLexicon,
        word: str,
        span: tuple[int, int],
        upos: str | None,
        chunk_text: str | None,
    ) -> str | None:
        """Resolve *span* against the compound buildup part that contains it.

        ``_resolve_compound_parts`` already resolves BOUNDARIES per part — each
        buildup unit against the lexicon as the word it is — and this closes the
        matching gap on the phoneme side, which looked up the whole compound
        only. For a compound the lexicon does not contain (etterforskningsteamet)
        that made every chunk refuse, even though etter and forsknings resolve
        cleanly on their own.

        ⚠️ It is a FALLBACK, deliberately, and not the per-part rule the boundary
        half uses. Measured over the nine stored lessons, resolving every
        compound per-part instead LOSES 10 chunks (skisporet's 'sporet' and
        'ret': standalone sporet is ambiguous between the definite-noun and
        past-participle -et readings, while the compound is not) and changes 22
        more, replacing a compound's secondary stress with a citation-form
        primary. Whether a fragment heard alone wants citation stress is an ear
        question; a coverage change must not answer it silently. Whole-word
        first keeps this strictly additive: +12 chunks, 0 lost, 0 changed.

        A span that CROSSES two parts ('forskningsteamet') has no single host
        and refuses. Stitching two parts' transcriptions would reintroduce the
        cross-seam splice per-part resolution exists to avoid, and every
        constituent rung of such a partial is covered on its own.
        """
        units = resolved_buildup_units(word)
        if units is None:
            return None
        start, stop = span
        base = 0
        for surface, pieces in units:
            end = base + len(pieces)
            if base <= start and stop <= end:
                return self._plan_against(lex, surface, pieces, (start - base, stop - base), upos, chunk_text)
            base = end
        return None

    def _plan_against(
        self,
        lex: NstLexicon,
        surface: str,
        syllables: list[str],
        span: tuple[int, int],
        upos: str | None,
        chunk_text: str | None,
    ) -> str | None:
        """IPA for *span* of *surface*, whose breakdown syllables are *syllables*.

        Called with the whole source word, and then with the part that hosts the
        span; *span* is always expressed in *syllables*' own indices. The gates,
        in execution order:
        1. The breakdown did NOT adopt the lexicon's boundaries for this word
           → ``None``. Count agreement is not boundary agreement (tunatale-xk1p):
           ``undersøke`` is 4 syllables both ways, but spelling puts the ``n``
           with ``un`` while pronunciation puts it with the next syllable, so a
           positional slice played ``der`` as ``nə``. The only safe test is that
           the split being sliced IS the split the caption was cut at. Asking it
           per part is what lets undersøke's ``søke`` half through while its
           ``under`` half — whose most-enunciated reading the tiebreak chose —
           resolves through that chosen reading (tunatale-k318.5).
        2. The word is neither RESOLVED nor ambiguous-but-agreeing → ``None``.
        3. SAMPA→IPA conversion fails, for any candidate → ``None``.
        4. Candidate readings disagree at THIS span (beyond stress) → ``None`` —
           UNLESS the most-enunciated tiebreak already CHOSE one reading for
           both boundaries and sound. Then that single reading is used directly
           and gate 4 is skipped: the disagreement it would have refused was
           deliberately settled by the same decision that set the caption's cut.
           The all-candidates gate stays load-bearing for the ``(split, None)``
           case, where readings agreed on the split but may still differ in
           sound (tunatale-d4td).
        5. *chunk_text* does not match the syllables at *span* → ``None``. A
           lesson stored before the boundaries moved carries the old text; give
           it IPA and it plays a syllable its caption does not name.
        """
        reading = lexicon_reading(surface, self._db_path)
        if reading is None:
            return None
        split, chosen_transcription = reading
        if split != syllables:
            return None
        start, stop = span

        if chosen_transcription is not None:
            # The tiebreak CHOSE one reading for BOTH boundaries and sound, so
            # that reading alone is the candidate pool (tunatale-k318.5). The
            # all-candidates-agree check below then cannot refute it — a single
            # candidate disagrees with nothing — which is exactly the "skip the
            # agree check" the brief asks for, done structurally. Every other
            # guard below (unknown segment, syllable count, chunk text) still
            # binds, unchanged.
            candidates = [chosen_transcription]
        else:
            resolution = lex.resolve(surface, upos)
            if resolution.outcome is LexiconOutcome.RESOLVED:
                candidates = [resolution.transcription]
            elif resolution.outcome in (LexiconOutcome.AMBIGUOUS_NO_POS, LexiconOutcome.AMBIGUOUS_POS_DIDNT_HELP):
                # An ambiguity that does not touch THIS span is not an ambiguity
                # for this chunk. Measured over the stored lessons: 4 of 8
                # ambiguous sub-word chunks are rescued this way — all of them
                # first syllables, where the readings differ only in stress. The
                # other 4 are the regular Norwegian -et split (definite noun /ə/
                # vs past participle /ət/), where the readings genuinely
                # disagree at exactly the syllable being asked for, and those
                # still fall back. Resolving the WHOLE word stays a refusal to
                # guess.
                #
                # AMBIGUOUS_POS_DIDNT_HELP: the POS tag narrowed but left
                # multiple readings — fall through to span-agreement, which is
                # the fallback for when no tag is available. A tag that does not
                # narrow must not be *worse* than no tag.
                candidates = sorted(lex.candidate_transcriptions(surface, upos))
            else:
                return None

        pieces: set[str] = set()
        for transcription in candidates:
            try:
                ipa = strip_tone(sampa_to_ipa(transcription))
            except UnknownSegmentError:
                return None
            lex_syls = ipa_syllables(ipa)
            if len(lex_syls) != len(split):
                return None
            pieces.add(".".join(lex_syls[start:stop]))

        if len({_STRESS.sub("", piece) for piece in pieces}) != 1:
            return None

        if chunk_text is not None and chunk_text.lower() != "".join(split[start:stop]):
            return None
        # They say the same thing; prefer the marked form, since a fragment
        # spoken alone is heard as a citation form.
        return max(pieces)


def create_phoneme_planner() -> NorwegianPhonemePlanner:
    """Zero-arg factory registered on the plugin's ``LanguageConfig``.

    Now returns a chunk planner (stage 2d): ``plan_chunk`` replaces ``plan``.
    """
    return NorwegianPhonemePlanner(DB_PATH)
