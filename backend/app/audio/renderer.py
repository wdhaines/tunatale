"""Lesson renderer — orchestrates preprocess → TTS → pauses → assembly."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from pydub import AudioSegment

from app.audio.pause_calculator import NaturalPauseCalculator
from app.audio.ports import TTSService
from app.audio.preprocessing.base import TextPreprocessor
from app.models.lesson import Lesson

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

    async def render(self, lesson: Lesson, output_path: Path) -> None:
        """Render *lesson* to *output_path* as a valid WAV file.

        Args:
            lesson: Lesson with sections and phrases.
            output_path: Destination file path (written as WAV).
        """
        combined = AudioSegment.empty()
        boundary_silence = AudioSegment.silent(duration=self._calc.get_section_boundary_pause())

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)

            # Render lesson title
            title_file = tmp / "title.mp3"
            await self._tts.synthesize(lesson.title, lesson.narrator_voice, title_file, rate="+0%")
            combined += AudioSegment.from_file(str(title_file))
            combined += boundary_silence

            for section_idx, section in enumerate(lesson.sections):
                if section_idx > 0:
                    combined += boundary_silence

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

                    seg = AudioSegment.from_file(str(phrase_file))
                    audio_duration_s = len(seg) / 1000.0
                    combined += seg

                    pause_ms = self._calc.get_phrase_pause(
                        audio_duration_s=audio_duration_s,
                        word_count=word_count,
                        section_type=section.section_type,
                    )
                    if pause_ms > 0:
                        combined += AudioSegment.silent(duration=pause_ms)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        combined.export(str(output_path), format="wav")
        logger.info("Rendered lesson to %s (%d ms)", output_path, len(combined))
