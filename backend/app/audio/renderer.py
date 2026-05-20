"""Lesson renderer — orchestrates preprocess → TTS → pauses → assembly."""

from __future__ import annotations

import asyncio
import logging
import tempfile
import time
from pathlib import Path

from pydub import AudioSegment

from app.audio.pause_calculator import NaturalPauseCalculator
from app.audio.ports import TTSService
from app.audio.preprocessing.base import TextPreprocessor
from app.models.lesson import Lesson, Section

logger = logging.getLogger(__name__)


class LessonRenderer:
    """Renders a Lesson to a WAV audio file using pydub for assembly.

    Pipeline per phrase:
      1. Preprocess text (language-specific)
      2. Synthesize via TTS → temp file
      3. Load as AudioSegment, measure actual duration
      4. Calculate post-phrase pause from real duration
      5. Concatenate all segments with boundary gaps
    Then export the combined AudioSegment as WAV.
    """

    def __init__(
        self,
        tts: TTSService,
        preprocessor: TextPreprocessor,
        pause_calculator: NaturalPauseCalculator,
    ) -> None:
        self._tts = tts
        self._preprocessor = preprocessor
        self._calc = pause_calculator

    async def _render_section(self, section: Section, tmp: Path, section_idx: int) -> AudioSegment:
        """Render a single section to an AudioSegment (no boundary silence).

        Args:
            section: The Section to render.
            tmp: Temp directory for intermediate TTS files.
            section_idx: Index used for temp file naming.

        Returns:
            AudioSegment containing all phrases with inter-phrase pauses.
        """
        phrase_files = [tmp / f"s{section_idx}_p{i}.mp3" for i in range(len(section.phrases))]
        processed_texts = [
            self._preprocessor.preprocess(phrase.text, section.section_type) for phrase in section.phrases
        ]

        # Synthesize all phrases in this section concurrently.
        # EdgeTTSService._semaphore limits total concurrent requests globally.
        await asyncio.gather(
            *[
                self._tts.synthesize(text, phrase.voice_id, phrase_files[i], rate=phrase.rate)
                for i, (text, phrase) in enumerate(zip(processed_texts, section.phrases, strict=True))
            ]
        )

        # Assemble in phrase order (order is preserved by the pre-allocated paths)
        seg = AudioSegment.empty()
        for i, phrase in enumerate(section.phrases):
            phrase_seg = AudioSegment.from_file(str(phrase_files[i]))
            audio_duration_s = len(phrase_seg) / 1000.0
            seg += phrase_seg

            pause_ms = self._calc.get_phrase_pause(
                audio_duration_s=audio_duration_s,
                word_count=len(phrase.text.split()),
                section_type=section.section_type,
                language_code=phrase.language_code,
            )
            if pause_ms > 0:
                seg += AudioSegment.silent(duration=pause_ms)

        return seg

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
        boundary_silence = AudioSegment.silent(duration=self._calc.get_section_boundary_pause())

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)

            # Render lesson title (full WAV only — not in section files)
            t0 = time.perf_counter()
            title_file = tmp / "title.mp3"
            await self._tts.synthesize(lesson.title, lesson.narrator_voice, title_file, rate="+0%")
            logger.debug("TTS title → %.0f ms", (time.perf_counter() - t0) * 1000)
            title_seg = AudioSegment.from_file(str(title_file))

            # Render all sections concurrently — phrases within each section are
            # also parallelised; EdgeTTSService._semaphore caps total concurrency.
            t0 = time.perf_counter()
            section_segs: list[AudioSegment] = list(
                await asyncio.gather(
                    *[self._render_section(section, tmp, i) for i, section in enumerate(lesson.sections)]
                )
            )
            logger.debug("All sections TTS → %.0f ms", (time.perf_counter() - t0) * 1000)

            if section_paths is not None:
                for section_idx, sec_seg in enumerate(section_segs):
                    sp = section_paths[section_idx]
                    sp.parent.mkdir(parents=True, exist_ok=True)
                    t0 = time.perf_counter()
                    # pydub's export() returns an unclosed file handle; close it.
                    sec_seg.export(str(sp), format="wav").close()
                    logger.debug("Section %d export → %.0f ms", section_idx, (time.perf_counter() - t0) * 1000)

            # Assemble full lesson: title + bs + sec0 + bs + sec1 + ...
            combined = title_seg + boundary_silence
            for i, sec_seg in enumerate(section_segs):
                if i > 0:
                    combined += boundary_silence
                combined += sec_seg

        output_path.parent.mkdir(parents=True, exist_ok=True)
        t0 = time.perf_counter()
        # pydub's export() returns an unclosed file handle; close it.
        combined.export(str(output_path), format="wav").close()
        logger.debug("Full lesson export → %.0f ms", (time.perf_counter() - t0) * 1000)
        total_ms = (time.perf_counter() - t_start) * 1000
        logger.info(
            "Rendered lesson to %s (audio: %d ms, wall: %.0f ms)",
            output_path,
            len(combined),
            total_ms,
        )
