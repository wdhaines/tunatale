"""Lesson renderer — orchestrates preprocess → TTS → pauses → assembly."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from app.audio.assembler import AudioAssembler
from app.audio.pause_calculator import NaturalPauseCalculator
from app.audio.ports import TTSService
from app.audio.preprocessing.base import TextPreprocessor
from app.models.lesson import Lesson

logger = logging.getLogger(__name__)

# Assumed duration for pause calculation when we can't measure real audio
_DEFAULT_PHRASE_DURATION_S = 1.5


class LessonRenderer:
    """Renders a Lesson to an audio file.

    Pipeline per phrase:
      1. Preprocess text (language-specific)
      2. Synthesize via TTS → temp file
      3. Read bytes
      4. Calculate post-phrase pause
      5. Collect all chunks
    Then assemble with section-boundary gaps and write to output.
    """

    def __init__(
        self,
        tts: TTSService,
        preprocessor: TextPreprocessor,
        pause_calculator: NaturalPauseCalculator,
        assembler: AudioAssembler,
    ) -> None:
        self._tts = tts
        self._preprocessor = preprocessor
        self._calc = pause_calculator
        self._assembler = assembler

    async def render(self, lesson: Lesson, output_path: Path) -> None:
        """Render *lesson* to *output_path*.

        Args:
            lesson: Lesson with sections and phrases.
            output_path: Destination file path (written as raw audio).
        """
        all_chunks: list[bytes] = []
        boundary_silence = self._assembler.add_silence(self._calc.get_section_boundary_pause())

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)

            for section_idx, section in enumerate(lesson.sections):
                if section_idx > 0:
                    all_chunks.append(boundary_silence)

                for phrase_idx, phrase in enumerate(section.phrases):
                    processed = self._preprocessor.preprocess(phrase.text, section.section_type)
                    word_count = len(phrase.text.split())

                    phrase_file = tmp / f"s{section_idx}_p{phrase_idx}.mp3"
                    await self._tts.synthesize(
                        processed,
                        phrase.voice_id,
                        phrase_file,
                        rate="+0%",
                    )

                    audio_bytes = phrase_file.read_bytes()
                    all_chunks.append(audio_bytes)

                    pause_ms = self._calc.get_phrase_pause(
                        audio_duration_s=_DEFAULT_PHRASE_DURATION_S,
                        word_count=word_count,
                        section_type=section.section_type,
                    )
                    if pause_ms > 0:
                        all_chunks.append(self._assembler.add_silence(pause_ms))

        combined = self._assembler.concatenate(all_chunks, silence_ms=0)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(combined)
        logger.info("Rendered lesson to %s (%d bytes)", output_path, len(combined))
