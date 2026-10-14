"""How a Cebuano Gemini take is checked for a t said as p or b (tunatale-ve4p.29).

The model is the Norwegian character CTC model the Norwegian slicer already
uses. Cebuano has no such model of its own that is small and local, and this
one does the job: every letter Cebuano writes is in its alphabet, Norwegian has
both /t/ and /p/, and on 2026-10-14 it separated every deliberate "Kaon na pa!"
and "adpo" render from every take it passed, with nothing in between (the table
is in ``app.audio.take_check``). A plugin may not import another plugin, so the
loader is written here rather than borrowed.

Only ``t`` is tested, the one consonant measured: "ta" said "pa" (day 5) and
"adto" said "adpo" (ve4p.21). Add a rival only with the same measurement.
"""

from __future__ import annotations

import re

from app.languages import TakeCheckConfig

MODEL_ID = "NbAiLab/nb-wav2vec2-300m-bokmaal"
SWAPS = {"t": "pb"}

# The model's word break.
_WORD_BREAK = "|"
# Marks that are written but not said as a letter: sentence punctuation, the
# hyphen (a glottal stop, kanus-a), the apostrophe (a contraction, napulo'g).
_NOT_LETTERS = re.compile(r"[?!.,;:…\"“”'’\-]")


def spell_for_model(text: str) -> str | None:
    """*text* in the model's letters, words joined by its word break; ``None`` if nothing is left."""
    words = _NOT_LETTERS.sub(" ", text.lower()).split()
    return _WORD_BREAK.join(words) or None


# Two like vowels side by side: in Cebuano spelling that is a glottal stop
# between them (miinom = mi.ʔi.nom, kaayo = ka.ʔa.yo), and the one the drill
# voice loses when it runs them together (tunatale-ve4p.24). Unlike vowels
# (moinom, mianhi) pass by ear without a closure, so they are not screened.
_LIKE_VOWELS = re.compile(r"([aeiou])\1")


def needs_closure_screen(text: str) -> bool:
    """One word with like vowels side by side. A line is not screened: the
    pauses between its words would read as closures."""
    words = _NOT_LETTERS.sub(" ", text.lower()).split()
    return len(words) == 1 and _LIKE_VOWELS.search(words[0]) is not None


def take_check_config() -> TakeCheckConfig:
    from app.plugins.languages.ceb.take_check_model import create_emission_model

    return TakeCheckConfig(
        model_id=MODEL_ID,
        model_factory=create_emission_model,
        swaps=SWAPS,
        normalize=spell_for_model,
        closure_screen=needs_closure_screen,
    )
