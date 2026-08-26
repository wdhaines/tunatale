"""One-shot: bring every stored lesson's KEY_PHRASES section up to today's code.

Per lesson, in this ORDER — and the order is forced, not stylistic:

  1. resync   rebuild the KEY_PHRASES section from lesson.key_phrases, so its
              chunk text is cut at the boundaries today's code produces.
  2. annotate re-tag chunk UPOS. resync builds FRESH Phrase objects, so it wipes
              `upos`; annotating first would throw the tags away. And the render
              reads `upos` to disambiguate (dekket/huset/sporet/vitnet resolve
              only with a tag), so annotating after the render is too late.
  3. render   reassemble_lesson_audio: re-render ONLY the KEY_PHRASES section
              and stream-copy the rest. Non-key-phrase audio is neither
              re-synthesized nor re-encoded.

RESUMABLE. Every step is idempotent and re-derived from the stored lesson, so a
run that dies partway through can simply be run again: lessons already converted
report "already current" and are skipped. That matters because the TTS provider
throttles — a real render has been measured failing 27.3% of clips.

Usage:
    uv run python scripts/regen_key_phrases.py --dry-run
    uv run python scripts/regen_key_phrases.py --go
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.audio.pause_calculator import NaturalPauseCalculator  # noqa: E402
from app.audio.render_service import reassemble_lesson_audio  # noqa: E402
from app.audio.renderer import LessonRenderer  # noqa: E402
from app.audio.slicer import build_slicers  # noqa: E402
from app.audio.tts_factory import get_tts_service  # noqa: E402
from app.config import settings  # noqa: E402
from app.generation.section_builder import build_key_phrases_section  # noqa: E402
from app.languages import get_phoneme_planner, get_preprocessor, resolve_db_path  # noqa: E402
from app.srs.database import SRSDatabase  # noqa: E402
from app.storage.resync_key_phrases import _key_phrases_section, _voices  # noqa: E402
from app.storage.store import ContentStore  # noqa: E402


def _rebuild_section(lesson):
    found = _key_phrases_section(lesson)
    if found is None or not lesson.key_phrases:
        return None
    index, section = found
    voice_map, narrator = _voices(section, lesson.language_code)
    rebuilt = build_key_phrases_section(
        [{"phrase": kp.phrase, "translation": kp.translation} for kp in lesson.key_phrases],
        voice_map,
        narrator,
        lesson.language_code,
    )
    before = [(p.text, p.source_word, p.syllable_span) for p in section.phrases]
    after = [(p.text, p.source_word, p.syllable_span) for p in rebuilt.phrases]
    return index, rebuilt, before != after


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--go", action="store_true")
    ap.add_argument(
        "--force",
        action="store_true",
        help="Re-render even when the chunk text is already current. For repairing a "
        "lesson whose AUDIO is fine but whose stored cue manifest was written by a "
        "buggy version — text_changed cannot see that.",
    )
    ap.add_argument("--db", default=None, help="content DB path (default: the configured Norwegian DB)")
    ap.add_argument("--audio-dir", default=None)
    ap.add_argument("--language", default="no")
    args = ap.parse_args()
    if args.dry_run == args.go:
        ap.error("pass exactly one of --dry-run / --go")

    code = args.language
    # resolve_db_path, not settings.database_urls[code]: the latter KeyErrors on a
    # single-language install, where resolve_language_context falls back to the
    # singular setting. scripts/check_singular_database_url.py names this exact fix.
    db_path = args.db or str(resolve_db_path(code, settings))
    audio_dir = Path(args.audio_dir) if args.audio_dir else settings.audio_dir

    store = ContentStore(db_path)
    srs_db = SRSDatabase(db_path)
    preprocessors = {code: get_preprocessor(code)}
    tts = get_tts_service(cache_dir=settings.tts_cache_dir)
    planner = get_phoneme_planner(code)
    renderer = LessonRenderer(
        tts=tts,
        preprocessors=preprocessors,
        pause_calculator=NaturalPauseCalculator(),
        delivery_codec=settings.audio_delivery_codec,
        delivery_bitrate=settings.audio_delivery_bitrate,
        slicers=build_slicers([code], tts, settings),
        phoneme_planners={code: planner} if planner is not None else {},
    )

    from app.api.generation import annotate_chunk_upos_for_lesson

    converted = skipped = failed = 0
    for lesson_id, _curriculum_id, _day, lesson in list(store.list_lessons()):
        if lesson.language_code != code:
            continue
        rebuilt = _rebuild_section(lesson)
        if rebuilt is None:
            print(f"  --  {lesson_id[:52]:54} no KEY_PHRASES section; skipped")
            skipped += 1
            continue
        index, section, text_changed = rebuilt
        tagged_now = sum(1 for s in lesson.sections for p in s.phrases if p.source_word and p.upos)
        if args.force:
            text_changed = True
        if not text_changed and tagged_now:
            print(f"  ==  {lesson_id[:52]:54} already current; skipped")
            skipped += 1
            continue

        lesson.sections[index] = section
        n_tags = await annotate_chunk_upos_for_lesson(lesson, srs_db)
        action = "resync+tag+render" if text_changed else "tag only"
        if args.dry_run:
            print(f"  ->  {lesson_id[:52]:54} {action:18} tags={n_tags}")
            converted += 1
            continue

        try:
            # AUDIO FIRST, then the lesson text. Neither order is atomic, but
            # this one keeps `text_changed` a truthful resume signal: if the
            # render dies (553 clips against a provider that throttles), the
            # stored text is still the OLD text, so the next run sees the change
            # again and retries. Saving the text first makes a half-converted
            # lesson look "already current" forever — and leaves its captions
            # naming syllables its audio does not say.
            # The render does not need the lesson persisted: it is handed the
            # in-memory one and reads only the audio rows from the store.
            if text_changed:
                await reassemble_lesson_audio(
                    store=store,
                    renderer=renderer,
                    tts=tts,
                    audio_dir=audio_dir,
                    lesson_id=lesson_id,
                    lesson=lesson,
                )
            store.update_lesson_data(lesson_id, lesson)
            print(f"  OK  {lesson_id[:52]:54} {action:18} tags={n_tags}")
            converted += 1
        except Exception as e:  # noqa: BLE001 - one bad lesson must not abort the rest
            print(f"  !!  {lesson_id[:52]:54} FAILED: {type(e).__name__}: {e}")
            failed += 1

    verb = "would convert" if args.dry_run else "converted"
    print(f"\n{verb}={converted}  skipped={skipped}  failed={failed}")
    if failed:
        print("Re-run to retry the failures: every step is idempotent.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
