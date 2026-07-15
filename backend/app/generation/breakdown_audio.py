"""CLI audio preview for the Norwegian Pimsleur breakdown.

Renders, per word, two Norwegian-only clips (no English narration) that
reproduce the exact TTS + inter-chunk pauses a real lesson produces, so the
splits and pronunciation can be confirmed by ear:

  * ``<word>_breakdown.<ext>`` — the full Pimsleur breakdown sequence, and
  * ``<word>_slow.<ext>`` — the slow-speed form.

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
from app.languages import get_preprocessor, get_tts_voice
from app.models.lesson import Phrase, Section, SectionType
from app.plugins.languages.no.norwegian_breakdown import (
    build_norwegian_breakdown,
    slow_norwegian_word,
)

_LANGUAGE_CODE = "no"


def _slug(word: str) -> str:
    """Filesystem-safe stem for a word's output files."""
    return "".join(c if c.isalnum() else "_" for c in word.strip().lower()) or "word"


def _phrase(text: str, voice_id: str) -> Phrase:
    return Phrase(text=text, voice_id=voice_id, language_code=_LANGUAGE_CODE, role="female-1")


def build_preview_sections(word: str, voice_id: str) -> tuple[Section, Section]:
    """Build the Norwegian-only (breakdown, slow) sections for a single word.

    Deliberately *not* the real ``build_key_phrases_section`` — that prepends an
    English "Key Phrases" title and reads the L2 word with the English narrator,
    which is useless for confirming Norwegian pronunciation. These sections carry
    only the female-1 Norwegian chunks. Crucially the breakdown section keeps
    ``SectionType.KEY_PHRASES`` so ``NaturalPauseCalculator`` still applies the
    1:1 audio-duration inter-chunk pause (the Pimsleur "repeat it back" pacing);
    a different section type would flatten every gap to 500 ms.
    """
    breakdown = Section(
        section_type=SectionType.KEY_PHRASES,
        phrases=[_phrase(step, voice_id) for step in build_norwegian_breakdown(word)],
    )
    slow = Section(
        section_type=SectionType.SLOW_SPEED,
        phrases=[_phrase(slow_norwegian_word(word), voice_id)],
    )
    return breakdown, slow


def _build_renderer() -> LessonRenderer:
    return LessonRenderer(
        tts=EdgeTTSService(),
        preprocessors={_LANGUAGE_CODE: get_preprocessor(_LANGUAGE_CODE)},
        pause_calculator=NaturalPauseCalculator(),
        delivery_codec=settings.audio_delivery_codec,
        delivery_bitrate=settings.audio_delivery_bitrate,
    )


async def render_word_previews(words: list[str], out_dir: Path) -> dict[str, list[Path]]:
    """Render breakdown + slow clips for each word. Returns {word: [breakdown, slow]}.

    Renders each section directly via the renderer's per-section assembly
    (``_render_section`` → ``_write_audio``), bypassing the full-lesson cue
    manifest — the manifest requires the English title/translation scaffolding we
    dropped, and a preview clip needs no cues, only the audio.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    renderer = _build_renderer()
    voice_id = get_tts_voice(_LANGUAGE_CODE, "female-1")
    ext = CODEC_EXT.get(settings.audio_delivery_codec, "wav")

    results: dict[str, list[Path]] = {}
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        for word in words:
            slug = _slug(word)
            sections = build_preview_sections(word, voice_id)
            out_paths: list[Path] = []
            synth_memo: dict[tuple[str, str, str], tuple[Path, asyncio.Task]] = {}
            memo_lock = asyncio.Lock()
            for idx, (section, suffix) in enumerate(zip(sections, ("breakdown", "slow"), strict=True)):
                audio, _timing = await renderer._render_section(
                    section, tmp, idx, _LANGUAGE_CODE, synth_memo, memo_lock
                )
                path = out_dir / f"{slug}_{suffix}.{ext}"
                await asyncio.to_thread(renderer._write_audio, path, audio)
                out_paths.append(path)
            results[word] = out_paths
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
