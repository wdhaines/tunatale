"""Norwegian forced-alignment adapter — the only place transformers is imported.

Deliberately thin, and deliberately the whole of the optional-dependency
surface: everything with logic in it (the Viterbi, the frame→sample mapping, the
slicing) lives in ``app.audio.alignment`` / ``app.audio.slicer`` and is covered
by model-free tests. This module is excluded from coverage for the same reason
``anki_sync/sync_driver.py`` is — CI runs torch-free by design
(``--no-group slovene --no-group norwegian``), so its body cannot execute there.
Keep it that way: no decisions belong here.

Install with ``uv sync --extra alignment``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from app.audio.alignment import ctc_align

if TYPE_CHECKING:
    from app.audio.alignment import CharAligner

# Character-level CTC over the lowercase Norwegian alphabet: 32 tokens
# (a–z + å æ ø + "|" + [UNK] + [PAD]). Our syllabification is orthographic, so
# ``ha|gen`` is characters [0:2] / [2:5] and the boundary is looked up rather
# than estimated — no phoneme dictionary and no G2P step in between.
MODEL_ID = "NbAiLab/nb-wav2vec2-300m-bokmaal"

NORWEGIAN_VOWELS = frozenset("aeiouyæøå")


class Wav2Vec2CharAligner:
    """Reports which frames each character of a word occupies."""

    def __init__(self, model_name: str) -> None:
        import torch
        from transformers import AutoModelForCTC, Wav2Vec2Processor

        self._torch = torch
        # Wav2Vec2Processor explicitly, NOT AutoProcessor: the NbAiLab repos ship
        # an n-gram decoder, so Auto resolves to Wav2Vec2ProcessorWithLM and then
        # demands pyctcdecode. Forced alignment scores a KNOWN transcript, so a
        # decoder LM is not merely unnecessary — involving one would be wrong.
        self._processor = Wav2Vec2Processor.from_pretrained(model_name)
        self._model = AutoModelForCTC.from_pretrained(model_name).eval()
        self._vocab = self._processor.tokenizer.get_vocab()
        blank = self._model.config.pad_token_id
        self._blank = self._vocab["[PAD]"] if blank is None else blank

    def supports(self, word: str) -> bool:
        return all(ch in self._vocab for ch in word.lower())

    def char_spans(self, samples: np.ndarray, word: str) -> tuple[list[tuple[int, int]], int]:
        from app.audio.alignment import MODEL_SAMPLE_RATE

        inputs = self._processor(samples, sampling_rate=MODEL_SAMPLE_RATE, return_tensors="pt")
        with self._torch.no_grad():
            logits = self._model(inputs.input_values).logits[0]
        log_probs = self._torch.log_softmax(logits, dim=-1).numpy().astype(np.float64)
        tokens = [self._vocab[ch] for ch in word.lower()]
        return ctc_align(log_probs, tokens, self._blank), log_probs.shape[0]


def create_aligner(model_name: str = MODEL_ID) -> CharAligner:
    """Load the aligner. Called at most once per process by ``ChunkSlicer``."""
    return Wav2Vec2CharAligner(model_name)
