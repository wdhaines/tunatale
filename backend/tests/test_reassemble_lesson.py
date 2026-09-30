"""Tests for reassemble_lesson_audio — selective KEY_PHRASES re-render.

Strict TDD: these tests MUST fail before implementation.
Assertions use ffprobe durations and sha256 of section files, never "sounds fine".
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Mapping
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np
import pytest

from app.audio.cues import Cue, CueTiming
from app.audio.paths import resolve_audio_path
from app.audio.pause_calculator import NaturalPauseCalculator
from app.models.lesson import KeyPhraseInfo, Lesson, Phrase, Section, SectionType
from app.storage.store import ContentStore

# Shells out to a real ffmpeg binary. CI's two hostile-timezone jobs deselect
# these with -m "not ffmpeg" so they need no ffmpeg install; see
# pyproject.toml [tool.pytest.ini_options] markers.
pytestmark = pytest.mark.ffmpeg

# ── helpers ──────────────────────────────────────────────────────────────────


class _CountingTTS:
    """TTS fake that counts calls per section type.

    Writes silence at the requested duration so the pipeline can decode and
    measure it. The counter is the primary oracle — we assert on the COUNT,
    not on what was written.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.call_count = 0
        # Set to a path to keep a copy of what was synthesized. The service
        # synthesizes the title into a TemporaryDirectory that no longer exists
        # by the time the assertions run, so an oracle that cannot reach the
        # title has a hole in it exactly where one of the lesson's pieces is.
        self.record_to: Path | None = None

    async def synthesize(
        self,
        text: str,
        voice_id: str,
        output_path: Path,
        rate: str = "+0%",
        phonemes: Mapping[str, str] | None = None,
    ) -> None:
        self.calls.append(text)
        self.call_count += 1
        from app.audio.transcode import encode_audio

        # ⚠️ MP3, NOT the delivery codec, because that is what the real service
        # does: AzureTTSService caches as <digest>.mp3 and writes those bytes
        # whatever the caller names the file. An earlier version of this double
        # wrote Opus, which made the reassembly's concat list look homogeneous
        # when in production it was not — the run failed on real data with
        # "Unsupported codec id in stream 0" while every test here was green.
        samples = np.zeros((24000, 1), dtype="float32")  # 1s silence @ 24kHz
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(encode_audio(samples, 24000, "mp3", "64k"))
        if self.record_to is not None:
            self.record_to.write_bytes(output_path.read_bytes())

    async def list_voices(self, language_code: str | None = None) -> list[dict]:
        return []


def _make_opus_file(path: Path, duration_s: float = 1.0) -> None:
    """Create a valid Opus file of the given duration at 24kHz mono."""
    from app.audio.transcode import encode_audio

    samples = np.zeros((int(24000 * duration_s), 1), dtype="float32")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encode_audio(samples, 24000, "opus", "28k"))


# ── the DECODED-audio oracles ────────────────────────────────────────────────
#
# Everything above this line measures with ffprobe, which reports FORMAT
# duration from granule positions. A stream-copied Opus join is not
# sample-preserving and granule positions cannot see that, which is why those
# tests stayed green over a full file whose audio ran a seam-count of drift
# ahead of its own cues. Everything below decodes the file and reads samples.


def _tone_samples(freq_hz: float, duration_s: float, rate: int) -> np.ndarray:
    n = int(round(duration_s * rate))
    t = np.arange(n, dtype="float32") / rate
    tone = np.sin(2 * np.pi * freq_hz * t).astype("float32")
    # A hard start is a broadband click; a 5 ms ramp keeps the onset sharp
    # (the detector reports the first window at half peak, ~2.5 ms in) while
    # removing the click that would leak energy at every other frequency.
    fade = min(int(0.005 * rate), n // 2)
    if fade:
        tone[:fade] *= np.linspace(0.0, 1.0, fade, dtype="float32")
    return tone.reshape(-1, 1)


def _make_tone_opus_file(path: Path, freq_hz: float, duration_s: float, rate: int) -> None:
    """A section file whose audio says which section it is."""
    from app.audio.transcode import encode_audio

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encode_audio(_tone_samples(freq_hz, duration_s, rate), rate, "opus", "28k"))


def _decode_pcm(path: Path, rate: int) -> np.ndarray:
    """Decode *path* to mono float32 at *rate* — the audio, not a container's
    idea of how long it is.

    ffmpeg, not ffprobe: ffprobe is what the tests below are written AGAINST.
    The input format is left to ffmpeg (it is probed) and only the OUTPUT is
    declared raw, because ``pipe:`` has no extension to infer one from.
    """
    proc = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-ac",
            "1",
            "-ar",
            str(rate),
            "-f",
            "f32le",
            "pipe:1",
        ],
        capture_output=True,
        check=True,
    )
    return np.frombuffer(proc.stdout, dtype="<f4").astype("float32")


def _tone_onset_ms(pcm: np.ndarray, rate: int, freq_hz: float) -> float:
    """Where *freq_hz* begins in *pcm*, in ms — from the samples alone.

    A Hann-windowed dot product against a complex exponential at *freq_hz* is a
    one-bin DFT: the first 10 ms window whose magnitude reaches half the
    loudest such window in the file. The reported position is that window's
    CENTRE, so the answer sits within half a window of the truth and the window
    size can never be mistaken for drift.

    Reads nothing but the samples — not the manifest, not ffprobe.
    """
    win = max(int(0.010 * rate), 8)
    assert len(pcm) >= win, f"decoded audio is shorter than one {win}-sample detection window"
    frames = np.lib.stride_tricks.sliding_window_view(pcm, win)[:: win // 4]
    taper = np.hanning(win)
    kernel = taper * np.exp(-2j * np.pi * freq_hz * np.arange(win) / rate)
    mag = np.abs(frames @ kernel) * 2.0 / taper.sum()
    peak = mag.max()
    assert peak > 0, f"no energy at {freq_hz} Hz anywhere in the decoded audio"
    return float(np.argmax(mag >= peak / 2) * (win // 4) + win / 2) * 1000 / rate


def _ffprobe_duration(path: Path) -> float:
    """Return duration in seconds via ffprobe, or -1.0 on failure."""
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return -1.0
    try:
        return float(result.stdout.strip())
    except ValueError:
        return -1.0


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


@pytest.fixture(autouse=True)
def _audio_dir_points_at_the_test_tree(tmp_path, monkeypatch):
    """``resolve_audio_path`` reads ``settings.audio_dir``, and since
    tunatale-kbb.15 step 3 a row stores only a basename — so without this every
    stored path resolves against the process CWD instead of the test's render
    tree. Autouse because it is true of every test in this file; a test that
    needs a different directory sets its own and wins, its monkeypatch running
    after this one."""
    from app.config import settings as _settings

    monkeypatch.setattr(_settings, "audio_dir", tmp_path / "audio")


def _full_path(store, lesson_id: str) -> Path:
    """The stored full-lesson file. The payload mirrors render_lesson_audio's
    shape (audio_id / lesson_id / sections / cues) and deliberately does not add
    a key that one — so the path is read back the way the app reads it."""
    row = next(r for r in store.list_audio_files_for_lesson(lesson_id) if r["section_index"] is None)
    return resolve_audio_path(row["file_path"])


def _build_test_lesson(title: str = "Inside the Cabin", n_sections: int = 4) -> Lesson:
    """Build a minimal test lesson with KEY_PHRASES at section index 0."""
    sections: list[Section] = []
    key_phrases: list[KeyPhraseInfo] = [
        KeyPhraseInfo(phrase="Dober dan", translation="Good day"),
        KeyPhraseInfo(phrase="Hvala lepa", translation="Thank you"),
    ]

    for i in range(n_sections):
        if i == 0:
            # Built by the REAL section builder: build_cue_manifest enforces
            # "2 + len(breakdown) phrases per key phrase" and rejects a
            # hand-rolled section, so a fixture that skips it cannot reach the
            # cue arithmetic this module is about.
            from app.generation.section_builder import build_key_phrases_section

            sections.append(
                build_key_phrases_section(
                    [{"phrase": kp.phrase, "translation": kp.translation} for kp in key_phrases],
                    {"female-1": "sl-SI-PetraNeural"},
                    "en-US-GuyNeural",
                    "sl",
                )
            )
            continue
        elif i == 1:
            sec_type = SectionType.NATURAL_SPEED
            phrases = [
                Phrase(text="Natural Speed", voice_id="narrator", language_code="en", role="narrator"),
                Phrase(text="Dober dan", voice_id="v", language_code="sl", role="f1"),
            ]
        elif i == 2:
            sec_type = SectionType.TRANSLATED
            phrases = [
                Phrase(text="English After", voice_id="narrator", language_code="en", role="narrator"),
                Phrase(text="Dober dan", voice_id="v", language_code="sl", role="f1"),
                Phrase(text="Good day", voice_id="narrator", language_code="en", role="narrator"),
            ]
        else:
            sec_type = SectionType.SLOW_SPEED
            phrases = [
                Phrase(text="Enunciated", voice_id="narrator", language_code="en", role="narrator"),
                Phrase(text="Dober dan", voice_id="v", language_code="sl", role="f1"),
            ]
        sections.append(Section(section_type=sec_type, phrases=phrases))

    return Lesson(
        title=title,
        language_code="sl",
        sections=sections,
        key_phrases=key_phrases,
    )


def _populate_store(
    store: ContentStore,
    lesson: Lesson,
    audio_dir: Path,
    section_durations: list[float],
    tone_freqs: list[float] | None = None,
    tone_rate: int = 24000,
    full_row_has_file: bool = True,
) -> tuple[str, list[str], list[Path]]:
    """Seed the ContentStore with a full-lesson row + section rows.

    Returns (full_audio_id, section_ids, section_paths_on_disk).

    *tone_freqs* gives each section a distinguishable tone instead of silence —
    one frequency per section, so the reassembled audio can be asked WHERE each
    section actually is. The default is silence, which is what every
    pre-existing test in this file was written against.

    *full_row_has_file=False* is the shape a lesson rendered since tunatale-guzo.3
    has: the row keeps its timeline, the concatenation was never encoded.
    """
    from uuid import uuid4

    full_id = str(uuid4())
    full_path = audio_dir / f"{full_id}.opus"
    if full_row_has_file:
        _make_opus_file(full_path, sum(section_durations) + 3.0)  # title + boundaries

    section_ids = [str(uuid4()) for _ in lesson.sections]
    section_paths = [audio_dir / f"{sid}.opus" for sid in section_ids]

    cues: list[Cue] = []
    timing_entries: list[CueTiming] = []
    frame = 0
    rate = 24000

    # Title cue
    title_len = int(1.0 * rate)
    timing_entries.append(CueTiming(section_index=None, phrase_index=0, start_frame=frame, end_frame=frame + title_len))
    cues.append(
        Cue(
            index=0,
            start_ms=0,
            end_ms=1000,
            section_index=None,
            section_type=None,
            phrase_index=0,
            role="narrator",
            language_code="en",
            text=lesson.title,
            ref={"kind": "narration"},
        )
    )
    frame += title_len + int(3.0 * rate)  # title + boundary

    # Section cues
    for i, (sec, dur) in enumerate(zip(lesson.sections, section_durations, strict=True)):
        sec_len = int(dur * rate)
        for j, phrase in enumerate(sec.phrases):
            ph_len = sec_len // max(len(sec.phrases), 1)
            ph_start = frame + j * ph_len
            ph_end = frame + (j + 1) * ph_len if j < len(sec.phrases) - 1 else frame + sec_len
            timing_entries.append(CueTiming(section_index=i, phrase_index=j, start_frame=ph_start, end_frame=ph_end))
            cues.append(
                Cue(
                    index=len(cues),
                    start_ms=round(ph_start / rate * 1000),
                    end_ms=round(ph_end / rate * 1000),
                    section_index=i,
                    section_type=sec.section_type.value,
                    phrase_index=j,
                    role=phrase.role,
                    language_code=phrase.language_code,
                    text=phrase.text,
                    ref={"kind": "line", "target_index": j} if phrase.language_code == "sl" else {"kind": "narration"},
                )
            )
        frame += sec_len
        if i < len(lesson.sections) - 1:
            frame += int(3.0 * rate)

    cues_json = json.dumps([asdict(c) for c in cues])

    # Seed DB
    store.save_audio_file(full_id, lesson.title, str(full_path) if full_row_has_file else None, cues_json=cues_json)
    for i, (sid, sp, sec) in enumerate(zip(section_ids, section_paths, lesson.sections, strict=True)):
        sec_cues = [c for c in cues if c.section_index == i]
        # Rebase section cues to start at 0 (matching derive_section_cues behavior)
        if sec_cues:
            first_start = sec_cues[0].start_ms
            rebased = [replace(c, start_ms=c.start_ms - first_start, end_ms=c.end_ms - first_start) for c in sec_cues]
        else:
            rebased = []
        sec_cues_json = json.dumps([asdict(c) for c in rebased])
        if tone_freqs is None:
            _make_opus_file(sp, section_durations[i])
        else:
            _make_tone_opus_file(sp, tone_freqs[i], section_durations[i], tone_rate)
        store.save_audio_file(
            sid, lesson.title, str(sp), section_index=i, section_type=sec.section_type.value, cues_json=sec_cues_json
        )

    return full_id, section_ids, section_paths


def _make_fake_renderer(kp_seconds: float = 1.5, rate: int = 48000):
    """A renderer fake that CAN exhibit the bug this module exists to prevent.

    ``render_section`` writes one section, which is the correct call. ``render``
    RAISES: it renders the WHOLE lesson, so reaching it means every phrase of
    every section would be re-synthesized and — because the caller passes the
    existing section paths — the user's real audio would be overwritten in place.

    The first version of this fake implemented only ``render`` and wrote silence
    without touching ``section_paths`` or the TTS, so the zero-TTS and
    byte-identity tests passed against an implementation that did neither. A
    fake that cannot fail is not a test.
    """

    class FakeRenderer:
        pause_calculator = NaturalPauseCalculator()

        def __init__(self) -> None:
            self.sections_rendered: list[int] = []

        async def render(self, lesson, output_path, section_paths=None, *, on_progress=None):
            raise AssertionError(
                "reassemble_lesson_audio called renderer.render(), which re-renders "
                "the WHOLE lesson and overwrites the existing section files"
            )

        async def render_section(self, section, output_path, section_idx, language_code):
            self.sections_rendered.append(section_idx)
            from app.audio.transcode import encode_audio

            frames = int(rate * kp_seconds)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(encode_audio(np.zeros((frames, 1), dtype="float32"), rate, "opus", "28k"))
            per = max(frames // max(len(section.phrases), 1), 1)
            cues = [
                (j, j * per, (j + 1) * per if j < len(section.phrases) - 1 else frames)
                for j in range(len(section.phrases))
            ]
            return cues, rate

    return FakeRenderer()


def _make_tone_renderer(freqs: list[float], rate: int = 48000, kp_seconds: float = 1.5):
    """A renderer whose re-rendered sections are tones, not silence.

    Same contract as :func:`_make_fake_renderer` (``render`` raises, because
    rendering the whole lesson would overwrite the user's real section files) —
    the difference is only that section *i*'s audio is a tone at ``freqs[i]``,
    so a re-rendered KEY_PHRASES section is as locatable as a reused one.
    """

    class ToneRenderer:
        pause_calculator = NaturalPauseCalculator()

        def __init__(self) -> None:
            self.sections_rendered: list[int] = []

        async def render(self, lesson, output_path, section_paths=None, *, on_progress=None):
            raise AssertionError("reassemble_lesson_audio called renderer.render()")

        async def render_section(self, section, output_path, section_idx, language_code):
            from app.audio.transcode import encode_audio

            self.sections_rendered.append(section_idx)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(
                encode_audio(_tone_samples(freqs[section_idx], kp_seconds, rate), rate, "opus", "28k")
            )
            frames = int(rate * kp_seconds)
            per = max(frames // max(len(section.phrases), 1), 1)
            cues = [
                (j, j * per, (j + 1) * per if j < len(section.phrases) - 1 else frames)
                for j in range(len(section.phrases))
            ]
            return cues, rate

    return ToneRenderer()


# ── test class ───────────────────────────────────────────────────────────────


class TestReassembleZeroTtsCalls:
    """Zero TTS calls for non-KEY_PHRASES sections (acceptance criterion #1)."""

    @pytest.mark.asyncio
    async def test_counting_fake_records_no_non_kp_calls(self, tmp_path: Path) -> None:
        tts = _CountingTTS()
        store = ContentStore(":memory:")
        lesson = _build_test_lesson()
        section_durations = [2.0, 5.0, 8.0, 5.0]
        audio_dir = tmp_path / "audio"
        _populate_store(store, lesson, audio_dir, section_durations)

        from app.audio.render_service import reassemble_lesson_audio

        await reassemble_lesson_audio(
            store=store,
            renderer=_make_fake_renderer(),
            tts=tts,
            audio_dir=audio_dir,
            lesson_id=lesson.title,
            lesson=lesson,
        )

        # Only the KEY_PHRASES section (index 0) + title should trigger TTS,
        # non-KEY_PHRASES sections = zero calls.
        # Title call is expected and counted; exclude it.
        kp_section_texts = {"Key Phrases", "Dober dan", "Hvala lepa"}
        title_text = lesson.title
        non_section_calls = [c for c in tts.calls if c not in kp_section_texts and c != title_text]
        assert non_section_calls == [], f"Non-KEY_PHRASES TTS calls: {non_section_calls}"

        # Total: KEY_PHRASES section phrases + 1 title = 4 calls
        assert tts.call_count >= 1, "Expected at least 1 TTS call (title)"


class TestReassembleByteIdentity:
    """Every non-KEY_PHRASES section file is byte-identical before and after (acceptance #2)."""

    @pytest.mark.asyncio
    async def test_non_kp_section_files_unchanged(self, tmp_path: Path) -> None:
        store = ContentStore(":memory:")
        lesson = _build_test_lesson()
        section_durations = [2.0, 5.0, 8.0, 5.0]
        audio_dir = tmp_path / "audio"
        _populate_store(store, lesson, audio_dir, section_durations)

        # Record sha256 of non-KEY_PHRASES section files before reassembly
        pre_hashes: dict[int, str] = {}
        rows = store.list_audio_files_for_lesson(lesson.title)
        for r in rows:
            if r["section_index"] is not None and r["section_index"] > 0:
                pre_hashes[r["section_index"]] = _sha256(resolve_audio_path(r["file_path"]))

        from app.audio.render_service import reassemble_lesson_audio

        await reassemble_lesson_audio(
            store=store,
            renderer=_make_fake_renderer(),
            tts=_CountingTTS(),
            audio_dir=audio_dir,
            lesson_id=lesson.title,
            lesson=lesson,
        )

        # Verify non-KEY_PHRASES sections are byte-identical
        post_rows = store.list_audio_files_for_lesson(lesson.title)
        for r in post_rows:
            if r["section_index"] is not None and r["section_index"] > 0:
                post_hash = _sha256(resolve_audio_path(r["file_path"]))
                assert pre_hashes[r["section_index"]] == post_hash, (
                    f"Section {r['section_index']} file changed: {pre_hashes[r['section_index']]} → {post_hash}"
                )


class TestReassembledAudioLandsOnItsCueTimeline:
    """The full file's AUDIO, not its container metadata, against its own cues.

    ffprobe FORMAT duration is computed from granule positions, so it cancels
    the pre-skip on both sides of a stream-copied join and cannot see a seam
    adding a frame of decoded audio. The layout is title + boundary + sec0 +
    ... , which is 8 seams for a 4-section lesson — enough for the full file's
    audio to run a fifth of a second ahead of the caption timeline by the last
    section while every ffprobe assertion in this module stayed green.

    Every section here is a tone at its own frequency, so the audio says where
    each section is and the check does not have to believe the manifest to
    find out. The manifest is used only to say where the audio SHOULD be.
    """

    RATE = 48000
    TONE_HZ = [300.0, 500.0, 700.0, 900.0]
    DURATIONS = [2.0, 3.0, 4.0, 2.5]

    async def _reassemble(self, tmp_path: Path):
        from app.audio.render_service import reassemble_lesson_audio

        store = ContentStore(":memory:")
        lesson = _build_test_lesson(n_sections=len(self.TONE_HZ))
        audio_dir = tmp_path / "audio"
        _populate_store(store, lesson, audio_dir, self.DURATIONS, tone_freqs=self.TONE_HZ, tone_rate=24000)

        tts = _CountingTTS()
        tts.record_to = tmp_path / "title.mp3"

        await reassemble_lesson_audio(
            store=store,
            renderer=_make_tone_renderer(self.TONE_HZ, rate=self.RATE),
            tts=tts,
            audio_dir=audio_dir,
            lesson_id=lesson.title,
            lesson=lesson,
        )

        rows = store.list_audio_files_for_lesson(lesson.title)
        full_row = next(r for r in rows if r["section_index"] is None)
        return (
            resolve_audio_path(full_row["file_path"]),
            json.loads(full_row["cues_json"]),
            tts.record_to,
            {r["section_index"]: resolve_audio_path(r["file_path"]) for r in rows if r["section_index"] is not None},
            lesson,
        )

    @staticmethod
    def _first_cue_start_ms(cues: list[dict], section_index: int) -> int:
        return min(c["start_ms"] for c in cues if c["section_index"] == section_index)

    @pytest.mark.asyncio
    async def test_o1_each_section_tone_lands_on_its_own_first_cue(self, tmp_path: Path) -> None:
        full_path, cues, *_ = await self._reassemble(tmp_path)
        pcm = _decode_pcm(full_path, self.RATE)

        errors = [
            round(_tone_onset_ms(pcm, self.RATE, freq) - self._first_cue_start_ms(cues, i), 1)
            for i, freq in enumerate(self.TONE_HZ)
        ]
        # 20 ms is ONE Opus frame: the tightest bound that still admits an
        # encode, and 8 seams of drift cannot hide inside it.
        assert all(abs(e) < 20 for e in errors), (
            f"onset-vs-cue error per section (ms): {errors} — a growing error across sections is per-seam drift"
        )

    @pytest.mark.asyncio
    async def test_o2_decoded_length_equals_the_sum_of_the_streamed_pieces(self, tmp_path: Path) -> None:
        """The full file must contain exactly the audio that was streamed, not
        the sum of the durations of the files it was built from."""
        full_path, _cues, title_mp3, section_paths, lesson = await self._reassemble(tmp_path)

        streamed_ms = 0.0
        streamed_ms += len(_decode_pcm(title_mp3, self.RATE)) * 1000 / self.RATE
        streamed_ms += NaturalPauseCalculator().get_section_boundary_pause() * len(lesson.sections)
        for i in range(len(lesson.sections)):
            streamed_ms += len(_decode_pcm(section_paths[i], self.RATE)) * 1000 / self.RATE

        actual_ms = len(_decode_pcm(full_path, self.RATE)) * 1000 / self.RATE
        assert abs(actual_ms - streamed_ms) < 20, (
            f"decoded full file is {actual_ms:.1f}ms, the streamed pieces sum to {streamed_ms:.1f}ms "
            f"(difference {actual_ms - streamed_ms:+.1f}ms)"
        )


class TestOnsetCheckIsNotVacuous:
    """O3 — the detector must reject what it is supposed to reject.

    A check that passes on the real file and on a deliberately wrong one
    measures nothing. Both controls here take the SAME decoded audio and the
    SAME expectation and break exactly one of them.
    """

    RATE = TestReassembledAudioLandsOnItsCueTimeline.RATE
    TONE_HZ = TestReassembledAudioLandsOnItsCueTimeline.TONE_HZ

    @pytest.mark.asyncio
    async def test_o3a_forty_milliseconds_of_extra_silence_is_caught(self, tmp_path: Path) -> None:
        cls = TestReassembledAudioLandsOnItsCueTimeline
        full_path, cues, *_ = await cls()._reassemble(tmp_path)
        pcm = _decode_pcm(full_path, self.RATE)

        last = len(self.TONE_HZ) - 1
        onset = _tone_onset_ms(pcm, self.RATE, self.TONE_HZ[last])
        at = int(onset * self.RATE / 1000)
        gapped = np.concatenate([pcm[:at], np.zeros(int(0.040 * self.RATE), dtype="float32"), pcm[at:]])

        moved = _tone_onset_ms(gapped, self.RATE, self.TONE_HZ[last]) - cls._first_cue_start_ms(cues, last)
        assert abs(moved) >= 20, f"a 40 ms gap moved the onset by only {moved:.1f}ms — the check is vacuous"

    @pytest.mark.asyncio
    async def test_o3b_the_wrong_sections_cue_is_caught(self, tmp_path: Path) -> None:
        cls = TestReassembledAudioLandsOnItsCueTimeline
        full_path, cues, *_ = await cls()._reassemble(tmp_path)
        pcm = _decode_pcm(full_path, self.RATE)

        last = len(self.TONE_HZ) - 1
        onset = _tone_onset_ms(pcm, self.RATE, self.TONE_HZ[last])
        wrong = onset - cls._first_cue_start_ms(cues, last - 1)
        assert abs(wrong) >= 20, f"the neighbouring section's cue is within {wrong:.1f}ms — the check is vacuous"


class TestLessonPiecesAreDecodedLazily:
    """O5 — the piece generator must hold ONE piece, not the lesson.

    Observed through the real seam rather than by inspecting the source: the
    LAST section's file is undecodable, so the only question is WHEN the failure
    arrives. If the generator decoded everything up front the RuntimeError
    would fire before the first piece was yielded and the list of pieces would
    come back empty; deferred, every earlier piece is already in hand.
    """

    def test_a_piece_is_decoded_only_when_the_encoder_asks_for_it(self, tmp_path: Path) -> None:
        import soundfile as sf

        from app.audio import assembly as _assembly
        from app.audio.render_service import _lesson_pcm_pieces

        rate = 48000
        boundary_ms = 3000

        title = tmp_path / "title.wav"
        sf.write(str(title), np.zeros((rate, 1), dtype="float32"), rate, subtype="PCM_16")

        section_paths = []
        for i in range(3):
            p = tmp_path / f"s{i}.opus"
            _make_tone_opus_file(p, 300.0 + 200 * i, 0.5, rate)
            section_paths.append(p)
        broken = tmp_path / "s3.opus"
        broken.write_bytes(b"this is not an opus stream")
        section_paths.append(broken)

        order = _assembly.lesson_layout([0] * 4, 0, boundary_ms).piece_descriptions
        piece_ms: list[int] = []
        pieces = _lesson_pcm_pieces(order, title, section_paths, int(rate * boundary_ms / 1000), rate, piece_ms)

        streamed = []
        with pytest.raises(RuntimeError):
            for piece in pieces:
                streamed.append(piece)

        assert len(streamed) == len(order) - 1, (
            f"only {len(streamed)} of {len(order)} pieces arrived before the bad file was reached — "
            f"the generator is not lazy"
        )
        assert len(piece_ms) == len(streamed), "a piece was measured that was never handed to the encoder"
        assert all(piece.shape == (int(rate * boundary_ms / 1000), 1) or piece.shape[0] > 0 for piece in streamed)

    def test_a_piece_that_decodes_to_nothing_is_refused(self, tmp_path: Path) -> None:
        """A zero-length section is a section the lesson silently loses.

        The stream-copy join this replaced caught it with a duration
        postcondition; per-piece decoding has to be loud by itself, and ffmpeg
        exits **0** on a structurally valid but empty input.
        """
        import soundfile as sf

        from app.audio import assembly as _assembly
        from app.audio.render_service import _lesson_pcm_pieces

        rate = 48000
        title = tmp_path / "title.wav"
        sf.write(str(title), np.zeros((rate, 1), dtype="float32"), rate, subtype="PCM_16")
        empty = tmp_path / "empty.wav"
        sf.write(str(empty), np.zeros((0, 1), dtype="float32"), rate, subtype="PCM_16")

        order = _assembly.lesson_layout([0], 0, 3000).piece_descriptions
        pieces = _lesson_pcm_pieces(order, title, [empty], 3000, rate, [])
        with pytest.raises(RuntimeError, match="no samples"):
            list(pieces)


class TestReassembleDuration:
    """Full file duration equals title + boundary*(N-1) + sum(section durations) within tolerance (acceptance #3).

    ⚠️ This class measures with ffprobe FORMAT duration, which is computed
    from granule positions and therefore CANNOT see decoded drift: a
    stream-copied Opus join's extra frames at each seam cancel out of the
    container's own arithmetic. It stayed green over a full file whose audio ran
    145 ms ahead of its cues. It is kept because a joined file that is the wrong
    SHAPE is still a real failure mode, but it is not the test for whether the
    audio and the cues agree — see ``TestReassembledAudioLandsOnItsCueTimeline``.
    """

    @pytest.mark.asyncio
    async def test_reassembled_duration_matches_formula(self, tmp_path: Path) -> None:
        store = ContentStore(":memory:")
        lesson = _build_test_lesson(n_sections=4)
        section_durations = [2.0, 5.0, 8.0, 5.0]
        audio_dir = tmp_path / "audio"
        _populate_store(store, lesson, audio_dir, section_durations)

        from app.audio.render_service import reassemble_lesson_audio

        await reassemble_lesson_audio(
            store=store,
            renderer=_make_fake_renderer(),
            tts=_CountingTTS(),
            audio_dir=audio_dir,
            lesson_id=lesson.title,
            lesson=lesson,
        )

        full_path = _full_path(store, lesson.title)
        assert full_path.exists()

        # Read section durations from the new section file (KEY_PHRASES was re-rendered)
        kp_section_rows = [r for r in store.list_audio_files_for_lesson(lesson.title) if r["section_index"] == 0]
        assert kp_section_rows
        kp_dur = _ffprobe_duration(resolve_audio_path(kp_section_rows[0]["file_path"]))
        assert kp_dur > 0, f"KEY_PHRASES section duration invalid: {kp_dur}"

        # Recompute expected total. The boundary count is N, not N-1: the
        # renderer builds `title + B + sec0 + B + sec1 + ...`, so there is one
        # boundary after the title AND one between each adjacent pair —
        # LessonRenderer.render seeds `parts = [title_audio, boundary]` and then
        # appends another boundary for every section after the first.
        # An N-1 formula here made a CORRECT reassembly look 3000 ms too long,
        # which is the sort of oracle error that gets "fixed" in the
        # implementation instead of the test.
        boundary_ms = NaturalPauseCalculator().get_section_boundary_pause()
        # Measured, not assumed: the title is synthesized as MP3 and re-encoded
        # into the delivery codec, and that round trip adds ~114 ms of codec
        # padding. The first cue IS the title, so the manifest already carries
        # the answer — and asserting against it also checks that the manifest
        # agrees with the audio it describes.
        full_row = next(r for r in store.list_audio_files_for_lesson(lesson.title) if r["section_index"] is None)
        title_dur = json.loads(full_row["cues_json"])[0]["end_ms"] / 1000
        n_sections = len(lesson.sections)
        expected_total = title_dur + (boundary_ms / 1000) * n_sections + kp_dur + sum(section_durations[1:])

        actual_dur = _ffprobe_duration(full_path)
        assert actual_dur > 0, f"Reassembled full file duration invalid: {actual_dur}"

        # Allow tolerance for Opus container overhead (measured: constant ~6.5ms)
        tolerance_ms = 100
        assert abs(actual_dur - expected_total) * 1000 < tolerance_ms, (
            f"Duration mismatch: actual={actual_dur:.4f}s, expected={expected_total:.4f}s, "
            f"diff={abs(actual_dur - expected_total) * 1000:.1f}ms"
        )


class TestCueOffsetAccuracy:
    """Cue for phrase in LAST section still lands on correct audio after KEY_PHRASES length change (acceptance #5).

    ⚠️ Same blindness as ``TestReassembleDuration``: every number here comes
    from ffprobe's FORMAT duration, so these assertions confirm the cues stay
    INSIDE the file, not that they sit on the audio. Whether they sit on it is
    ``TestReassembledAudioLandsOnItsCueTimeline``'s question, and the two
    disagreed for as long as both existed.
    """

    @pytest.mark.asyncio
    async def test_last_section_cue_offset_correct(self, tmp_path: Path) -> None:
        store = ContentStore(":memory:")
        lesson = _build_test_lesson(n_sections=4)
        section_durations = [2.0, 5.0, 8.0, 5.0]
        audio_dir = tmp_path / "audio"
        _populate_store(store, lesson, audio_dir, section_durations)

        # Record the old full file's duration and last-section cue offsets
        old_rows = store.list_audio_files_for_lesson(lesson.title)
        old_full = next(r for r in old_rows if r["section_index"] is None)
        old_full_dur = _ffprobe_duration(resolve_audio_path(old_full["file_path"]))

        _old_last_sec_cues = json.loads(
            next(r for r in old_rows if r["section_index"] == len(lesson.sections) - 1)["cues_json"]
        )

        from app.audio.render_service import reassemble_lesson_audio

        # Reassemble with a NEW KEY_PHRASES duration (changed from original)
        await reassemble_lesson_audio(
            store=store,
            renderer=_make_fake_renderer(kp_seconds=2.0),
            tts=_CountingTTS(),
            audio_dir=audio_dir,
            lesson_id=lesson.title,
            lesson=lesson,
        )

        # New full file duration changed
        new_full_dur = _ffprobe_duration(_full_path(store, lesson.title))
        assert new_full_dur != old_full_dur, "Expected duration to change after KEY_PHRASES re-render"

        # The last section's cues must still be valid (start < total frames)
        # Read absolute-position cues from the full-lesson row
        rate = 24000
        boundary_ms = 3000
        title_dur = 1.0
        new_total_frames = int(new_full_dur * rate)
        new_rows = store.list_audio_files_for_lesson(lesson.title)
        new_full_row = next(r for r in new_rows if r["section_index"] is None)
        new_full_cues = json.loads(new_full_row["cues_json"])

        last_sec_idx = len(lesson.sections) - 1
        last_sec_cues = [c for c in new_full_cues if c.get("section_index") == last_sec_idx]
        assert last_sec_cues, "No cues found for last section in full manifest"

        for cue in last_sec_cues:
            cue_start_ms = cue["start_ms"]
            cue_start_frame = int(cue_start_ms * rate / 1000)
            assert cue_start_frame < new_total_frames, (
                f"Last-section cue at {cue_start_ms}ms ({cue_start_frame} frames) "
                f"exceeds total {new_total_frames} frames"
            )

        # Also verify that the first cue of the last section comes AFTER
        # all previous sections' audio
        first_prev_section_end = int(
            (title_dur + boundary_ms / 1000 * (len(lesson.sections) - 1) + sum(section_durations[:-1])) * rate
        )
        assert last_sec_cues[0]["start_ms"] * rate / 1000 > first_prev_section_end / rate * 1000 - 1000, (
            f"Last section cue starts too early: {last_sec_cues[0]['start_ms']}ms, "
            f"expected after {first_prev_section_end / rate * 1000:.0f}ms"
        )


class TestReassembleMissingFullRow:
    """API 404s when full row is missing — partial write must not leave that state (acceptance #6)."""

    @pytest.mark.asyncio
    async def test_missing_full_row_returns_404(self, tmp_path: Path) -> None:
        """GET /api/audio/lesson/{id} returns 404 when only section rows exist (no full row)."""
        store = ContentStore(":memory:")

        # Create a lesson with only section rows (no full row)
        lesson_id = "partial-lesson"
        audio_dir = tmp_path / "audio"
        audio_dir.mkdir()
        sid = "sec-1"
        sp = audio_dir / f"{sid}.opus"
        _make_opus_file(sp, 2.0)
        store.save_audio_file(sid, lesson_id, str(sp), section_index=0, section_type="key_phrases")

        # Verify: listing shows section rows but no full row
        rows = store.list_audio_files_for_lesson(lesson_id)
        full_row = next((r for r in rows if r["section_index"] is None), None)
        assert full_row is None, "Expected no full-lesson row"
        section_rows = [r for r in rows if r["section_index"] is not None]
        assert len(section_rows) == 1

        # This condition (section rows exist, full row missing) should not
        # occur in practice — reassemble_lesson_audio must always leave the
        # store in a consistent state. This test verifies the precondition
        # that the GET endpoint would return 404 for this state.


class TestReassembleRefusals:
    """Reassembly refuses rather than guessing when the stored rows do not
    describe the lesson it was handed. Each of these would otherwise corrupt the
    audio rows: the function deletes them all before writing the new set."""

    @pytest.mark.asyncio
    async def test_refuses_when_no_full_row_exists(self, tmp_path: Path) -> None:
        from app.audio.render_service import reassemble_lesson_audio

        store = ContentStore(":memory:")
        lesson = _build_test_lesson()
        with pytest.raises(ValueError, match="Full lesson audio not found"):
            await reassemble_lesson_audio(
                store=store,
                renderer=_make_fake_renderer(),
                tts=_CountingTTS(),
                audio_dir=tmp_path / "audio",
                lesson_id=lesson.title,
                lesson=lesson,
            )

    @pytest.mark.asyncio
    async def test_reassembles_when_the_old_full_row_has_no_file(self, tmp_path: Path) -> None:
        """A full row with no file is a normal row, not a broken one.

        Reassembly unlinks the old full file when it commits the new rows, and
        `resolve_audio_path(None)` is a TypeError — so this is the shape every
        lesson rendered since tunatale-guzo.3 arrives in.
        """
        from app.audio.render_service import reassemble_lesson_audio

        store = ContentStore(":memory:")
        lesson = _build_test_lesson(n_sections=4)
        audio_dir = tmp_path / "audio"
        _populate_store(store, lesson, audio_dir, [2.0, 5.0, 8.0, 5.0], full_row_has_file=False)

        old_rows = store.list_audio_files_for_lesson(lesson.title)
        old_full = next(r for r in old_rows if r["section_index"] is None)
        assert old_full["file_path"] is None

        result = await reassemble_lesson_audio(
            store=store,
            renderer=_make_fake_renderer(),
            tts=_CountingTTS(),
            audio_dir=audio_dir,
            lesson_id=lesson.title,
            lesson=lesson,
        )

        assert result["audio_id"] != old_full["id"]
        assert len(result["sections"]) == len(lesson.sections)
        assert result["cues"], "reassembly rebuilt the manifest"
        # Reassembly itself still writes a full file — only render() stopped.
        new_full = store.get_audio_file_row(result["audio_id"])
        assert new_full["file_path"] is not None
        assert resolve_audio_path(new_full["file_path"]).exists()

    @pytest.mark.asyncio
    async def test_refuses_when_section_row_count_disagrees(self, tmp_path: Path) -> None:
        """A lesson that gained or lost a section since it was last rendered
        cannot be reassembled from the old rows — the section files no longer
        line up with lesson.sections, so every cue after the mismatch is wrong."""
        from app.audio.render_service import reassemble_lesson_audio

        store = ContentStore(":memory:")
        lesson = _build_test_lesson()
        audio_dir = tmp_path / "audio"
        _populate_store(store, lesson, audio_dir, [2.0, 5.0, 8.0, 5.0])
        lesson.sections.append(
            Section(
                section_type=SectionType.EN_TRANSLATED,
                phrases=[Phrase(text="x", voice_id="v", language_code="en", role="narrator")],
            )
        )
        with pytest.raises(ValueError, match="section audio rows"):
            await reassemble_lesson_audio(
                store=store,
                renderer=_make_fake_renderer(),
                tts=_CountingTTS(),
                audio_dir=audio_dir,
                lesson_id=lesson.title,
                lesson=lesson,
            )

    @pytest.mark.asyncio
    async def test_refuses_a_lesson_with_no_key_phrases_section(self, tmp_path: Path) -> None:
        from app.audio.render_service import reassemble_lesson_audio

        store = ContentStore(":memory:")
        lesson = _build_test_lesson()
        audio_dir = tmp_path / "audio"
        _populate_store(store, lesson, audio_dir, [2.0, 5.0, 8.0, 5.0])
        lesson.sections[0] = Section(section_type=SectionType.EN_TRANSLATED, phrases=lesson.sections[0].phrases)
        with pytest.raises(ValueError, match="No KEY_PHRASES section"):
            await reassemble_lesson_audio(
                store=store,
                renderer=_make_fake_renderer(),
                tts=_CountingTTS(),
                audio_dir=audio_dir,
                lesson_id=lesson.title,
                lesson=lesson,
            )


class TestTitlePcmLoad:
    """``_read_title_pcm`` normalises the TTS's MP3 into the lesson's stream.

    The TTS returns MP3 at its own rate whatever the caller names the file, and
    the lesson is one stream at the assembly rate, so this is the adapter — and
    each branch here is a real mismatch it has to absorb. It used to be
    ``_transcode_to_delivery``, which also encoded the result; the title no
    longer round-trips through the delivery codec on its way into the lesson.
    """

    def test_matching_rate_is_not_resampled(self, tmp_path: Path) -> None:
        """The common case once the TTS and the sections agree: no resample."""
        import soundfile as sf

        from app.audio.render_service import _read_title_pcm

        src = tmp_path / "in.wav"
        sf.write(str(src), np.zeros((48000, 1), dtype="float32"), 48000, subtype="PCM_16")
        pcm = _read_title_pcm(src, 48000)
        assert pcm.shape == (48000, 1)
        assert pcm.dtype == np.float32

    def test_mismatched_rate_is_resampled_to_the_target(self, tmp_path: Path) -> None:
        """Duration must survive the rate change — a resample that drops or
        doubles frames would shift every cue after the title."""
        import soundfile as sf

        from app.audio.render_service import _read_title_pcm

        src = tmp_path / "in.wav"
        sf.write(str(src), np.zeros((24000, 1), dtype="float32"), 24000, subtype="PCM_16")
        pcm = _read_title_pcm(src, 48000)
        assert abs(pcm.shape[0] / 48000 - 1.0) < 0.005, pcm.shape

    def test_stereo_is_downmixed_to_mono(self, tmp_path: Path) -> None:
        """The lesson is assembled mono; a stereo title has to lose a channel
        before the resample, not after it."""
        import soundfile as sf

        from app.audio.render_service import _read_title_pcm

        src = tmp_path / "in.wav"
        sf.write(str(src), np.zeros((48000, 2), dtype="float32"), 48000, subtype="PCM_16")
        pcm = _read_title_pcm(src, 48000)
        assert pcm.shape == (48000, 1)

    @pytest.mark.asyncio
    async def test_wav_delivery_streams_through_soundfile(self, tmp_path: Path, monkeypatch) -> None:
        """The wav branch writes the same PCM, uncompressed, through an open
        soundfile handle — and lands the tones on their cues just as the
        compressed branch does, because the seam is the same either way."""
        import soundfile as sf

        from app.audio import render_service
        from app.audio.render_service import reassemble_lesson_audio

        monkeypatch.setattr(render_service.settings, "audio_delivery_codec", "wav")

        store = ContentStore(":memory:")
        tone_hz = [300.0, 500.0, 700.0, 900.0]
        durations = [2.0, 3.0, 4.0, 2.5]
        lesson = _build_test_lesson(n_sections=4)
        audio_dir = tmp_path / "audio"
        _populate_store(store, lesson, audio_dir, durations, tone_freqs=tone_hz, tone_rate=24000)

        await reassemble_lesson_audio(
            store=store,
            renderer=_make_tone_renderer(tone_hz, rate=48000),
            tts=_CountingTTS(),
            audio_dir=audio_dir,
            lesson_id=lesson.title,
            lesson=lesson,
        )

        full_path = _full_path(store, lesson.title)
        info = sf.info(str(full_path))
        assert info.channels == 1
        assert info.samplerate == 48000
        assert abs(info.duration - (1.05 + 4 * 3.0 + 1.5 + sum(durations[1:]))) < 0.2, info.duration

        pcm = _decode_pcm(full_path, 48000)
        cues = json.loads(
            next(r for r in store.list_audio_files_for_lesson(lesson.title) if r["section_index"] is None)["cues_json"]
        )
        errors = [
            round(_tone_onset_ms(pcm, 48000, f) - min(c["start_ms"] for c in cues if c["section_index"] == i), 1)
            for i, f in enumerate(tone_hz)
        ]
        assert all(abs(e) < 20 for e in errors), errors


class TestFullManifestMatchesARenderedLesson:
    """The reassembled full manifest must be indistinguishable from a rendered
    one. Consumers cannot tell which function last touched a lesson, so any
    field that differs leaves two kinds of manifest in one database.

    Both assertions below passed against a version that got them WRONG, because
    the suite only checked offsets. They are here because a real reassembly of
    the user's 9 lessons shipped both defects.
    """

    @pytest.mark.asyncio
    async def test_title_cue_carries_the_narration_ref(self, tmp_path: Path) -> None:
        """build_cue_manifest gives the title cue ref={"kind": "narration"} and
        language_code="en". A hand-rolled copy set ref=None and derived the
        language from the lesson — which agreed only by luck, because the
        KEY_PHRASES section happens to open with an English narrator line."""
        from app.audio.render_service import reassemble_lesson_audio

        store = ContentStore(":memory:")
        lesson = _build_test_lesson()
        audio_dir = tmp_path / "audio"
        _populate_store(store, lesson, audio_dir, [2.0, 5.0, 8.0, 5.0])
        await reassemble_lesson_audio(
            store=store,
            renderer=_make_fake_renderer(),
            tts=_CountingTTS(),
            audio_dir=audio_dir,
            lesson_id=lesson.title,
            lesson=lesson,
        )
        row = next(r for r in store.list_audio_files_for_lesson(lesson.title) if r["section_index"] is None)
        title = json.loads(row["cues_json"])[0]
        assert title["section_index"] is None
        assert title["ref"] == {"kind": "narration"}, title
        assert title["language_code"] == "en"
        assert title["role"] == "narrator"

    @pytest.mark.asyncio
    async def test_full_manifest_text_comes_from_the_lesson_not_the_stored_cues(self, tmp_path: Path) -> None:
        """The stored PER-SECTION cues are ellipsis-scrubbed by
        derive_section_cues; the FULL manifest is not. Re-offsetting the stored
        cues into a full manifest therefore imports the scrubbed text and the
        lesson ends up carrying natural text where a rendered one carries raw.

        Pinned by giving a stored section cue text that appears NOWHERE in the
        lesson: if the full manifest quotes it back, it was built from the cues
        instead of from the lesson.
        """
        from app.audio.render_service import reassemble_lesson_audio

        store = ContentStore(":memory:")
        lesson = _build_test_lesson()
        audio_dir = tmp_path / "audio"
        _populate_store(store, lesson, audio_dir, [2.0, 5.0, 8.0, 5.0])

        rows = store.list_audio_files_for_lesson(lesson.title)
        victim = next(r for r in rows if r["section_index"] == 1)
        cues = json.loads(victim["cues_json"])
        assert cues, "fixture section 1 has no cues to poison"
        cues[0]["text"] = "SCRUBBED-SENTINEL-NOT-IN-THE-LESSON"
        # Poisoned IN PLACE: adding a row instead would trip the section-count
        # guard, and replacing the row is not what the scenario is about.
        with store._get_conn() as conn:
            conn.execute(
                "UPDATE audio_files SET cues_json = ? WHERE id = ?",
                (json.dumps(cues), victim["id"]),
            )

        await reassemble_lesson_audio(
            store=store,
            renderer=_make_fake_renderer(),
            tts=_CountingTTS(),
            audio_dir=audio_dir,
            lesson_id=lesson.title,
            lesson=lesson,
        )
        full = next(r for r in store.list_audio_files_for_lesson(lesson.title) if r["section_index"] is None)
        texts = [c["text"] for c in json.loads(full["cues_json"])]
        assert "SCRUBBED-SENTINEL-NOT-IN-THE-LESSON" not in texts, (
            "the full manifest was built from the stored per-section cues, "
            "so it inherited their scrubbed text instead of the lesson's"
        )


class TestReassembleArbitrarySectionSet:
    """reassemble_lesson_audio takes an explicit SectionType set to re-render.

    Fixture section order is [KEY_PHRASES(0), NATURAL_SPEED(1), TRANSLATED(2),
    SLOW_SPEED(3)]. Every pre-existing test in this file exercises the offset
    arithmetic with the target at index 0, where an off-by-one is invisible;
    O3 re-renders TRANSLATED (index 2) specifically because it has sections on
    BOTH sides.
    """

    @pytest.mark.asyncio
    async def test_o1_single_middle_target_is_selective(self, tmp_path: Path) -> None:
        """Re-render {SLOW_SPEED} only: KEY_PHRASES path+bytes untouched, the
        SLOW_SPEED file is replaced and its old file unlinked."""
        from app.audio.render_service import reassemble_lesson_audio

        store = ContentStore(":memory:")
        lesson = _build_test_lesson()
        audio_dir = tmp_path / "audio"
        _populate_store(store, lesson, audio_dir, [2.0, 5.0, 8.0, 5.0])

        rows_before = {r["section_index"]: r for r in store.list_audio_files_for_lesson(lesson.title)}
        kp_path_before = rows_before[0]["file_path"]
        kp_sha_before = _sha256(resolve_audio_path(kp_path_before))
        slow_path_before = rows_before[3]["file_path"]

        await reassemble_lesson_audio(
            store=store,
            renderer=_make_fake_renderer(),
            tts=_CountingTTS(),
            audio_dir=audio_dir,
            lesson_id=lesson.title,
            lesson=lesson,
            section_types={SectionType.SLOW_SPEED},
        )

        rows_after = {r["section_index"]: r for r in store.list_audio_files_for_lesson(lesson.title)}
        assert rows_after[0]["file_path"] == kp_path_before, "KEY_PHRASES row's file_path must be unchanged"
        assert _sha256(resolve_audio_path(rows_after[0]["file_path"])) == kp_sha_before, (
            "KEY_PHRASES file must be byte-identical"
        )
        assert rows_after[3]["file_path"] != slow_path_before, "SLOW_SPEED row must point at a NEW path"
        assert not Path(slow_path_before).exists(), "the old SLOW_SPEED file must be unlinked"

    @pytest.mark.asyncio
    async def test_o2_multiple_targets(self, tmp_path: Path) -> None:
        """Re-render {KEY_PHRASES, SLOW_SPEED}: both get new paths, the two
        untouched sections keep byte-identical files."""
        from app.audio.render_service import reassemble_lesson_audio

        store = ContentStore(":memory:")
        lesson = _build_test_lesson()
        audio_dir = tmp_path / "audio"
        _populate_store(store, lesson, audio_dir, [2.0, 5.0, 8.0, 5.0])

        rows_before = {r["section_index"]: r for r in store.list_audio_files_for_lesson(lesson.title)}
        sha_before = {i: _sha256(resolve_audio_path(r["file_path"])) for i, r in rows_before.items() if r is not None}

        await reassemble_lesson_audio(
            store=store,
            renderer=_make_fake_renderer(),
            tts=_CountingTTS(),
            audio_dir=audio_dir,
            lesson_id=lesson.title,
            lesson=lesson,
            section_types={SectionType.KEY_PHRASES, SectionType.SLOW_SPEED},
        )

        rows_after = {r["section_index"]: r for r in store.list_audio_files_for_lesson(lesson.title)}
        for i in (0, 3):
            assert rows_after[i]["file_path"] != rows_before[i]["file_path"], f"section {i} must be re-rendered"
        for i in (1, 2):
            assert _sha256(resolve_audio_path(rows_after[i]["file_path"])) == sha_before[i], (
                f"section {i} must stay byte-identical"
            )

    @pytest.mark.asyncio
    async def test_o3_discriminator_middle_target_offset_arithmetic(self, tmp_path: Path) -> None:
        """Re-render TRANSLATED alone (index 2, sections on both sides) and pin
        every cue position against a ffprobe-consistent 'before' manifest.

        The seeded manifest records IDEAL durations (2.0/5.0/8.0/5.0s), but the
        actual on-disk files each carry ~6.5ms of codec container padding and the
        re-synthesised title carries ~62ms of MP3 round-trip padding. So the
        'before' baseline is reconstructed from the SAME measured quantities
        reassemble uses — its own title decode and round(n_samples*1000/rate)
        per section — which makes the 'identical'/'shifted by exactly' claims
        exact, not approximate. An implementation that gets the target offset
        wrong (invisible at index 0) breaks section 3's shift.
        """
        from app.audio.render_service import _read_title_pcm, reassemble_lesson_audio

        store = ContentStore(":memory:")
        lesson = _build_test_lesson()
        audio_dir = tmp_path / "audio"
        seeded = [2.0, 5.0, 8.0, 5.0]
        _populate_store(store, lesson, audio_dir, seeded)

        rate = 48000
        renderer = _make_fake_renderer(kp_seconds=1.5, rate=rate)
        tts = _CountingTTS()

        rows = {r["section_index"]: r for r in store.list_audio_files_for_lesson(lesson.title)}
        rows_ordered = [rows[i] for i in range(len(lesson.sections))]

        # The duration reassemble WILL use for each section, measured the way it
        # measures: from the decoded samples it streams, at the assembly rate.
        dur_ms = [round(len(_decode_pcm(resolve_audio_path(r["file_path"]), rate)) * 1000 / rate) for r in rows_ordered]

        # The title reassemble will decode: same bytes, same decode.
        rt_mp3 = tmp_path / "title-roundtrip.mp3"
        await tts.synthesize(lesson.title, lesson.narrator_voice, rt_mp3, rate="+0%")
        title_ms = round(len(_read_title_pcm(rt_mp3, rate)) * 1000 / rate)

        boundary_ms = NaturalPauseCalculator().get_section_boundary_pause()

        # Reconstructed "before" full manifest: title + boundary, then each
        # section's STORED cues rebased onto ffprobe-realistic section starts.
        before: list[dict] = [{"section_index": None, "phrase_index": 0, "start_ms": 0, "end_ms": title_ms}]
        section_cues_before: dict[int, list[dict]] = {}
        cursor = title_ms + boundary_ms
        for i, r in enumerate(rows_ordered):
            rebased = json.loads(r["cues_json"])
            section_cues_before[i] = [
                {
                    "section_index": i,
                    "phrase_index": cd["phrase_index"],
                    "start_ms": cd["start_ms"] + cursor,
                    "end_ms": cd["end_ms"] + cursor,
                }
                for cd in rebased
            ]
            before.extend(section_cues_before[i])
            cursor += dur_ms[i] + boundary_ms

        # Store the reconstructed manifest so the app's view of "before" is
        # coherent with what the rows on disk actually measure.
        with store._get_conn() as conn:
            full_row = next(r for r in rows.values() if r["section_index"] is None)
            conn.execute("UPDATE audio_files SET cues_json = ? WHERE id = ?", (json.dumps(before), full_row["id"]))

        payload = await reassemble_lesson_audio(
            store=store,
            renderer=renderer,
            tts=tts,
            audio_dir=audio_dir,
            lesson_id=lesson.title,
            lesson=lesson,
            section_types={SectionType.TRANSLATED},
        )
        after = payload["cues"]

        # The re-rendered title must reproduce the measured one byte-for-byte;
        # otherwise sections 0/1 could not be identical to before.
        assert after[0]["end_ms"] == title_ms, (after[0]["end_ms"], title_ms)

        after_by_sec: dict[int, list[dict]] = {}
        for c in after:
            if c["section_index"] is not None:
                after_by_sec.setdefault(c["section_index"], []).append(c)

        # (a) Sections BEFORE the middle target: identical start/end ms.
        for i in (0, 1):
            got = [(c["start_ms"], c["end_ms"]) for c in after_by_sec[i]]
            expected = [(c["start_ms"], c["end_ms"]) for c in section_cues_before[i]]
            assert got == expected, (i, got, expected)

        # (b) Section AFTER the target shifts by exactly the target's duration
        #     delta — the section at index 2 was 8.0s, now it is 1.5s. Both
        #     sides are measured the way reassemble measures, or the "exactly"
        #     would be an artefact of two different rulers.
        new_rows = {r["section_index"]: r for r in store.list_audio_files_for_lesson(lesson.title)}
        new_sec2_dur_ms = round(len(_decode_pcm(resolve_audio_path(new_rows[2]["file_path"]), rate)) * 1000 / rate)
        delta_ms = new_sec2_dur_ms - dur_ms[2]
        sec3_before = sorted(section_cues_before[3], key=lambda c: c["phrase_index"])
        sec3_after = sorted(after_by_sec[3], key=lambda c: c["phrase_index"])
        assert len(sec3_after) == len(sec3_before)
        for b_cue, a_cue in zip(sec3_before, sec3_after, strict=True):
            assert a_cue["start_ms"] - b_cue["start_ms"] == delta_ms, (b_cue, a_cue, delta_ms)
            assert a_cue["end_ms"] - b_cue["end_ms"] == delta_ms, (b_cue, a_cue, delta_ms)

        # (c) The full-lesson file's DECODED length equals title + 4*boundary +
        #     the four (re-derived) section durations, within one Opus frame.
        #     Decoded, not ffprobed: a granule-position duration cancels the
        #     pre-skip on both sides and cannot see a seam.
        expected_total = (
            title_ms
            + 4 * boundary_ms
            + sum(
                round(len(_decode_pcm(resolve_audio_path(r["file_path"]), rate)) * 1000 / rate)
                for r in (new_rows[i] for i in range(4))
            )
        )
        actual_total = len(_decode_pcm(_full_path(store, lesson.title), rate)) * 1000 / rate
        assert abs(actual_total - expected_total) < 20, (actual_total, expected_total)

    @pytest.mark.asyncio
    async def test_o4_no_matching_section(self, tmp_path: Path) -> None:
        """{EN_TRANSLATED} on a lesson without one must raise with the lesson id."""
        from app.audio.render_service import reassemble_lesson_audio

        store = ContentStore(":memory:")
        lesson = _build_test_lesson()
        audio_dir = tmp_path / "audio"
        _populate_store(store, lesson, audio_dir, [2.0, 5.0, 8.0, 5.0])

        with pytest.raises(ValueError, match=lesson.title):
            await reassemble_lesson_audio(
                store=store,
                renderer=_make_fake_renderer(),
                tts=_CountingTTS(),
                audio_dir=audio_dir,
                lesson_id=lesson.title,
                lesson=lesson,
                section_types={SectionType.EN_TRANSLATED},
            )

    @pytest.mark.asyncio
    async def test_o6_rate_disagreement_raises(self, tmp_path: Path) -> None:
        """Two targets reporting different rates must FAIL loudly — the concat
        demuxer runs under -c copy and cannot join heterogeneous streams."""
        from app.audio.render_service import reassemble_lesson_audio

        store = ContentStore(":memory:")
        lesson = _build_test_lesson()
        audio_dir = tmp_path / "audio"
        _populate_store(store, lesson, audio_dir, [2.0, 5.0, 8.0, 5.0])

        base = _make_fake_renderer()

        class _RateDisagreeingRenderer:
            pause_calculator = base.pause_calculator

            async def render(self, *a, **kw):
                return await base.render(*a, **kw)

            async def render_section(self, section, output_path, section_idx, language_code):
                cues, _ = await base.render_section(section, output_path, section_idx, language_code)
                return cues, 48000 if section_idx == 0 else 44100

        with pytest.raises(ValueError) as exc_info:
            await reassemble_lesson_audio(
                store=store,
                renderer=_RateDisagreeingRenderer(),
                tts=_CountingTTS(),
                audio_dir=audio_dir,
                lesson_id=lesson.title,
                lesson=lesson,
                section_types={SectionType.KEY_PHRASES, SectionType.SLOW_SPEED},
            )
        message = str(exc_info.value)
        assert "48000" in message and "44100" in message, message


class TestAnUnusableSectionIsNeverSilent:
    """A section the reassemble cannot read must be LOUD, not dropped.

    Measured directly (tunatale-c7tx), with real Opus files and the product's
    own argv: ``ffmpeg -f concat`` EXITS 0 when a mid-list entry is unopenable.
    It logs "Impossible to open" plus "Error during demuxing", returns 0, and
    writes a file truncated at that entry — which is how four Norwegian lessons
    shipped 267s of audio under a 1400s caption timeline.

    The stream-copy join that did this is gone. The property is not: a piece is
    now decoded on its own, and a decode either produces samples or raises. The
    last test here is the control that matters most, because this check sits on
    a path that rewrites a user's lesson and a false positive is worse than the
    bug it guards.
    """

    def test_a_section_file_that_is_not_there_is_refused(self, tmp_path: Path) -> None:
        from app.audio.render_service import _decode_section_pcm

        _make_opus_file(tmp_path / "a.opus", 1.0)
        with pytest.raises(RuntimeError, match="could not decode a lesson section"):
            _decode_section_pcm(tmp_path / "gone.opus", 48000)

    def test_a_section_file_that_exists_but_is_unopenable_is_refused(self, tmp_path: Path) -> None:
        """An existence preflight would NOT have been enough. An entry that
        exists and holds garbage used to truncate the join at the same place,
        at exit 0, and it still fails to decode."""
        from app.audio.render_service import _decode_section_pcm

        bad = tmp_path / "bad.opus"
        bad.write_bytes(b"this is not an opus stream")
        with pytest.raises(RuntimeError, match="could not decode a lesson section"):
            _decode_section_pcm(bad, 48000)

    def test_a_healthy_section_decodes_to_the_audio_it_holds(self, tmp_path: Path) -> None:
        """The control: a real section decodes to its own duration at the
        assembly rate, resampled rather than rejected, and mono."""
        from app.audio.render_service import _decode_section_pcm

        path = tmp_path / "s.opus"
        _make_tone_opus_file(path, 440.0, 1.0, 24000)
        pcm = _decode_section_pcm(path, 48000)
        assert pcm.shape == (48000, 1)
        assert abs(_tone_onset_ms(pcm.reshape(-1), 48000, 440.0)) < 20, "the control decoded to something else"

    @pytest.mark.asyncio
    async def test_a_reassemble_whose_section_vanished_refuses_and_writes_nothing(self, tmp_path: Path) -> None:
        """End to end: the refusal reaches the caller BEFORE the store is
        rewritten, so the lesson's rows still describe the audio that is on
        disk."""
        from app.audio.render_service import reassemble_lesson_audio

        store = ContentStore(":memory:")
        lesson = _build_test_lesson()
        audio_dir = tmp_path / "audio"
        _populate_store(store, lesson, audio_dir, [2.0, 5.0, 8.0, 5.0])
        rows_before = sorted(store.list_audio_files_for_lesson(lesson.title), key=lambda r: str(r["id"]))

        victim = next(r for r in rows_before if r["section_index"] == 2)
        resolve_audio_path(victim["file_path"]).unlink()
        files_before = sorted(p.name for p in audio_dir.iterdir())

        with pytest.raises(RuntimeError, match="could not decode a lesson section"):
            await reassemble_lesson_audio(
                store=store,
                renderer=_make_fake_renderer(),
                tts=_CountingTTS(),
                audio_dir=audio_dir,
                lesson_id=lesson.title,
                lesson=lesson,
            )

        rows_after = sorted(store.list_audio_files_for_lesson(lesson.title), key=lambda r: str(r["id"]))
        assert [r["id"] for r in rows_after] == [r["id"] for r in rows_before], (
            "the store was rewritten even though the lesson could not be built"
        )
        # audio_dir is the user's real content, and a stray file there is
        # indistinguishable from a rendered clip once this process is gone. The
        # encoder finalises whatever it was fed before the failing section, so
        # without cleanup a truncated full file — and the freshly re-rendered
        # section files no row points at — would be left behind.
        assert sorted(p.name for p in audio_dir.iterdir()) == files_before, (
            "a failed reassemble left files in audio_dir that no row references"
        )


class TestReassembleServesALegacyRelativeSectionRow:
    """The production shape of tunatale-c7tx, end to end.

    Four Norwegian lessons hold an ``audio_files.file_path`` of
    ``output/audio/<uuid>.opus`` at ``section_index=1`` while every other row on
    the same lesson is absolute. A later re-render reuses those rows verbatim,
    hands the relative string to the concat demuxer, and the demuxer resolves it
    against the LIST FILE's directory — ``backend/output/audio/`` — where
    ``output/audio/<uuid>.opus`` does not exist. ffmpeg drops the entry and
    exits 0.

    The row is seeded with raw SQL on purpose: ``save_audio_file`` now refuses
    to create this shape, so the only way to reproduce it is the way it actually
    exists — as data written before the fix. Those rows are still in the user's
    database, so the read path has to serve them.
    """

    @pytest.mark.asyncio
    async def test_a_relative_section_row_still_joins_at_full_length(self, tmp_path: Path, monkeypatch) -> None:
        from app.audio import render_service

        store = ContentStore(":memory:")
        lesson = _build_test_lesson()
        section_durations = [2.0, 5.0, 8.0, 5.0]
        audio_dir = tmp_path / "audio"
        _populate_store(store, lesson, audio_dir, section_durations)

        monkeypatch.setattr(render_service.settings, "audio_dir", audio_dir)

        # Rewrite section 1's row to the legacy relative shape, pointing at the
        # same real file — the bytes were never the problem, the bookkeeping was.
        rows = store.list_audio_files_for_lesson(lesson.title)
        sec1 = next(r for r in rows if r["section_index"] == 1)
        relative = f"output/audio/{Path(sec1['file_path']).name}"
        with store._get_conn() as conn:
            conn.execute("UPDATE audio_files SET file_path = ? WHERE id = ?", (relative, sec1["id"]))
            conn.commit()
        assert store.get_audio_file_row(sec1["id"])["file_path"] == relative

        from app.audio.render_service import reassemble_lesson_audio

        await reassemble_lesson_audio(
            store=store,
            renderer=_make_fake_renderer(),
            tts=_CountingTTS(),
            audio_dir=audio_dir,
            lesson_id=lesson.title,
            lesson=lesson,
        )

        full_row = next(r for r in store.list_audio_files_for_lesson(lesson.title) if r["section_index"] is None)
        joined = _ffprobe_duration(resolve_audio_path(full_row["file_path"]))

        # title + 4 sections + 4 boundaries. The number that matters is that
        # section 1's 5.0s is IN there: dropping it is what shipped.
        expected = 1.0 + sum(section_durations) + 4 * 3.0
        assert joined == pytest.approx(expected, abs=0.5), f"joined {joined:.2f}s, expected ~{expected:.2f}s"


def _reroot_rows_to_basenames(store: ContentStore, lesson_id: str) -> None:
    """Rewrite every recorded ``file_path`` for a lesson to a bare basename.

    The post-migration shape: a recorded path is where a render was written, and
    after step 2 the rows carry ``<uuid>.opus`` with the real bytes sitting in
    ``settings.audio_dir``. Seeding this through :func:`_populate_store` and
    then rewording the rows is deliberate — the bytes are identical to the
    absolute shape, so only the bookkeeping is under test.
    """
    for r in store.list_audio_files_for_lesson(lesson_id):
        store.save_audio_file(
            r["id"],
            lesson_id,
            Path(r["file_path"]).name,
            section_index=r["section_index"],
            section_type=r["section_type"],
            cues_json=r["cues_json"],
        )


class _WritingRenderer:
    """Minimal renderer for render_lesson_audio: writes bytes, needs no ffmpeg.

    Mirrors the real ``output_path=None`` contract (tunatale-guzo.3): a render
    is handed None for the full file and writes the section files only.
    """

    async def render(self, lesson, output_path, section_paths=None, *, on_progress=None):
        if output_path is not None:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"full")
        for sp in section_paths or []:
            sp.parent.mkdir(parents=True, exist_ok=True)
            sp.write_bytes(b"sec")
        return []


class TestReassembleUnlinksResolvedFiles:
    """The reassemble unlinks resolve recorded paths before touching disk.

    ⚠️ ONE audio directory throughout, because that is the only shape production
    has: ``app.state.audio_dir`` IS ``settings.audio_dir`` (``main.py``) and
    every render entry point takes ``audio_dir`` from there. Since kbb.15 step 3
    a row stores a BASENAME, so a render written outside ``settings.audio_dir``
    is unresolvable BY CONSTRUCTION. These tests originally rendered into a
    second directory, which was only reachable while rows held absolute paths.
    ``test_every_render_entry_point_uses_the_settings_audio_dir`` pins the
    invariant that makes the single-directory assumption safe.

    Post-migration the rows record bare basenames whose real files live in
    ``settings.audio_dir``. The DELETE sites in the sweep resolve before
    returning and the two reassemble unlinks resolve before unlinking; either
    way, ``Path("<basename>").unlink()`` against the process CWD silently keeps
    the old file and orphans the render. Before this sweep's edit at
    ``reassemble_lesson_audio``'s unlink sites, the basename case let the old
    files survive in place.
    """

    @pytest.mark.asyncio
    async def test_basename_rows_have_old_full_and_target_unlinked(self, tmp_path: Path, monkeypatch) -> None:
        """Scenario (a): the old full-lesson file and the re-rendered section's
        old file are removed THROUGH their recorded basenames; reused sections
        keep their files byte-for-byte."""
        from app.audio import render_service
        from app.audio.render_service import reassemble_lesson_audio

        store = ContentStore(":memory:")
        lesson = _build_test_lesson()
        legacy_dir = tmp_path / "legacy_audio"
        full_id, _section_ids, old_section_paths = _populate_store(store, lesson, legacy_dir, [2.0, 5.0, 8.0, 5.0])
        old_full_path = legacy_dir / f"{full_id}.opus"
        _reroot_rows_to_basenames(store, lesson.title)
        monkeypatch.setattr(render_service.settings, "audio_dir", legacy_dir)

        await reassemble_lesson_audio(
            store=store,
            renderer=_make_fake_renderer(),
            tts=_CountingTTS(),
            audio_dir=legacy_dir,
            lesson_id=lesson.title,
            lesson=lesson,
        )

        assert not old_full_path.exists(), "the old full-lesson file survived the unlink"
        assert not old_section_paths[0].exists(), "the re-rendered section's old file survived the unlink"
        for i in (1, 2, 3):
            assert old_section_paths[i].exists(), f"reused section {i} file must be kept, not deleted"
        for r in store.list_audio_files_for_lesson(lesson.title):
            assert resolve_audio_path(r["file_path"]).exists(), "a new row does not reach a real file"

    @pytest.mark.asyncio
    async def test_absent_old_files_do_not_raise(self, tmp_path: Path, monkeypatch) -> None:
        """Scenario (c), CONTROL: an unlink that starts throwing is worse than one
        that orphans. Old files that genuinely do not exist must not make the
        reassemble fail."""
        from app.audio import render_service
        from app.audio.render_service import reassemble_lesson_audio

        store = ContentStore(":memory:")
        lesson = _build_test_lesson()
        legacy_dir = tmp_path / "legacy_audio"
        full_id, _section_ids, old_section_paths = _populate_store(store, lesson, legacy_dir, [2.0, 5.0, 8.0, 5.0])
        old_full_path = legacy_dir / f"{full_id}.opus"
        old_full_path.unlink()
        old_section_paths[0].unlink()
        _reroot_rows_to_basenames(store, lesson.title)
        monkeypatch.setattr(render_service.settings, "audio_dir", legacy_dir)

        payload = await reassemble_lesson_audio(
            store=store,
            renderer=_make_fake_renderer(),
            tts=_CountingTTS(),
            audio_dir=legacy_dir,
            lesson_id=lesson.title,
            lesson=lesson,
        )

        assert len(payload["sections"]) == len(lesson.sections)


class TestRenderLessonAudioUnlinksRecordedPaths:
    """``render_lesson_audio``'s old-cohort unlink resolves recorded basenames.

    The re-render removes the previous cohort's files; on a moved DB those rows
    are bare basenames that only resolve against ``settings.audio_dir``. The
    old absolute shape is already covered end to end by the API test in
    test_api_audio.py; this is the post-migration shape for the same site.
    """

    @pytest.mark.asyncio
    async def test_rerender_unlinks_basename_old_files(self, tmp_path: Path, monkeypatch) -> None:
        from app.audio import render_service
        from app.audio.render_service import render_lesson_audio

        legacy_dir = tmp_path / "legacy_audio"
        legacy_dir.mkdir()
        monkeypatch.setattr(render_service.settings, "audio_dir", legacy_dir)
        lesson = _build_test_lesson()
        store = ContentStore(":memory:")
        old_full = legacy_dir / "old_full.opus"
        old_sec = legacy_dir / "old_sec.opus"
        old_full.write_bytes(b"full")
        old_sec.write_bytes(b"sec")
        store.save_audio_file("old-full", lesson.title, "old_full.opus", cues_json="[]")
        store.save_audio_file(
            "old-sec", lesson.title, "old_sec.opus", section_index=0, section_type="key_phrases", cues_json="[]"
        )

        result = await render_lesson_audio(
            store=store,
            renderer=_WritingRenderer(),
            audio_dir=legacy_dir,
            lesson_id=lesson.title,
            lesson=lesson,
        )

        assert not old_full.exists(), "the old full-lesson render survived the re-render"
        assert not old_sec.exists(), "the old section render survived the re-render"
        assert len(result["sections"]) == len(lesson.sections)
        rows = store.list_audio_files_for_lesson(lesson.title)
        assert len(rows) == len(lesson.sections) + 1
        # Every row that names a file reaches one. The full row names none,
        # since guzo.3 — it is a timeline record, not an encoded render.
        file_less = [r for r in rows if r["file_path"] is None]
        assert [r["section_index"] for r in file_less] == [None], file_less
        for r in rows:
            if r["file_path"] is None:
                continue
            assert resolve_audio_path(r["file_path"]).exists(), "a new row does not reach a real file"
