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

# A glottal stop between like vowels is a closure this long or longer: a dip of
# CLOSURE_DIP_DB under the word's loudest 10 ms frame, held. Measured against
# the owner's blind ear test, 2026-10-14 (tunatale-ve4p.24): 17 of 18 takes of
# miinom and kaayo agreed, the one that lacked the stop measured 20 ms, and the
# one disagreement was a take heard WITH the stop that also measured 20 ms — a
# false alarm, which costs one more take.
CLOSURE_MIN_MS = 40
CLOSURE_DIP_DB = 20.0
# The word is the frames within this many dB of its peak; quieter is the
# silence around it, which is no closure.
_WORD_FLOOR_DB = 30.0
_FRAME = MODEL_SAMPLE_RATE // 100  # 10 ms

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


def glottal_closure_ms(samples: np.ndarray) -> int:
    """The longest dip inside the word, in ms: frames ``CLOSURE_DIP_DB`` or more under its peak.

    *samples* is mono at the model's rate. Any oral stop (k, d, t) reads as a
    closure too, so this says something only about a word with none where the
    stop should be — which is why a language chooses the texts it screens.
    """
    n = len(samples) // _FRAME
    frames = samples[: n * _FRAME].astype(np.float64).reshape(n, _FRAME)
    db = 20 * np.log10(np.sqrt((frames**2).mean(axis=1)) + 1e-9)
    peak = db.max()
    word = np.flatnonzero(db > peak - _WORD_FLOOR_DB)
    longest = run = 0
    for level in db[word[0] : word[-1] + 1]:
        run = run + 1 if level < peak - CLOSURE_DIP_DB else 0
        longest = max(longest, run)
    return longest * 10


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
        """How well *audio* says *text*: below zero fails. ``None`` when nothing could be asked.

        The worst of the checks that apply: the consonant swap (``swap_margin``)
        and, for a text the language screens, the glottal closure, scored as
        tens of ms over ``CLOSURE_MIN_MS``. Both are zero at their own line, so
        the lower one decides, and a re-draw keeps the take that scored best.
        """
        samples: list[np.ndarray] = []

        def decoded() -> np.ndarray:
            if not samples:
                samples.append(self._decode(audio))
            return samples[0]

        scores = []
        screen = self._config.closure_screen
        if screen is not None and screen(text):
            scores.append((glottal_closure_ms(decoded()) - CLOSURE_MIN_MS) / 10)
        swapped = self._swap_margin(decoded, text)
        if swapped is not None:
            scores.append(swapped)
        return min(scores) if scores else None

    def _swap_margin(self, decoded: Callable[[], np.ndarray], text: str) -> float | None:
        spelled = self._config.normalize(text)
        if spelled is None or not any(ch in self._config.swaps for ch in spelled):
            return None
        model = self._loaded()
        if model is None or any(ch not in model.vocab for ch in spelled):
            return None
        log_probs = model.log_probs(decoded())
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
