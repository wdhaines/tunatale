"""Did a TTS take say the consonant it was asked for? (tunatale-ve4p.29)

The owner, 2026-10-14, on Cebuano day 5: the first key phrase, "Kaon na ta!",
said PA — "it's just unacceptable for those to keep being broken." The same
voice had said "adto" as "adpo" the day before. Gemini TTS is nondeterministic,
so a bad take is fixed by drawing another one; what was missing was a way to
know a take is bad without a listener.

How a take is scored
--------------------
A character CTC model (a language plugin's, see ``TakeCheckConfig``) turns the
audio into per-frame log-probabilities. The score is the CTC log-likelihood of
the text the take was asked to say, minus the best log-likelihood of the same
text with one ``t`` swapped for a rival (``p``, ``b``), taken at the worst
``t``. Below zero, the model hears the rival.

Measured on live takes, 2026-10-14, with the Norwegian wav2vec2 model the
slicer already loads: 7 deliberate "pa"/"adpo" renders scored -5.2 to -8.7, and
19 fresh takes of "Kaon na ta!" and "adto" split into 17 at +1.4 to +10.3 and 2
at -6.5 / -6.9. Nothing fell between -5.2 and +1.4, so the threshold is zero.

The whole-text comparison is load-bearing. The first measure scored only the
frames the aligner gave the ``t``, and on "adto" all three "adpo" controls
passed: in a ``dt`` cluster the aligner put the ``t`` on the ``d``'s frames.
Summing over every alignment (the CTC forward algorithm) asks the model the
question directly.
"""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable, Mapping

import numpy as np

from app.audio.alignment import MODEL_SAMPLE_RATE
from app.languages import TakeCheckConfig, get_take_check_for_tts_locale

logger = logging.getLogger(__name__)

# How many more takes a failed one may draw. Measured: about one take in ten
# fails, so three re-draws leave about one clip in ten thousand unfixed, and
# that one is logged.
TAKE_CHECK_REROLLS = 3


def ctc_log_likelihood(log_probs: np.ndarray, tokens: list[int], blank: int) -> float:
    """``log P(tokens | audio)`` summed over every CTC alignment (the forward algorithm).

    States are ``[blank, t0, blank, t1, …, blank]``. A state is entered from
    itself, from the one before, or from two before when that skips a blank
    between two DIFFERENT tokens; a doubled letter must keep its blank.
    ``-inf`` when the text cannot fit in the frames. *tokens* is never empty:
    a text with nothing to say has nothing to check (``TakeChecker.margin``).
    """
    ext = [blank]
    for token in tokens:
        ext += [token, blank]
    n_states = len(ext)
    can_skip = np.array([s > 1 and ext[s] != blank and ext[s] != ext[s - 2] for s in range(n_states)])
    alpha = np.full(n_states, -np.inf)
    alpha[:2] = log_probs[0, ext[:2]]
    for t in range(1, len(log_probs)):
        stay = alpha
        step = np.concatenate(([-np.inf], alpha[:-1]))
        skip = np.where(can_skip, np.concatenate(([-np.inf, -np.inf], alpha[:-2])), -np.inf)
        alpha = np.logaddexp(np.logaddexp(stay, step), skip) + log_probs[t, ext]
    return float(np.logaddexp(alpha[-1], alpha[-2]))


def swap_margin(
    log_probs: np.ndarray, text: str, vocab: Mapping[str, int], blank: int, swaps: Mapping[str, str]
) -> float | None:
    """The intended text's log-likelihood minus its best rival's, at the worst swappable character.

    ``None`` when *text* holds nothing *swaps* tests.
    """
    tokens = [vocab[ch] for ch in text]
    base = ctc_log_likelihood(log_probs, tokens, blank)
    margins = []
    for i, ch in enumerate(text):
        for rival in swaps.get(ch, ""):
            swapped = [*tokens[:i], vocab[rival], *tokens[i + 1 :]]
            margins.append(base - ctc_log_likelihood(log_probs, swapped, blank))
    return min(margins) if margins else None


def decode_audio(audio: bytes) -> np.ndarray:
    """Encoded audio (a take's MP3) as mono float32 at the model's rate, via ffmpeg."""
    proc = subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-i", "pipe:0", "-ac", "1", "-ar", str(MODEL_SAMPLE_RATE)]
        + ["-f", "f32le", "-"],
        input=audio,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg decode failed: {proc.stderr.decode()[:300]}")
    return np.frombuffer(proc.stdout, dtype=np.float32)


class TakeChecker:
    """Scores takes for one language. The model loads on the first take that needs it."""

    def __init__(self, config: TakeCheckConfig, *, decode: Callable[[bytes], np.ndarray] = decode_audio) -> None:
        self._config = config
        self._decode = decode
        self._model = None
        self._unavailable = False

    def _loaded(self):
        if self._model is None and not self._unavailable:
            try:
                self._model = self._config.model_factory(self._config.model_id)
            except Exception as e:  # noqa: BLE001 — an unloadable model must not fail a render
                self._unavailable = True
                logger.warning(
                    "TAKE_CHECK_UNAVAILABLE model=%s: %s — takes are rendered UNCHECKED for a consonant said wrong",
                    self._config.model_id,
                    e,
                )
        return self._model

    def margin(self, audio: bytes, text: str) -> float | None:
        """How surely *audio* says *text* rather than a rival, or ``None`` when it cannot be asked."""
        spelled = self._config.normalize(text)
        if spelled is None or not any(ch in self._config.swaps for ch in spelled):
            return None
        model = self._loaded()
        if model is None or any(ch not in model.vocab for ch in spelled):
            return None
        log_probs = model.log_probs(self._decode(audio))
        return swap_margin(log_probs, spelled, model.vocab, model.blank, self._config.swaps)

    @staticmethod
    def passes(margin: float | None) -> bool:
        """A take with no score passes: there was nothing the check could say against it."""
        return margin is None or margin >= 0


def checkers_by_locale() -> Callable[[str], TakeChecker | None]:
    """A locale -> checker lookup that builds each language's checker once and keeps it.

    One per process (``tts_factory``), so a model is loaded once however many
    takes a render draws.
    """
    built: dict[str, TakeChecker | None] = {}

    def lookup(locale: str) -> TakeChecker | None:
        if locale not in built:
            config = get_take_check_for_tts_locale(locale)
            built[locale] = TakeChecker(config) if config is not None else None
        return built[locale]

    return lookup
