"""Render one lesson's KEY_PHRASES section both ways, through the REAL renderer.

The ear check for syllable slicing. Unlike the spikes this borrows nothing: it
builds the same ``LessonRenderer`` production builds, once with slicers and once
without, so what you hear is what a generated lesson will sound like.

    keyphrases_PRODUCTION.<ext>   every chunk synthesized on its own (today)
    keyphrases_SLICED.<ext>       single-word chunks cut from one whole-word render

Both files use the same pauses, taken from the production render, because
slicing must change how a chunk sounds and nothing else.

Requires the optional extra and the opt-in setting::

    cd backend && uv sync --all-groups --extra alignment
    uv run python -m scripts.render_slicing_ab --lesson <id> --db tunatale_no.db

First run downloads ~1.2 GB to ~/.cache/huggingface.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

from app.audio.edge_tts import EdgeTTSService
from app.audio.pause_calculator import NaturalPauseCalculator
from app.audio.renderer import LessonRenderer
from app.audio.slicer import build_slicers, slicing_available
from app.audio.transcode import CODEC_EXT
from app.config import settings
from app.generation.section_builder import build_key_phrases_section
from app.languages import get_language, get_preprocessor
from app.models.lesson import SectionType

_LANGUAGE_CODE = "no"


def load_lesson(db_path: Path, lesson_id: str) -> dict:
    con = sqlite3.connect(str(db_path))
    try:
        row = con.execute("SELECT data_json FROM lessons WHERE id = ?", (lesson_id,)).fetchone()
    finally:
        con.close()
    if row is None:
        raise SystemExit(f"lesson {lesson_id!r} not found in {db_path}")
    return json.loads(row[0])


async def run(db_path: Path, lesson_id: str, out_dir: Path, codec: str) -> int:
    data = load_lesson(db_path, lesson_id)
    key_phrases = data.get("key_phrases", [])
    if not key_phrases:
        raise SystemExit(f"lesson {lesson_id!r} has no key phrases")

    stored = next(
        (s for s in data["sections"] if s["section_type"] == SectionType.KEY_PHRASES.value),
        None,
    )
    if stored is None:
        raise SystemExit(f"lesson {lesson_id!r} has no KEY_PHRASES section")

    language = get_language(_LANGUAGE_CODE)
    section = build_key_phrases_section(
        key_phrases,
        language.tts_voice_map,
        data.get("narrator_voice", ""),
        _LANGUAGE_CODE,
    )

    # Stage 2/3 must be text-identical to what is already stored. Checking it on
    # a REAL lesson (not the 16-phrase test corpus) is the point of doing this
    # against a stored section rather than a synthetic one.
    stored_texts = [p["text"] for p in stored["phrases"]]
    rebuilt_texts = [p.text for p in section.phrases]
    if stored_texts != rebuilt_texts:
        sys.stdout.write("TEXT DIVERGENCE between the stored section and the rebuilt one:\n")
        for i, (a, b) in enumerate(zip(stored_texts, rebuilt_texts, strict=False)):
            if a != b:
                sys.stdout.write(f"  [{i}] stored={a!r}  rebuilt={b!r}\n")
        raise SystemExit("refusing to render an A/B whose two sides are not the same words")

    sliceable = [p for p in section.phrases if p.syllable_span is not None]
    if not slicing_available(settings):
        raise SystemExit(
            "slicing is not available: set audio_slicing_enabled=true and install "
            "the extra (uv sync --all-groups --extra alignment)"
        )

    tts = EdgeTTSService(cache_dir=out_dir / ".tts_cache")
    preprocessors = {_LANGUAGE_CODE: get_preprocessor(_LANGUAGE_CODE)}
    ext = CODEC_EXT.get(codec, codec)
    out_dir.mkdir(parents=True, exist_ok=True)

    sys.stdout.write(
        f"{len(section.phrases)} phrases; {len(sliceable)} carry a syllable span\nloading aligner on first chunk ...\n"
    )
    sys.stdout.flush()

    results: dict[str, Path] = {}
    for label, slicers in (
        ("PRODUCTION", {}),
        ("SLICED", build_slicers([_LANGUAGE_CODE], tts, settings)),
    ):
        renderer = LessonRenderer(
            tts=tts,
            preprocessors=preprocessors,
            pause_calculator=NaturalPauseCalculator(),
            delivery_codec=codec,
            delivery_bitrate=settings.audio_delivery_bitrate,
            slicers=slicers,
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            audio, _cues = await renderer._render_section(
                section,
                Path(tmp_dir),
                0,
                _LANGUAGE_CODE,
                {},
                asyncio.Lock(),
            )
            path = out_dir / f"keyphrases_{label}.{ext}"
            renderer._write_audio(path, audio)
        results[label] = path
        sys.stdout.write(f"  {label:11s} {audio.duration_ms / 1000:6.1f} s  -> {path}\n")
        sys.stdout.flush()

    prod_s = results["PRODUCTION"].stat().st_size
    sliced_s = results["SLICED"].stat().st_size
    sys.stdout.write(f"\nbytes: production={prod_s} sliced={sliced_s}\nlisten to both and compare.\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lesson", default="day-5-the-trail-ends-in-the-garden-69713a61")
    ap.add_argument("--db", type=Path, default=Path("tunatale_no.db"))
    ap.add_argument("--out", type=Path, default=Path.home() / "Desktop" / "tunatale-slicing-stage3")
    ap.add_argument("--codec", default="mp3", choices=sorted(CODEC_EXT))
    args = ap.parse_args(argv)
    return asyncio.run(run(args.db, args.lesson, args.out, args.codec))


if __name__ == "__main__":
    raise SystemExit(main())
