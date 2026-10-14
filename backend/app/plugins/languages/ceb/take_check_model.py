"""The take check's emission model — the only place this plugin imports transformers.

Thin on purpose, and excluded from coverage for the reason
``app/plugins/languages/no/alignment.py`` is: running it means downloading the
model. Every decision (the spelling, the scoring, the threshold, what happens on
a failure to load) lives in ``take_check.py`` and ``app.audio.take_check``,
both covered by model-free tests.
"""

from __future__ import annotations

import numpy as np


class Wav2Vec2Emissions:
    """Per-frame log-probabilities over the model's characters."""

    def __init__(self, model_id: str) -> None:
        import torch
        from transformers import AutoModelForCTC, Wav2Vec2Processor

        self._torch = torch
        # Wav2Vec2Processor, not AutoProcessor: the repo ships an n-gram decoder
        # that Auto would load (and demand pyctcdecode for). A known text is being
        # scored, so a language model has no business in it.
        self._processor = Wav2Vec2Processor.from_pretrained(model_id)
        self._model = AutoModelForCTC.from_pretrained(model_id).eval()
        self.vocab = self._processor.tokenizer.get_vocab()
        blank = self._model.config.pad_token_id
        self.blank = self.vocab["[PAD]"] if blank is None else blank

    def log_probs(self, samples: np.ndarray) -> np.ndarray:
        from app.audio.alignment import MODEL_SAMPLE_RATE

        inputs = self._processor(samples, sampling_rate=MODEL_SAMPLE_RATE, return_tensors="pt")
        with self._torch.no_grad():
            logits = self._model(inputs.input_values).logits[0]
        return self._torch.log_softmax(logits, dim=-1).numpy().astype(np.float64)


def create_emission_model(model_id: str) -> Wav2Vec2Emissions:
    return Wav2Vec2Emissions(model_id)
