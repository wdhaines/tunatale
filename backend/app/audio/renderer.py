"""Lesson renderer — orchestrates preprocess → TTS → pauses → assembly."""

from __future__ import annotations

import logging
import tempfile
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
        seg = AudioSegment.empty()
        for phrase_idx, phrase in enumerate(section.phrases):
            processed = self._preprocessor.preprocess(phrase.text, section.section_type)
            word_count = len(phrase.text.split())

            phrase_file = tmp / f"s{section_idx}_p{phrase_idx}.mp3"
            await self._tts.synthesize(
                processed,
                phrase.voice_id,
                phrase_file,
                rate=phrase.rate,
            )

            phrase_seg = AudioSegment.from_file(str(phrase_file))
            audio_duration_s = len(phrase_seg) / 1000.0
            seg += phrase_seg

            pause_ms = self._calc.get_phrase_pause(
                audio_duration_s=audio_duration_s,
                word_count=word_count,
                section_type=section.section_type,
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
        boundary_silence = AudioSegment.silent(duration=self._calc.get_section_boundary_pause())

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)

            # Render lesson title (full WAV only — not in section files)
            title_file = tmp / "title.mp3"
            await self._tts.synthesize(lesson.title, lesson.narrator_voice, title_file, rate="+0%")
            title_seg = AudioSegment.from_file(str(title_file))

            # Render each section to its own AudioSegment
            section_segs: list[AudioSegment] = []
            for section_idx, section in enumerate(lesson.sections):
                sec_seg = await self._render_section(section, tmp, section_idx)
                section_segs.append(sec_seg)

                # Write per-section file if requested
                if section_paths is not None:
                    sp = section_paths[section_idx]
                    sp.parent.mkdir(parents=True, exist_ok=True)
                    sec_seg.export(str(sp), format="wav")

            # Assemble full lesson: title + bs + sec0 + bs + sec1 + ...
            combined = title_seg + boundary_silence
            for i, sec_seg in enumerate(section_segs):
                if i > 0:
                    combined += boundary_silence
                combined += sec_seg

        output_path.parent.mkdir(parents=True, exist_ok=True)
        combined.export(str(output_path), format="wav")
        logger.info("Rendered lesson to %s (%d ms)", output_path, len(combined))
