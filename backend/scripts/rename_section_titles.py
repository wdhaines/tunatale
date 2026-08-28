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

    1. retitle point each renamed section's title phrase at the NEW name. The
               title lives as ordinary Phrase text inside the stored lesson JSON
               (SECTION_TITLES is consumed by the BUILDERS at generation time),
               so without this the re-render re-synthesizes the OLD title and
               the run is a no-op — measured, 2026-08-28.
    2. render  reassemble_lesson_audio(..., section_types=RENAMED) — re-render
               the five renamed sections and stream-copy the rest.
    3. persist store.update_lesson_data, AFTER the render.

RESUMABLE. A lesson is "already current" only when BOTH the stored lesson's title
phrase text AND its stored per-section title cue already spell the NEW title.
Requiring both is what makes the signal honest: the cue alone reported success on
a run that changed nothing. A run that dies
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


def _stale_renamed_sections(store: ContentStore, lesson_id: str, lesson, renamed: list[int]) -> list[int]:
    """Return the renamed section indices that are not yet on the new title.

    TWO signals, and BOTH are required — checking only the cue text is the bug
    that made the 2026-08-28 run a no-op (it reported converted=9, replaced 45
    files, and changed nothing):

      1. the stored LESSON's title phrase text (``sections[i].phrases[0].text``),
         which is what the renderer actually speaks; and
      2. the stored per-section cues_json title cue at phrase_index 0.

    A section is current only when both already spell the NEW title. Missing
    cues_json counts as stale — re-render.
    """
    rows = {
        r["section_index"]: r for r in store.list_audio_files_for_lesson(lesson_id) if r["section_index"] is not None
    }
    stale: list[int] = []
    for i in renamed:
        row = rows.get(i)
        section = lesson.sections[i]
        phrase_current = bool(section.phrases) and section.phrases[0].text == SECTION_TITLES[section.section_type]
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
        if not (current and phrase_current):
            stale.append(i)
    return stale


def _retitle(lesson, renamed: list[int]) -> None:
    """Point each renamed section's title phrase at the NEW name, in memory.

    ⚠️ THIS IS THE STEP THE FIRST VERSION MISSED. SECTION_TITLES is consumed by
    the section BUILDERS at generation time; the title then lives on as ordinary
    Phrase text inside the stored lesson JSON. reassemble_lesson_audio renders
    the Phrase objects it is handed, so without this the re-render faithfully
    re-synthesizes the OLD title and the whole backfill is a no-op.
    """
    for i in renamed:
        section = lesson.sections[i]
        if section.phrases:
            section.phrases[0].text = SECTION_TITLES[section.section_type]


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
        if not args.force and not _stale_renamed_sections(store, lesson_id, lesson, renamed):
            print(f"  ==  {lesson_id[:52]:54} already current; skipped")
            skipped += 1
            continue
        action = f"re-render {len(renamed)} renamed section(s)"
        if args.dry_run:
            print(f"  ->  {lesson_id[:52]:54} {action}")
            converted += 1
            continue
        try:
            # Retitle IN MEMORY first: the render speaks the Phrase objects it is
            # handed, so this is what makes the new name reach the audio at all.
            _retitle(lesson, renamed)
            # AUDIO FIRST, then the lesson text — the same ordering, and the same
            # reason, as scripts/regen_key_phrases.py. The render is handed the
            # in-memory lesson and reads only the audio rows, so it needs no
            # persist; and if it dies (the TTS provider throttles — this run hit
            # HTTP 429 repeatedly) the stored lesson still holds the OLD title,
            # so the next run sees the section stale and retries. Persisting
            # first would make a half-converted lesson look current forever,
            # leaving its transcript naming a title its audio never says.
            await reassemble_lesson_audio(
                store=store,
                renderer=renderer,
                tts=tts,
                audio_dir=audio_dir,
                lesson_id=lesson_id,
                lesson=lesson,
                section_types=RENAMED,
            )
            store.update_lesson_data(lesson_id, lesson)
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
