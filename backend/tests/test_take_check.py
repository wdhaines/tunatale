"""Is a TTS take saying the consonant it was asked for? (tunatale-ve4p.29)

The owner, 2026-10-14, on Cebuano day 5: "Kaon na ta!" said PA, and "it's just
unacceptable for those to keep being broken." The check scores a take with a
character CTC model: how much more likely the audio is as the text it was asked
to say than as the same text with a ``t`` turned into ``p`` or ``b``. Measured on
live takes that day (the bead holds the table): every deliberate "pa"/"adpo"
control scored -5.2 or lower, every take the model passed scored +1.4 or higher,
and nothing fell between.

These tests need no model: the scoring is plain numpy over emissions a test can
write by hand.
"""

from __future__ import annotations

import itertools
import logging

import numpy as np
import pytest

from app.audio.take_check import TakeChecker, ctc_log_likelihood, swap_margin
from app.languages import TakeCheckConfig

_VOCAB = {"<b>": 0, "t": 1, "p": 2, "b": 3, "a": 4, "|": 5}
_BLANK = 0


def _emissions(*frames: dict[str, float]) -> np.ndarray:
    """Log-probabilities per frame: each frame names the symbols it favours; the rest share what is left."""
    out = np.full((len(frames), len(_VOCAB)), 1e-3)
    for t, frame in enumerate(frames):
        for symbol, p in frame.items():
            out[t, _VOCAB[symbol]] = p
    out /= out.sum(axis=1, keepdims=True)
    return np.log(out)


def _brute_force(log_probs: np.ndarray, tokens: list[int], blank: int) -> float:
    """Sum over every frame labelling that collapses to *tokens* — the definition CTC computes."""
    total = -np.inf
    n_frames, n_symbols = log_probs.shape
    for labels in itertools.product(range(n_symbols), repeat=n_frames):
        collapsed = [k for k, _ in itertools.groupby(labels) if k != blank]
        if collapsed == tokens:
            total = np.logaddexp(total, sum(log_probs[t, s] for t, s in enumerate(labels)))
    return float(total)


@pytest.mark.parametrize("tokens", [[1, 4], [2, 4], [4], [1, 1], [4, 1, 4]])
def test_the_likelihood_is_the_sum_over_every_alignment(tokens):
    """[1, 1] is the case the blank-skip rule exists for: a doubled letter needs a blank between."""
    log_probs = np.log(np.random.default_rng(7).dirichlet(np.ones(len(_VOCAB)), size=4))

    assert ctc_log_likelihood(log_probs, tokens, _BLANK) == pytest.approx(_brute_force(log_probs, tokens, _BLANK))


def test_text_longer_than_the_audio_cannot_be_said():
    log_probs = _emissions({"t": 0.9}, {"a": 0.9})

    assert ctc_log_likelihood(log_probs, [1, 4, 1, 4], _BLANK) == -np.inf


def test_a_take_that_says_ta_passes():
    log_probs = _emissions({"<b>": 0.9}, {"t": 0.9}, {"a": 0.9}, {"<b>": 0.9})

    assert swap_margin(log_probs, "ta", _VOCAB, _BLANK, {"t": "pb"}) > 5


def test_a_take_that_says_pa_fails():
    log_probs = _emissions({"<b>": 0.9}, {"p": 0.9}, {"a": 0.9}, {"<b>": 0.9})

    assert swap_margin(log_probs, "ta", _VOCAB, _BLANK, {"t": "pb"}) < -5


def test_the_worst_rival_is_the_one_that_counts():
    """b, not p: either is a lost t."""
    log_probs = _emissions({"b": 0.9}, {"a": 0.9})

    assert swap_margin(log_probs, "ta", _VOCAB, _BLANK, {"t": "pb"}) < -5


def test_every_t_is_tested_and_the_worst_one_decides():
    good_then_bad = _emissions({"t": 0.9}, {"a": 0.9}, {"|": 0.9}, {"p": 0.9}, {"a": 0.9})

    assert swap_margin(good_then_bad, "ta|ta", _VOCAB, _BLANK, {"t": "pb"}) < -5


def test_a_text_with_nothing_to_swap_has_no_margin():
    assert swap_margin(_emissions({"a": 0.9}), "a", _VOCAB, _BLANK, {"t": "pb"}) is None


# --- the checker: text in, model lazily, a verdict out --------------------------


class _Model:
    """An emission model that says what it was built to say, and counts how often it was asked."""

    vocab = _VOCAB
    blank = _BLANK

    def __init__(self, emissions: np.ndarray) -> None:
        self.emissions = emissions
        self.calls = 0

    def log_probs(self, samples: np.ndarray) -> np.ndarray:
        self.calls += 1
        return self.emissions


def _config(model: _Model, loads: list[str] | None = None) -> TakeCheckConfig:
    def factory(model_id: str) -> _Model:
        if loads is not None:
            loads.append(model_id)
        return model

    return TakeCheckConfig(
        model_id="test-model",
        model_factory=factory,
        swaps={"t": "pb"},
        normalize=lambda text: text.lower().strip("!") if "?" not in text else None,
    )


def _decode(audio: bytes) -> np.ndarray:
    return np.zeros(160, dtype=np.float32)


def test_the_checker_scores_a_take_against_its_text():
    says_pa = _Model(_emissions({"p": 0.9}, {"a": 0.9}))
    checker = TakeChecker(_config(says_pa), decode=_decode)

    assert checker.margin(b"audio", "Ta!") < -5


def test_the_model_loads_once_and_only_when_a_take_needs_it():
    loads: list[str] = []
    model = _Model(_emissions({"t": 0.9}, {"a": 0.9}))
    checker = TakeChecker(_config(model, loads), decode=_decode)

    assert checker.margin(b"audio", "aa") is None  # nothing to swap: no load
    assert loads == []
    checker.margin(b"audio", "ta")
    checker.margin(b"audio", "ta")
    assert loads == ["test-model"]
    assert model.calls == 2


def test_a_text_the_language_cannot_spell_for_the_model_is_not_checked():
    model = _Model(_emissions({"t": 0.9}))
    checker = TakeChecker(_config(model), decode=_decode)

    assert checker.margin(b"audio", "ta?") is None  # normalize refuses
    assert checker.margin(b"audio", "tz") is None  # z is not in the model's vocabulary
    assert model.calls == 0


def test_the_verdict_threshold_is_zero():
    """Measured gap: controls at -5.2 and below, passes at +1.4 and above."""
    checker = TakeChecker(_config(_Model(_emissions({"t": 0.9}, {"a": 0.9}))), decode=_decode)

    assert checker.passes(5.0) and checker.passes(0.0)
    assert not checker.passes(-0.1)
    assert checker.passes(None)


def test_a_model_that_fails_to_load_is_loud_and_checks_nothing(caplog):
    def broken(model_id: str):
        raise OSError("no such model")

    config = TakeCheckConfig(model_id="gone", model_factory=broken, swaps={"t": "pb"}, normalize=str.lower)
    checker = TakeChecker(config, decode=_decode)

    with caplog.at_level(logging.WARNING):
        assert checker.margin(b"audio", "ta") is None
        assert checker.margin(b"audio", "ta") is None
    assert caplog.text.count("TAKE_CHECK_UNAVAILABLE") == 1
    assert "no such model" in caplog.text


# --- the registry: which languages are checked, and how Cebuano is spelled -----


def test_cebuano_takes_are_checked_for_a_t_said_as_p_or_b():
    from app.audio.take_check import checkers_by_locale

    lookup = checkers_by_locale()
    checker = lookup("ceb-PH")

    assert isinstance(checker, TakeChecker)
    assert lookup("ceb-PH") is checker  # built once per process


def test_a_locale_with_no_take_check_gets_none():
    from app.audio.take_check import checkers_by_locale

    assert checkers_by_locale()("nb-NO") is None


def test_a_locale_no_language_declares_gets_none():
    from app.audio.take_check import checkers_by_locale

    assert checkers_by_locale()("xx-XX") is None


@pytest.mark.parametrize(
    ("text", "spelled"),
    [
        ("Kaon na ta!", "kaon|na|ta"),
        ("adto", "adto"),
        ("Kanus-a ka moabot?", "kanus|a|ka|moabot"),
        ("Napulo'g duha.", "napulo|g|duha"),
        ("…", None),
    ],
)
def test_cebuano_is_spelled_in_the_models_letters(text, spelled):
    from app.plugins.languages.ceb.take_check import spell_for_model

    assert spell_for_model(text) == spelled


@pytest.mark.ffmpeg
def test_a_take_is_decoded_to_mono_at_the_models_rate():
    import subprocess

    from app.audio.alignment import MODEL_SAMPLE_RATE
    from app.audio.take_check import decode_audio

    mp3 = subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=0.5:sample_rate=24000"]
        + ["-ac", "2", "-f", "mp3", "-"],
        capture_output=True,
        check=True,
    ).stdout

    samples = decode_audio(mp3)

    assert samples.dtype == np.float32
    # Half a second at 16 kHz, give or take the MP3 encoder's padding (~50 ms).
    # The source rate (24 kHz) would give ~12,000 and interleaved stereo ~17,600.
    assert abs(len(samples) - MODEL_SAMPLE_RATE // 2) < MODEL_SAMPLE_RATE // 10


@pytest.mark.ffmpeg
def test_audio_ffmpeg_cannot_read_raises_naming_ffmpeg():
    from app.audio.take_check import decode_audio

    with pytest.raises(RuntimeError, match="ffmpeg decode failed"):
        decode_audio(b"not audio")
