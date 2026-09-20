"""ffmpeg runs at low CPU priority so the API keeps answering during a render.

bd tunatale-rwkz.2 follow-up, from a user observation on 2026-09-19: "The test
makes the site very slow to respond." Measured at the time — load average 3.30
on a 2-shared-vCPU box, with section exports taking 102 s against 40 s for the
same unchanged code on an idle box. A render is meant to be background work; on
this hardware it was taking the foreground with it.

The CPU goes almost entirely into ffmpeg CHILD processes, which is what makes
this cheap: they can be deprioritised without touching the API's own scheduling.
The kernel then hands the API whatever it asks for and lets ffmpeg have the
rest, so a render gets slower and the site stops stuttering — the trade the user
asked for.

⚠️ ``os.setpriority`` from the PARENT, not ``preexec_fn``. preexec_fn runs
between fork and exec in a process that here is multi-threaded (the encode is
already inside ``asyncio.to_thread``), where the CPython docs warn it can
deadlock. Renicing after spawn leaves the child at normal priority for a few
milliseconds, which is nothing against a multi-minute encode.
"""

from __future__ import annotations

import os

import numpy as np
import pytest

from app.audio.transcode import encode_audio, encode_audio_stream
from app.config import settings

pytestmark = pytest.mark.ffmpeg


@pytest.fixture
def calls(monkeypatch):
    """Records os.setpriority calls. A stdlib boundary, not an app internal."""
    recorded: list[tuple[int, int, int]] = []
    real = os.setpriority

    def spy(which, who, priority):
        recorded.append((which, who, priority))
        return real(which, who, priority)

    monkeypatch.setattr(os, "setpriority", spy)
    return recorded


def _samples(frames: int = 4000) -> np.ndarray:
    return np.zeros((frames, 1), dtype="float32")


class TestFfmpegIsDeprioritised:
    def test_the_streaming_encoder_renices_its_child(self, calls, tmp_path):
        encode_audio_stream(iter([_samples()]), 12000, "opus", "28k", tmp_path / "out.opus")

        assert calls, "ffmpeg ran at the API's own priority"
        which, pid, priority = calls[-1]
        assert which == os.PRIO_PROCESS
        assert pid > 0
        assert priority == settings.ffmpeg_nice

    def test_the_buffered_encoder_renices_too(self, calls):
        """Section files and clip encodes go through this one — they were 39% of
        a render's wall time, so exempting them would leave most of the problem."""
        encode_audio(_samples(), 12000, "opus", "28k")

        assert calls, "ffmpeg ran at the API's own priority"
        assert calls[-1][2] == settings.ffmpeg_nice

    def test_the_nice_value_is_a_real_deprioritisation(self):
        """A positive value is LOWER priority on POSIX. A zero or negative
        default would read as configured while doing nothing (or worse, would
        need privileges and fail)."""
        assert settings.ffmpeg_nice > 0

    def test_encoding_survives_a_refused_renice(self, monkeypatch, tmp_path):
        """⚠️ Priority is an optimisation; audio is the product.

        A platform or sandbox that refuses setpriority must not cost the learner
        their lesson audio — the encode continues at normal priority instead.
        """

        def refuse(which, who, priority):
            raise OSError(1, "Operation not permitted")

        monkeypatch.setattr(os, "setpriority", refuse)
        out = tmp_path / "out.opus"

        encode_audio_stream(iter([_samples()]), 12000, "opus", "28k", out)

        assert out.read_bytes()[:4] == b"OggS"
