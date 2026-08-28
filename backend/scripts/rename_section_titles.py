"""One-shot: bring every stored lesson's SECTION TITLES up to today's names.

tunatale-v3ri renamed the human-facing section titles because the "Slow" pass is
enunciated speech, not slower speech, and the English gloss sits after/before the
L2 line:

    SLOW_SPEED          'Slow Speed'              -> 'Enunciated'
    TRANSLATED          'Translated'              -> 'English After'
    EN_TRANSLATED       'English Translated'      -> 'English Before'
    SLOW_TRANSLATED     'Slow Translated'         -> 'Enunciated, English After'
    SLOW_EN_TRANSLATED  'Slow English Translated' -> 'Enunciated, English Before'

The SectionType ENUM VALUES are untouched ("slow_speed", "translated", ...) —
they are persisted in audio_files.section_type and stored lesson JSON — so this
needs no schema migration, no resync and no UPOS re-annotation. Rendering the
renamed sections re-synthesizes ONLY their audio (every other section is
stream-copied, not re-encoded):

    1. render  reassemble_lesson_audio(..., section_types=RENAMED) — re-render
               the five renamed sections and stream-copy the rest.

RESUMABLE. The stored per-section cues_json carries the title cue's text, so a
lesson is "already current" when every renamed section's stored title cue already
spells the NEW title — the render re-writes that cue text. A run that dies
partway through can simply be run again (the TTS provider throttles; a real
render has been measured failing 27.3% of clips).

Usage:
    uv run python scripts/rename_section_titles.py --dry-run
    uv run python scripts/rename_section_titles.py --go
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.audio.pause_calculator import NaturalPauseCalculator  # noqa: E402
from app.audio.render_service import reassemble_lesson_audio  # noqa: E402
from app.audio.renderer import LessonRenderer  # noqa: E402
from app.audio.slicer import build_slicers  # noqa: E402
from app.audio.tts_factory import get_tts_service  # noqa: E402
from app.config import settings  # noqa: E402
from app.generation.section_builder import SECTION_TITLES  # noqa: E402
from app.languages import get_phoneme_planner, get_preprocessor, resolve_db_path  # noqa: E402
from app.models.lesson import SectionType  # noqa: E402
from app.storage.store import ContentStore  # noqa: E402

# The five renamed section types. KEY_PHRASES / NATURAL_SPEED are excluded —
# their titles did not move.
RENAMED = (
    SectionType.SLOW_SPEED,
    SectionType.TRANSLATED,
    SectionType.SLOW_TRANSLATED,
    SectionType.EN_TRANSLATED,
    SectionType.SLOW_EN_TRANSLATED,
)


def _stale_renamed_sections(store: ContentStore, lesson_id: str, renamed: list[int]) -> list[int]:
    """Return the renamed section indices whose stored title cue text is stale.

    The resume signal is the stored per-section cues_json: the title phrase sits
    at phrase_index 0, so a section is current iff its title cue text already
    equals the NEW title. Missing cues_json counts as stale — re-render.
    """
    rows = {
        r["section_index"]: r for r in store.list_audio_files_for_lesson(lesson_id) if r["section_index"] is not None
    }
    stale: list[int] = []
    for i in renamed:
        row = rows.get(i)
        current = False
        if row is not None and row.get("cues_json"):
            try:
                title_cue = next(
                    (c for c in json.loads(row["cues_json"]) if c.get("phrase_index") == 0),
                    None,
                )
                current = (
                    title_cue is not None and title_cue.get("text") == SECTION_TITLES[SectionType(row["section_type"])]
                )
            except TypeError, ValueError, KeyError:
                current = False
        if not current:
            stale.append(i)
    return stale


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--go", action="store_true")
    ap.add_argument(
        "--force",
        action="store_true",
        help="Re-render all renamed sections regardless of the stored title cue "
        "text. For repairing lessons whose audio is stale but whose cues were "
        "written by a buggy version.",
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
    tts = get_tts_service(cache_dir=settings.tts_cache_dir)
    preprocessors = {code: get_preprocessor(code)}
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

    converted = skipped = failed = 0
    for lesson_id, _curriculum_id, _day, lesson in list(store.list_lessons()):
        if lesson.language_code != code:
            continue
        renamed = [i for i, sec in enumerate(lesson.sections) if sec.section_type in RENAMED]
        if not renamed:
            print(f"  --  {lesson_id[:52]:54} no renamed sections; skipped")
            skipped += 1
            continue
        if not args.force and not _stale_renamed_sections(store, lesson_id, renamed):
            print(f"  ==  {lesson_id[:52]:54} already current; skipped")
            skipped += 1
            continue
        action = f"re-render {len(renamed)} renamed section(s)"
        if args.dry_run:
            print(f"  ->  {lesson_id[:52]:54} {action}")
            converted += 1
            continue
        try:
            # AUDIO FIRST. The render does not need the lesson persisted: it is
            # handed the in-memory one and reads only the audio rows. The stored
            # cues are rewritten by reassemble itself, so the title-cue resume
            # signal comes true only once the audio actually changed.
            await reassemble_lesson_audio(
                store=store,
                renderer=renderer,
                tts=tts,
                audio_dir=audio_dir,
                lesson_id=lesson_id,
                lesson=lesson,
                section_types=RENAMED,
            )
            print(f"  OK  {lesson_id[:52]:54} {action}")
            converted += 1
        except Exception as e:  # noqa: BLE001 - one bad lesson must not abort the rest
            print(f"  !!  {lesson_id[:52]:54} FAILED: {type(e).__name__}: {e}")
            failed += 1

    verb = "would convert" if args.dry_run else "converted"
    print(f"\n{verb}={converted}  skipped={skipped}  failed={failed}")
    if failed:
        print("Re-run to retry the failures: the resume signal is the stored title cue text.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
