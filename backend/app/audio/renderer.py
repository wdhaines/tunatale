"""Lesson renderer — orchestrates preprocess → TTS → pauses → assembly."""

from __future__ import annotations

import asyncio
import logging
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

from app.audio.pause_calculator import NaturalPauseCalculator
from app.audio.ports import TTSService
from app.audio.preprocessing.base import TextPreprocessor
from app.audio.transcode import encode_audio
from app.models.lesson import Lesson, Section

logger = logging.getLogger(__name__)

_SAMPLE_DTYPE = "float32"
_WAV_SUBTYPE = "PCM_16"


@dataclass
class _Audio:
    """A decoded audio buffer: float32 samples shaped ``(frames, channels)`` + rate.

    Replaces pydub's ``AudioSegment`` for the small set of operations the
    renderer needs (decode, measure, silence, concatenate, export to WAV), so the
    audio pipeline depends only on maintained libraries (``soundfile`` decodes
    EdgeTTS MP3 via bundled libsndfile; ``numpy`` does the assembly).
    """

    samples: np.ndarray
    rate: int

    @property
    def duration_ms(self) -> float:
        return len(self.samples) / self.rate * 1000.0


def _read_audio(path: Path) -> _Audio:
    """Decode an audio file (EdgeTTS MP3 in prod, WAV in tests) to float32 samples."""
    samples, rate = sf.read(str(path), dtype=_SAMPLE_DTYPE, always_2d=True)
    return _Audio(samples, int(rate))


def _silence(duration_ms: float, like: _Audio) -> _Audio:
    """A silent buffer of *duration_ms*, matching *like*'s rate and channel count."""
    frames = round(duration_ms / 1000.0 * like.rate)
    return _Audio(np.zeros((frames, like.samples.shape[1]), dtype=_SAMPLE_DTYPE), like.rate)


def _concat(parts: list[_Audio]) -> _Audio:
    """Concatenate audio buffers that share sample rate and channel count.

    EdgeTTS emits a uniform 24 kHz mono stream for every voice, so this holds in
    practice. A mismatch means a foreign/corrupt input; we fail loudly rather
    than silently re-speed it — pydub's implicit ``_sync`` resample used to hide
    that. Always called with a non-empty list (a section always has ≥1 phrase;
    the full mix always starts with the lesson title).
    """
    head = parts[0]
    channels = head.samples.shape[1]
    for part in parts[1:]:
        if part.rate != head.rate or part.samples.shape[1] != channels:
            raise ValueError(
                "cannot concatenate audio with mismatched format: "
                f"expected {head.rate} Hz / {channels} ch, "
                f"got {part.rate} Hz / {part.samples.shape[1]} ch"
            )
    return _Audio(np.concatenate([p.samples for p in parts], axis=0), head.rate)


def _write_wav(path: Path, audio: _Audio) -> None:
    """Write *audio* to *path* as a 16-bit PCM WAV."""
    sf.write(str(path), audio.samples, audio.rate, subtype=_WAV_SUBTYPE)


class LessonRenderer:
    """Renders a Lesson to a WAV audio file using soundfile + numpy for assembly.

    Pipeline per phrase:
      1. Preprocess text (language-specific)
      2. Synthesize via TTS → temp file
      3. Decode to samples, measure actual duration
      4. Calculate post-phrase pause from real duration
      5. Concatenate all buffers with boundary gaps
    Then export the combined buffer as WAV.
    """

    def __init__(
        self,
        tts: TTSService,
        preprocessors: dict[str, TextPreprocessor],
        pause_calculator: NaturalPauseCalculator,
        delivery_codec: str = "wav",
        delivery_bitrate: str = "28k",
    ) -> None:
        self._tts = tts
        self._preprocessors = preprocessors
        self._calc = pause_calculator
        self._delivery_codec = delivery_codec
        self._delivery_bitrate = delivery_bitrate

    def _write_audio(self, path: Path, audio: _Audio) -> None:
        """Write *audio* to *path* in the configured delivery codec.

        ``"wav"`` writes uncompressed PCM (the historical default); any other
        codec routes the buffer through ffmpeg for a compressed, mobile-friendly
        file. The caller is responsible for giving *path* the matching extension.
        """
        if self._delivery_codec == "wav":
            _write_wav(path, audio)
        else:
            path.write_bytes(encode_audio(audio.samples, audio.rate, self._delivery_codec, self._delivery_bitrate))

    async def _render_section(self, section: Section, tmp: Path, section_idx: int, language_code: str = "sl") -> _Audio:
        """Render a single section to an audio buffer (no boundary silence).

        Args:
            section: The Section to render.
            tmp: Temp directory for intermediate TTS files.
            section_idx: Index used for temp file naming.
            language_code: Language code for preprocessor lookup.

        Returns:
            Audio buffer containing all phrases with inter-phrase pauses.
        """
        preprocessor = self._preprocessors.get(language_code, next(iter(self._preprocessors.values())))
        phrase_files = [tmp / f"s{section_idx}_p{i}.mp3" for i in range(len(section.phrases))]
        processed_texts = [preprocessor.preprocess(phrase.text, section.section_type) for phrase in section.phrases]

        # Synthesize all phrases in this section concurrently.
        # EdgeTTSService._semaphore limits total concurrent requests globally.
        await asyncio.gather(
            *[
                self._tts.synthesize(text, phrase.voice_id, phrase_files[i], rate=phrase.rate)
                for i, (text, phrase) in enumerate(zip(processed_texts, section.phrases, strict=True))
            ]
        )

        # Assemble in phrase order (order is preserved by the pre-allocated paths)
        parts: list[_Audio] = []
        for i, phrase in enumerate(section.phrases):
            phrase_audio = _read_audio(phrase_files[i])
            parts.append(phrase_audio)

            pause_ms = self._calc.get_phrase_pause(
                audio_duration_s=phrase_audio.duration_ms / 1000.0,
                word_count=len(phrase.text.split()),
                section_type=section.section_type,
                language_code=phrase.language_code,
            )
            if pause_ms > 0:
                parts.append(_silence(pause_ms, phrase_audio))

        return _concat(parts)

    async def render(
        self,
        lesson: Lesson,
        output_path: Path,
        section_paths: list[Path] | None = None,
    ) -> None:
        """Render *lesson* to *output_path* as a valid WAV file.

        Optionally writes per-section WAV files to *section_paths* (one per
        section, in lesson order). Each section file contains only the section
        content with no leading/trailing boundary silence.

        Args:
            lesson: Lesson with sections and phrases.
            output_path: Destination file path for the full lesson (written as WAV).
            section_paths: Optional list of paths for per-section output WAVs.
                           Must have same length as lesson.sections if provided.
        """
        t_start = time.perf_counter()

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)

            # Render lesson title (full WAV only — not in section files)
            t0 = time.perf_counter()
            title_file = tmp / "title.mp3"
            await self._tts.synthesize(lesson.title, lesson.narrator_voice, title_file, rate="+0%")
            logger.debug("TTS title → %.0f ms", (time.perf_counter() - t0) * 1000)
            title_audio = _read_audio(title_file)

            # Render all sections concurrently — phrases within each section are
            # also parallelised; EdgeTTSService._semaphore caps total concurrency.
            t0 = time.perf_counter()
            section_audios: list[_Audio] = list(
                await asyncio.gather(
                    *[
                        self._render_section(section, tmp, i, language_code=lesson.language_code)
                        for i, section in enumerate(lesson.sections)
                    ]
                )
            )
            logger.debug("All sections TTS → %.0f ms", (time.perf_counter() - t0) * 1000)

            if section_paths is not None:
                for section_idx, sec_audio in enumerate(section_audios):
                    sp = section_paths[section_idx]
                    sp.parent.mkdir(parents=True, exist_ok=True)
                    t0 = time.perf_counter()
                    self._write_audio(sp, sec_audio)
                    logger.debug("Section %d export → %.0f ms", section_idx, (time.perf_counter() - t0) * 1000)

            # Assemble full lesson: title + bs + sec0 + bs + sec1 + ...
            boundary = _silence(self._calc.get_section_boundary_pause(), title_audio)
            parts: list[_Audio] = [title_audio, boundary]
            for i, sec_audio in enumerate(section_audios):
                if i > 0:
                    parts.append(boundary)
                parts.append(sec_audio)
            combined = _concat(parts)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        t0 = time.perf_counter()
        self._write_audio(output_path, combined)
        logger.debug("Full lesson export → %.0f ms", (time.perf_counter() - t0) * 1000)
        logger.info(
            "Rendered lesson to %s (audio: %d ms, wall: %.0f ms)",
            output_path,
            round(combined.duration_ms),
            (time.perf_counter() - t_start) * 1000,
        )
