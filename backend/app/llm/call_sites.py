"""Declared LLM call-site labels, in one place (bead 6zzu2 Stage 2).

The Groq ledger's sixth field is a label naming the ROUTE that spent the
budget. Labels are declared here as literal constants so the set is enumerable
by a test (``test_llm_call_sites.py`` asserts the exact membership) rather
than scattered as bare strings across call sites.

Two kinds of label:

- Single-route sites take one of :data:`FLAT_LABELS` verbatim
  (``story``, ``planner``, … — see the label table in the 6zzu2 brief).
- The three cloze helpers are shared by two routes — the background prestage
  mint and the interactive card-adding API — so their label is COMPOSED from
  the caller's route and the helper's operation: ``f"{caller}.{operation}"``
  (``prestage.cloze_generate``, ``api.cloze_judge``, …).

Every label must satisfy ``^[a-z0-9_.]+$``: the ledger line is
whitespace-split, so a space, hyphen or uppercase letter would corrupt the
column count (``test_llm_call_sites.py::TestCallSitePattern`` pins it).
"""

from __future__ import annotations

from typing import Final


class CallSite:
    """Container for the declared call-site label constants."""

    # ── Single-route labels — used verbatim at the .complete() site ──────────
    STORY: Final = "story"
    PLANNER: Final = "planner"
    GLOSSING: Final = "glossing"
    TRANSLATE_TERM: Final = "translate_term"
    WORD_GLOSS: Final = "word_gloss"
    MEDIA_QUERY: Final = "media_query"
    MEDIA_CHOOSE: Final = "media_choose"
    REGLOSS: Final = "regloss"
    SRS_TRANSLATE: Final = "srs_translate"
    LEMMA_RESOLVE: Final = "lemma_resolve"

    # ── Operation suffixes for the three threaded cloze helpers ──────────────
    CLOZE_GENERATE: Final = "cloze_generate"
    CLOZE_JUDGE: Final = "cloze_judge"
    CLOZE_TRANSLATE: Final = "cloze_translate"

    # ── The two routes that thread a caller through those helpers ────────────
    CALLER_PRESTAGE: Final = "prestage"
    CALLER_API: Final = "api"

    FLAT_LABELS: Final = frozenset(
        {
            STORY,
            PLANNER,
            GLOSSING,
            TRANSLATE_TERM,
            WORD_GLOSS,
            MEDIA_QUERY,
            MEDIA_CHOOSE,
            REGLOSS,
            SRS_TRANSLATE,
            LEMMA_RESOLVE,
        }
    )
    OPERATION_SUFFIXES: Final = frozenset({CLOZE_GENERATE, CLOZE_JUDGE, CLOZE_TRANSLATE})
    CALLERS: Final = frozenset({CALLER_PRESTAGE, CALLER_API})

    @staticmethod
    def compose(caller: str, operation: str) -> str:
        """The composed label for a threaded helper — ``prestage.cloze_generate``."""
        return f"{caller}.{operation}"
