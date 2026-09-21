"""The full-lesson export streams; the timeline it produces does not change.

bd tunatale-rwkz.2, lever 2. The export was the single longest stage of the
2026-09-19 renders (``Full lesson export → 1063853 ms`` — ~18 of the 47 minutes)
and the largest allocation in the process: the whole lesson was concatenated
into one float32 buffer (~340 MB for 58.6 minutes at 24 kHz), then serialised
into a WAV ``BytesIO`` and copied again by ``.getvalue()`` for ffmpeg's stdin.
On a 953 MB box that is what pushed a render into swap.

⚠️ THE OBVIOUS FIX IS WRONG, AND IT WAS MEASURED, not reasoned about. The bead
proposed joining the already-encoded section files with ffmpeg's concat demuxer.
A control (three segments, impulse at each midpoint, decoded at Opus's native
48 kHz) says:

    expected impulses : [24000, 64800, 89500]   total 97400 frames
    one encode        : [24000, 64800, 89499]   total 97400   ← exact
    concat -c copy    : [24000, 65760, 91420]   total 99320   ← +20 ms per seam

Every segment boundary adds an Opus frame of padding, so the drift ACCUMULATES:
with seven sections and their boundaries the end of a lesson lands ~300 ms late
and every cue after the first is progressively wrong. Worse, the concatenated
file is chained Ogg streams, which ``soundfile`` refuses outright ("Supported
file format but file is malformed") even though ffmpeg wrote it with exit 0 —
so our own reader could not open the result.

So the encode stays ONE continuous encode, and only the plumbing changes: the
PCM is fed to ffmpeg piece by piece instead of being concatenated first. The
timeline is therefore identical by construction, which is what the pinned cue
table below exists to prove rather than assert.
"""

from __future__ import annotations

from io import BytesIO
from unittest.mock import AsyncMock

import numpy as np
import pytest
import soundfile as sf

from app.audio.renderer import LessonRenderer, NaturalPauseCalculator
from app.models.lesson import Lesson, Phrase, Section, SectionType
from app.plugins.languages.sl.preprocessor import SlovenePreprocessor

# Every test here drives a real ffmpeg, so the whole module carries the marker
# CI's two hostile-timezone jobs deselect with `-m "not ffmpeg"` — they do not
# install the binary at all. Without this the module reddened both of them with
# FileNotFoundError while the `backend` job stayed green (run 35479824454).
pytestmark = pytest.mark.ffmpeg

_RATE = 11025

# Per-phrase synthetic durations, so every cue below is a consequence of numbers
# fixed in this file and not of anything the TTS happened to return.
_DURATIONS_MS = {"ena": 180, "dve": 240, "tri": 300, "stiri": 150, "pet": 210}

# ── the locked oracle ────────────────────────────────────────────────────────
#
# MEASURED against the pre-change renderer on 2026-09-19 and pinned here, in the
# order (index, start_ms, end_ms, section_index, phrase_index). This is the
# manifest the reader highlights against: a drift of even one Opus frame per
# seam would move every row after the first, which is exactly the failure the
# rejected concat approach produces. Do not regenerate these numbers to make a
# change pass — if they move, the change moved the audio.
_PINNED_CUES = [
    (0, 0, 120, None, 0),
    (1, 3120, 3300, 0, 0),
    (2, 3800, 4040, 0, 1),
    (3, 7540, 7840, 1, 0),
    (4, 11440, 11590, 2, 0),
    (5, 12090, 12300, 2, 1),
]


def _wav_bytes(duration_ms: int) -> bytes:
    buf = BytesIO()
    frames = round(duration_ms / 1000 * _RATE)
    sf.write(buf, np.full((frames, 1), 0.25, dtype="float32"), _RATE, format="WAV", subtype="PCM_16")
    return buf.getvalue()


def _lesson() -> Lesson:
    def section(section_type, *texts):
        return Section(
            section_type=section_type,
            phrases=[Phrase(text=t, voice_id="sl-SI-PetraNeural", language_code="sl") for t in texts],
        )

    # Three sections, so the inter-section boundaries — the seams the rejected
    # concat approach corrupts — are actually exercised. No KEY_PHRASES section:
    # its cue builder needs lesson.key_phrases to match, which is a different
    # invariant and not this test's subject.
    return Lesson(
        title="Pinned",
        language_code="sl",
        sections=[
            section(SectionType.NATURAL_SPEED, "ena", "dve"),
            section(SectionType.SLOW_SPEED, "tri"),
            section(SectionType.TRANSLATED, "stiri", "pet"),
        ],
    )


@pytest.fixture
def tts():
    async def synthesize(text, voice_id, output_path, rate="+0%", phonemes=None, speak_locale=None):
        ms = next((v for k, v in _DURATIONS_MS.items() if k in text), 120)
        output_path.write_bytes(_wav_bytes(ms))

    mock = AsyncMock()
    mock.synthesize = synthesize
    return mock


def _renderer(tts, codec="opus"):
    return LessonRenderer(
        tts=tts,
        preprocessors={"sl": SlovenePreprocessor()},
        pause_calculator=NaturalPauseCalculator(),
        delivery_codec=codec,
        delivery_bitrate="28k",
    )


class TestTheTimelineIsUnchanged:
    async def test_the_cue_manifest_matches_the_pinned_table(self, tts, tmp_path):
        """The whole point of the bead's 'hard oracle'."""
        cues = await _renderer(tts).render(
            _lesson(),
            tmp_path / "full.opus",
            section_paths=[tmp_path / f"s{i}.opus" for i in range(3)],
        )

        actual = [(c.index, c.start_ms, c.end_ms, c.section_index, c.phrase_index) for c in cues]
        assert actual == _PINNED_CUES

    async def test_the_file_is_still_one_readable_ogg_stream(self, tts, tmp_path):
        """⚠️ Directly pins what the rejected concat approach broke.

        ``soundfile`` refuses chained Ogg streams, and it is what ``_read_audio``
        uses — so a file that only ffmpeg can open would pass a "the bytes start
        with OggS" check and still be unusable by the app itself.
        """
        out = tmp_path / "full.opus"
        await _renderer(tts).render(_lesson(), out)

        assert out.read_bytes()[:4] == b"OggS"
        samples, rate = sf.read(str(out), dtype="float32", always_2d=True)
        # NOT 48 kHz: libsndfile reports the stream's own rate, and Opus rounds
        # this fixture's 11025 up to its nearest supported 12000. Asserting 48000
        # here failed against the UNCHANGED renderer — a wrong expectation in the
        # test, caught by running it before touching the product.
        assert rate > 0
        decoded_ms = len(samples) / rate * 1000
        # The audio runs PAST the last cue: a lesson ends with the pause after
        # its final phrase plus a section boundary. The bound that matters is
        # that nothing extra accumulated — a per-seam drift would show here as
        # hundreds of ms beyond the trailing pause.
        assert decoded_ms >= _PINNED_CUES[-1][2]
        assert decoded_ms < _PINNED_CUES[-1][2] + 1000

    async def test_wav_delivery_still_works(self, tts, tmp_path):
        """The uncompressed path has no ffmpeg in it and must keep working —
        it is what the tests above use as ground truth elsewhere in the suite."""
        out = tmp_path / "full.wav"
        cues = await _renderer(tts, codec="wav").render(_lesson(), out)

        samples, rate = sf.read(str(out), dtype="float32", always_2d=True)
        assert rate == _RATE
        assert [(c.index, c.start_ms, c.end_ms, c.section_index, c.phrase_index) for c in cues] == _PINNED_CUES
        # WAV is exact, so this pins the trailing pause too: measured 499.8 ms
        # past the last cue against the unchanged renderer. A seam drift would
        # move it, and unlike the Opus case there is no codec padding to blame.
        assert 499.0 < len(samples) / rate * 1000 - _PINNED_CUES[-1][2] < 500.5


class TestSectionFilesKeepTheirLength:
    """bd tunatale-rwkz.5: the per-section files stream too, and stay exactly as long.

    The reader highlights a section file against cues REBASED from the full
    lesson (render_service::derive_section_cues). Those are computed, so no
    encode change can move them; what an encode change CAN move is the file's
    own length, and then every rebased cue stops lining up with the audio.

    MEASURED against the pre-change (buffered) renderer on 2026-09-21:
        wav  frames 15654 / 9923 / 14993 at 11025 Hz
        opus 1419.92 / 900.08 / 1359.92 ms, each within 0.1 ms of its WAV twin
    WAV is pinned exactly. Opus is pinned RELATIVE to WAV (within 5 ms) rather
    than by frame count, because end-trimming can differ between ffmpeg
    versions and CI's is not this Mac's; 5 ms still catches the failure that
    matters, one Opus frame (20 ms) of padding.
    """

    _WAV_FRAMES = [15654, 9923, 14993]

    async def _render(self, tts, tmp_path, codec):
        ext = "wav" if codec == "wav" else "opus"
        paths = [tmp_path / f"s{i}.{ext}" for i in range(3)]
        await _renderer(tts, codec=codec).render(_lesson(), tmp_path / f"full.{ext}", section_paths=paths)
        return paths

    async def test_wav_sections_are_exactly_the_pinned_length(self, tts, tmp_path):
        paths = await self._render(tts, tmp_path, "wav")
        assert [sf.info(str(p)).frames for p in paths] == self._WAV_FRAMES

    async def test_opus_sections_match_the_pcm_length_and_open_in_soundfile(self, tts, tmp_path):
        (tmp_path / "w").mkdir()
        (tmp_path / "o").mkdir()
        wav = await self._render(tts, tmp_path / "w", "wav")
        opus = await self._render(tts, tmp_path / "o", "opus")
        for w, o in zip(wav, opus, strict=True):
            wi = sf.info(str(w))
            # sf.info on the Opus file is also the "one readable Ogg stream"
            # check: soundfile refuses chained Ogg outright.
            oi = sf.info(str(o))
            assert o.read_bytes()[:4] == b"OggS"
            assert abs(oi.frames / oi.samplerate - wi.frames / wi.samplerate) * 1000 < 5, o.name


class TestTheStreamingEncoder:
    """``encode_audio_stream`` is the seam that removes the big allocation.

    ⚠️ Tested DIRECTLY rather than by spying on the renderer. A spy would mean
    ``monkeypatch.setattr("app.audio.renderer...")``, which the mock-boundary
    rule forbids — and it would be testing the patch. What the renderer does
    with it is covered by the timeline tests above; what it guarantees is here.
    The peak-memory claim itself is measured out of band (see the PR), because
    numpy buffers are allocated outside tracemalloc's view and a unit test that
    pretended to measure them would be decoration.

    ⚠️ CORRECTED 2026-09-21 (bd tunatale-rwkz.5): that holds for the numpy
    buffers, but NOT for the copy this writer used to make. ``.tobytes()``
    builds a Python ``bytes`` object, which tracemalloc does see, and for a
    one-chunk call (a whole section) it was a full float32 copy: measured 57.9
    MB extra for a 10-minute section, against 32.4 MB for the old buffered
    path. The zero-copy test below guards exactly that.
    """

    def test_a_large_chunk_is_written_without_copying_it(self, tmp_path):
        """A section is streamed as ONE chunk, so a per-chunk copy is a copy of
        the whole section. Control: the chunk's own size, which a copy would
        reach and a view does not come near."""
        import tracemalloc

        from app.audio.transcode import encode_audio_stream

        rate = 24000
        chunk = np.zeros((rate * 20, 1), dtype="float32")  # 20 s, 1.92 MB
        tracemalloc.start()
        encode_audio_stream(iter([chunk]), rate, "opus", "28k", tmp_path / "big.opus")
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        assert peak < chunk.nbytes * 0.25, f"peak {peak} bytes for a {chunk.nbytes}-byte chunk: it was copied"

    def test_it_consumes_a_generator_lazily(self, tmp_path):
        """A generator, not a list: taking a sequence would let a caller build
        the whole lesson first and still satisfy the signature."""
        from app.audio.transcode import encode_audio_stream

        produced: list[int] = []

        def chunks():
            for i in range(4):
                produced.append(i)
                yield np.full((1000, 1), 0.1, dtype="float32")

        gen = chunks()
        assert produced == [], "the generator must not be drained before encoding starts"
        encode_audio_stream(gen, 12000, "opus", "28k", tmp_path / "out.opus")

        assert produced == [0, 1, 2, 3]
        assert (tmp_path / "out.opus").read_bytes()[:4] == b"OggS"

    def test_the_pieces_arrive_as_one_continuous_stream(self, tmp_path):
        """Four chunks in, one unbroken timeline out — the property the
        rejected concat approach fails. An impulse in the final chunk must land
        where the chunk lengths say, not 20 ms per seam later."""
        from app.audio.transcode import encode_audio_stream

        rate = 12000
        pieces = [np.zeros((rate // 2, 1), dtype="float32") for _ in range(4)]
        pieces[3][100, 0] = 0.9
        out = tmp_path / "out.opus"

        encode_audio_stream(iter(pieces), rate, "opus", "64k", out)

        samples, out_rate = sf.read(str(out), dtype="float32", always_2d=True)
        scale = out_rate / rate
        expected = (3 * (rate // 2) + 100) * scale
        loudest = int(np.argmax(np.abs(samples[:, 0])))
        assert abs(loudest - expected) < 0.02 * out_rate, f"impulse at {loudest}, expected ~{expected:.0f}"

    def test_a_failing_encode_raises_rather_than_writing_a_stub(self, tmp_path):
        """Same contract ``encode_audio`` already has: a bad config must fail
        loudly, not leave a truncated file that plays as silence."""
        from app.audio.transcode import encode_audio_stream

        out = tmp_path / "out.opus"
        with pytest.raises(RuntimeError, match="ffmpeg"):
            encode_audio_stream(iter([np.zeros((1000, 1), dtype="float32")]), 12000, "opus", "not-a-bitrate", out)

    def test_stereo_input_is_refused_rather_than_mis_declared(self, tmp_path):
        """⚠️ The channel count is declared to ffmpeg BEFORE any audio arrives.

        Every renderer path is mono, so the declaration is a constant — and a
        stereo buffer slipping in would be interpreted as mono at twice the
        length, playing everything at half speed with nothing raised. Refused at
        the door instead, because the symptom is otherwise an audio bug with no
        error anywhere near it.
        """
        from app.audio.transcode import encode_audio_stream

        with pytest.raises(ValueError, match="mono-only"):
            encode_audio_stream(
                iter([np.zeros((1000, 2), dtype="float32")]), 12000, "opus", "28k", tmp_path / "out.opus"
            )
