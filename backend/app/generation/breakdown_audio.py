"""CLI audio preview for the Norwegian Pimsleur breakdown.

Renders, per word, two clips that reproduce the exact TTS + inter-chunk pauses a
real lesson produces, so the splits and pronunciation can be confirmed by ear:

  * the full Pimsleur breakdown sequence (a KEY_PHRASES section), and
  * the slow-speed form (a SLOW_SPEED section).

    uv run python -m app.generation.breakdown_preview <word> [<word> ...] [--out DIR]

This module is the thin audio/CLI glue: it wires the real audio pipeline
(``EdgeTTSService`` + ``NaturalPauseCalculator`` + ``LessonRenderer``) to the
pure, fully-tested helpers in ``norwegian_breakdown`` / ``breakdown_preview``.
Because every line here is I/O against the TTS process boundary and the
filesystem, the module is coverage-omitted (see ``pyproject.toml``), following
the ``build_function_word_list.py`` convention — the testable logic lives in the
pure helpers, not here.
"""

from __future__ import annotations

import argparse
import asyncio
import tempfile
from pathlib import Path

from app.audio.edge_tts import EdgeTTSService
from app.audio.pause_calculator import NaturalPauseCalculator
from app.audio.renderer import LessonRenderer
from app.audio.transcode import CODEC_EXT
from app.config import settings
from app.generation.breakdown_preview import format_breakdown_preview
from app.generation.section_builder import (
    build_key_phrases_section,
    build_slow_speed_section,
)
from app.languages import get_preprocessor, get_tts_voice
from app.models.lesson import KeyPhraseInfo, Lesson

_LANGUAGE_CODE = "no"
_NARRATOR_VOICE = "en-US-GuyNeural"


def _slug(word: str) -> str:
    """Filesystem-safe stem for a word's output files."""
    return "".join(c if c.isalnum() else "_" for c in word.strip().lower()) or "word"


def build_preview_lesson(word: str, voice_id: str) -> Lesson:
    """Build a two-section Lesson (breakdown + slow form) for a single word.

    Reuses the real ``build_key_phrases_section`` / ``build_slow_speed_section``
    so the TTS voice, per-phrase text, and inter-chunk pauses are byte-for-byte
    what a production lesson emits (the whole point of the preview). The word is
    passed as its own translation placeholder — the English narrator reads it —
    since a real gloss isn't needed to confirm pronunciation.
    """
    voice_map = {"female-1": voice_id}
    key_phrases = [{"phrase": word, "translation": word}]
    slow_scene = [{"label": word, "lines": [{"speaker": "female-1", "text": word, "translation": word}]}]
    return Lesson(
        title=f"Breakdown preview: {word}",
        language_code=_LANGUAGE_CODE,
        sections=[
            build_key_phrases_section(key_phrases, voice_map, _NARRATOR_VOICE, _LANGUAGE_CODE),
            build_slow_speed_section(slow_scene, voice_map, _NARRATOR_VOICE, _LANGUAGE_CODE),
        ],
        narrator_voice=_NARRATOR_VOICE,
        key_phrases=[KeyPhraseInfo(phrase=word, translation=word)],
    )


def _build_renderer() -> LessonRenderer:
    return LessonRenderer(
        tts=EdgeTTSService(),
        preprocessors={_LANGUAGE_CODE: get_preprocessor(_LANGUAGE_CODE)},
        pause_calculator=NaturalPauseCalculator(),
        delivery_codec=settings.audio_delivery_codec,
        delivery_bitrate=settings.audio_delivery_bitrate,
    )


async def render_word_previews(words: list[str], out_dir: Path) -> dict[str, list[Path]]:
    """Render breakdown + slow clips for each word. Returns {word: [breakdown, slow]}."""
    out_dir.mkdir(parents=True, exist_ok=True)
    renderer = _build_renderer()
    voice_id = get_tts_voice(_LANGUAGE_CODE, "female-1")
    ext = CODEC_EXT.get(settings.audio_delivery_codec, "wav")

    results: dict[str, list[Path]] = {}
    for word in words:
        lesson = build_preview_lesson(word, voice_id)
        slug = _slug(word)
        full_path = out_dir / f"{slug}.{ext}"
        section_paths = [
            out_dir / f"{slug}_breakdown.{ext}",
            out_dir / f"{slug}_slow.{ext}",
        ]
        await renderer.render(lesson, full_path, section_paths=section_paths)
        results[word] = section_paths
    return results


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m app.generation.breakdown_preview",
        description="Preview + render Norwegian Pimsleur breakdowns.",
    )
    parser.add_argument("words", nargs="+", help="Word(s) to break down.")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Directory for rendered audio (default: a temp dir).",
    )
    parser.add_argument(
        "--no-audio",
        action="store_true",
        help="Print the text report only; skip audio rendering.",
    )
    args = parser.parse_args(argv)

    for word in args.words:
        print(format_breakdown_preview(word))

    if args.no_audio:
        return

    out_dir = args.out or Path(tempfile.mkdtemp(prefix="tt_breakdown_"))
    results = asyncio.run(render_word_previews(args.words, out_dir))
    print("=== Rendered audio ===")
    for word, (breakdown_path, slow_path) in results.items():
        print(f"  {word}")
        print(f"    breakdown: {breakdown_path}")
        print(f"    slow:      {slow_path}")


if __name__ == "__main__":  # pragma: no cover — CLI guard
    main()
