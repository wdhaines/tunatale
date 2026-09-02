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

    async def list_voices(self, language_code: str | None = None) -> list[dict]:
        return []


def _make_opus_file(path: Path, duration_s: float = 1.0) -> None:
    """Create a valid Opus file of the given duration at 24kHz mono."""
    from app.audio.transcode import encode_audio

    samples = np.zeros((int(24000 * duration_s), 1), dtype="float32")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encode_audio(samples, 24000, "opus", "28k"))


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


def _full_path(store, lesson_id: str) -> Path:
    """The stored full-lesson file. The payload mirrors render_lesson_audio's
    shape (audio_id / lesson_id / sections / cues) and deliberately does not add
    a key that one — so the path is read back the way the app reads it."""
    row = next(r for r in store.list_audio_files_for_lesson(lesson_id) if r["section_index"] is None)
    return Path(row["file_path"])


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
    store: ContentStore, lesson: Lesson, audio_dir: Path, section_durations: list[float]
) -> tuple[str, list[str], list[Path]]:
    """Seed the ContentStore with a full-lesson row + section rows.

    Returns (full_audio_id, section_ids, section_paths_on_disk).
    """
    from uuid import uuid4

    full_id = str(uuid4())
    full_path = audio_dir / f"{full_id}.opus"
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
    store.save_audio_file(full_id, lesson.title, str(full_path), cues_json=cues_json)
    for i, (sid, sp, sec) in enumerate(zip(section_ids, section_paths, lesson.sections, strict=True)):
        sec_cues = [c for c in cues if c.section_index == i]
        # Rebase section cues to start at 0 (matching derive_section_cues behavior)
        if sec_cues:
            first_start = sec_cues[0].start_ms
            rebased = [replace(c, start_ms=c.start_ms - first_start, end_ms=c.end_ms - first_start) for c in sec_cues]
        else:
            rebased = []
        sec_cues_json = json.dumps([asdict(c) for c in rebased])
        _make_opus_file(sp, section_durations[i])
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

        async def render(self, lesson, output_path, section_paths=None):
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
                pre_hashes[r["section_index"]] = _sha256(Path(r["file_path"]))

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
                post_hash = _sha256(Path(r["file_path"]))
                assert pre_hashes[r["section_index"]] == post_hash, (
                    f"Section {r['section_index']} file changed: {pre_hashes[r['section_index']]} → {post_hash}"
                )


class TestReassembleDuration:
    """Full file duration equals title + boundary*(N-1) + sum(section durations) within tolerance (acceptance #3)."""

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
        kp_dur = _ffprobe_duration(Path(kp_section_rows[0]["file_path"]))
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
    """Cue for phrase in LAST section still lands on correct audio after KEY_PHRASES length change (acceptance #5)."""

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
        old_full_dur = _ffprobe_duration(Path(old_full["file_path"]))

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


class TestConcatAndProbeEdges:
    """The helpers' refusal paths. Each is reachable and each is a real failure
    mode: a caller with nothing to concatenate, a single-file concat (where
    ffmpeg would be pure overhead), and ffmpeg/ffprobe exiting non-zero."""

    def test_concat_refuses_an_empty_list(self, tmp_path: Path) -> None:
        from app.audio.render_service import _concat_stream_copy

        with pytest.raises(ValueError, match="at least one file"):
            _concat_stream_copy([], tmp_path / "out.opus")

    def test_concat_of_one_file_copies_bytes_verbatim(self, tmp_path: Path) -> None:
        """No ffmpeg for a single input — the bytes are the answer already."""
        from app.audio.render_service import _concat_stream_copy

        src = tmp_path / "a.opus"
        _make_opus_file(src, 0.5)
        out = tmp_path / "nested" / "out.opus"
        _concat_stream_copy([src], out)
        assert out.read_bytes() == src.read_bytes()

    def test_concat_raises_when_ffmpeg_fails(self, tmp_path: Path) -> None:
        from app.audio.render_service import _concat_stream_copy

        bad = tmp_path / "not-audio.opus"
        bad.write_bytes(b"this is not an opus stream")
        other = tmp_path / "b.opus"
        _make_opus_file(other, 0.5)
        with pytest.raises(RuntimeError, match="ffmpeg concat failed"):
            _concat_stream_copy([bad, other], tmp_path / "out.opus")

    def test_duration_raises_when_ffprobe_fails(self, tmp_path: Path) -> None:
        from app.audio.render_service import _read_audio_duration

        missing = tmp_path / "nope.opus"
        with pytest.raises(RuntimeError, match="ffprobe failed"):
            _read_audio_duration(missing)

    def test_duration_raises_when_ffprobe_returns_no_number(self, tmp_path: Path) -> None:
        """ffprobe can exit 0 and still print nothing parseable.

        A real case, not a mock: handed a PNG, ffprobe identifies the container,
        exits 0, and prints "N/A" for format=duration. Left unguarded, float()
        raises ValueError deep inside reassembly; guarded, it must be LOUD
        rather than a silent 0.0, which would collapse every later cue offset
        onto the section before it.
        """
        import struct
        import zlib

        from app.audio.render_service import _read_audio_duration

        def _chunk(tag: bytes, data: bytes) -> bytes:
            return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

        png = tmp_path / "not-audio.png"
        png.write_bytes(
            b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(b"\x00\x00"))
            + _chunk(b"IEND", b"")
        )
        with pytest.raises(RuntimeError, match="invalid duration"):
            _read_audio_duration(png)

    def test_silence_can_be_written_as_wav(self, tmp_path: Path, monkeypatch) -> None:
        """The wav delivery codec skips ffmpeg entirely."""
        from app.audio import render_service
        from app.audio.render_service import _write_silence

        monkeypatch.setattr(render_service.settings, "audio_delivery_codec", "wav")
        out = tmp_path / "sil.wav"
        _write_silence(out, 250, 48000)
        assert abs(_ffprobe_duration(out) - 0.25) < 0.01


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


class TestTitleTranscode:
    """_transcode_to_delivery normalises the TTS's MP3 into the delivery codec.

    Every input to the concat demuxer must agree on codec, rate and channel
    count. The TTS returns MP3 at its own rate whatever the caller names the
    file, so this is the adapter between the two — and each branch here is a
    real mismatch it has to absorb.
    """

    def test_matching_rate_is_not_resampled(self, tmp_path: Path) -> None:
        """The common case once the TTS and the sections agree: no resample."""
        import soundfile as sf

        from app.audio.render_service import _transcode_to_delivery

        src = tmp_path / "in.wav"
        sf.write(str(src), np.zeros((48000, 1), dtype="float32"), 48000, subtype="PCM_16")
        dest = tmp_path / "out.opus"
        _transcode_to_delivery(src, dest, 48000)
        assert abs(_ffprobe_duration(dest) - 1.0) < 0.05

    def test_mismatched_rate_is_resampled_to_the_target(self, tmp_path: Path) -> None:
        """Duration must survive the rate change — a resample that drops or
        doubles frames would shift every cue after the title."""
        import soundfile as sf

        from app.audio.render_service import _transcode_to_delivery

        src = tmp_path / "in.wav"
        sf.write(str(src), np.zeros((24000, 1), dtype="float32"), 24000, subtype="PCM_16")
        dest = tmp_path / "out.opus"
        _transcode_to_delivery(src, dest, 48000)
        assert abs(_ffprobe_duration(dest) - 1.0) < 0.05

    def test_stereo_is_downmixed_to_mono(self, tmp_path: Path) -> None:
        """The section files are mono; a stereo title would make the concat
        demuxer refuse the copy."""
        import soundfile as sf

        from app.audio.render_service import _transcode_to_delivery

        src = tmp_path / "in.wav"
        sf.write(str(src), np.zeros((48000, 2), dtype="float32"), 48000, subtype="PCM_16")
        dest = tmp_path / "out.wav"
        _transcode_to_delivery(src, dest, 48000)
        info = sf.info(str(dest))
        assert info.channels == 1

    def test_wav_delivery_skips_ffmpeg(self, tmp_path: Path, monkeypatch) -> None:
        import soundfile as sf

        from app.audio import render_service
        from app.audio.render_service import _transcode_to_delivery

        monkeypatch.setattr(render_service.settings, "audio_delivery_codec", "wav")
        src = tmp_path / "in.wav"
        sf.write(str(src), np.zeros((48000, 1), dtype="float32"), 48000, subtype="PCM_16")
        dest = tmp_path / "out.wav"
        _transcode_to_delivery(src, dest, 48000)
        assert sf.info(str(dest)).samplerate == 48000


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
        kp_sha_before = _sha256(Path(kp_path_before))
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
        assert _sha256(Path(rows_after[0]["file_path"])) == kp_sha_before, "KEY_PHRASES file must be byte-identical"
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
        sha_before = {i: _sha256(Path(r["file_path"])) for i, r in rows_before.items() if r is not None}

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
            assert _sha256(Path(rows_after[i]["file_path"])) == sha_before[i], f"section {i} must stay byte-identical"

    @pytest.mark.asyncio
    async def test_o3_discriminator_middle_target_offset_arithmetic(self, tmp_path: Path) -> None:
        """Re-render TRANSLATED alone (index 2, sections on both sides) and pin
        every cue position against a ffprobe-consistent 'before' manifest.

        The seeded manifest records IDEAL durations (2.0/5.0/8.0/5.0s), but the
        actual on-disk files each carry ~6.5ms of codec container padding and the
        re-synthesised title carries ~62ms of MP3 round-trip padding. So the
        'before' baseline is reconstructed from the SAME measured quantities
        reassemble uses — its own title round-trip and round(ffprobe(...)*1000)
        per section — which makes the 'identical'/'shifted by exactly' claims
        exact, not approximate. An implementation that gets the target offset
        wrong (invisible at index 0) breaks section 3's shift.
        """
        from app.audio.render_service import _read_audio_duration, _transcode_to_delivery, reassemble_lesson_audio

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

        # The duration reassemble WILL use for each section (round(ffprobe)),
        # measured from the seeded files — the container padding is included.
        dur_ms = [round(_ffprobe_duration(Path(r["file_path"])) * 1000) for r in rows_ordered]

        # The title reassemble will measure: same bytes, same round trip.
        rt_mp3 = tmp_path / "title-roundtrip.mp3"
        await tts.synthesize(lesson.title, lesson.narrator_voice, rt_mp3, rate="+0%")
        rt_opus = tmp_path / "title-roundtrip.opus"
        _transcode_to_delivery(rt_mp3, rt_opus, rate)
        title_ms = round(_read_audio_duration(rt_opus) * 1000)

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
        #     delta — the section at index 2 was 8.0s, now it is 1.5s.
        new_rows = {r["section_index"]: r for r in store.list_audio_files_for_lesson(lesson.title)}
        new_sec2_dur_ms = round(_ffprobe_duration(Path(new_rows[2]["file_path"])) * 1000)
        delta_ms = new_sec2_dur_ms - dur_ms[2]
        sec3_before = sorted(section_cues_before[3], key=lambda c: c["phrase_index"])
        sec3_after = sorted(after_by_sec[3], key=lambda c: c["phrase_index"])
        assert len(sec3_after) == len(sec3_before)
        for b_cue, a_cue in zip(sec3_before, sec3_after, strict=True):
            assert a_cue["start_ms"] - b_cue["start_ms"] == delta_ms, (b_cue, a_cue, delta_ms)
            assert a_cue["end_ms"] - b_cue["end_ms"] == delta_ms, (b_cue, a_cue, delta_ms)

        # (c) The full-lesson file's duration equals title + 4*boundary + the
        #     four (re-derived) section durations, within 60ms.
        expected_total = (
            title_ms
            + 4 * boundary_ms
            + sum(round(_ffprobe_duration(Path(r["file_path"])) * 1000) for r in (new_rows[i] for i in range(4)))
        )
        actual_total = _ffprobe_duration(_full_path(store, lesson.title)) * 1000
        assert abs(actual_total - expected_total) < 60, (actual_total, expected_total)

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
