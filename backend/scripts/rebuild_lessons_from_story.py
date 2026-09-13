"""One-shot: rebuild each stored lesson from its OWN stored Story JSON.

A lesson blob pins a resolved ``voice_id`` per phrase, so a change to a
language's ``tts_voice_map`` reaches new lessons only. When ``tunatale-rag.4``
un-collapsed ``male-2`` (and ``female-2`` before it), the lessons already on disk
kept speaking the old voice — two characters in one voice — and no amount of
re-rendering fixes that, because the render faithfully speaks the ``voice_id`` it
is handed.

This walks the LATEST lesson per day, rebuilds it from the Story JSON stored in
its own ``generation_metadata.story`` (no LLM call, no network beyond TTS), and
re-renders it. The rebuild runs today's ``build_lesson_from_story``, which
re-resolves every ``speaker`` through the CURRENT voice map.

MEASURED ON THE REAL NORWEGIAN CORPUS, 2026-09-12, before any of it ran
(9 lessons, latest per day):

    section types + phrase counts   IDENTICAL on all 9 — a rebuild is not a
                                    re-generation; the lesson keeps its shape
    voice_id                        714 phrases change
                                      male-2   Finn     -> William   (300)
                                      female-2 Pernille -> Iselin    (414)
    text                             63 phrases change, all of them FIXES of
                                    spurious mid-word splits the generator used
                                    to emit: 'selv, følgelig.' -> 'selvfølgelig.',
                                    'for, står' -> 'forstår', 'på gul, vet' ->
                                    'på gulvet'
    upos                            447 phrases gain/alter an annotation
    Azure renders                   380 cache misses, 18,159 chars (~$0.27);
                                    1,283 of 1,663 clips are already cached

⚠️ female-2 is the lesson this script IS. That slot was un-collapsed to Iselin
weeks earlier and the change silently never reached a single stored lesson, which
is why 414 phrases of it are still Pernille. A voice-map edit without a rebuild
is half a change.

IDEMPOTENT, and the signal is honest: a lesson is "already current" only when the
rebuilt blob is BYTE-IDENTICAL to the stored one. That is a usable check because
the rebuild is deterministic — verified by building all 9 lessons twice and
comparing (the lemmatizer runs during the rebuild, so this was not a given, and
``review_request`` reads stored curriculum metadata rather than live SRS state,
so it does not drift either).

RESUMABLE. Audio is rendered BEFORE the blob is persisted, the same ordering and
the same reason as ``scripts/rename_section_titles.py``: if the render dies —
and it will, the provider throttles, a real render has been measured failing
27.3% of clips — the stored lesson still holds the OLD voices, so the next run
still sees it as stale and retries. Persisting first would leave a lesson whose
transcript claims a voice its audio never speaks, looking current forever.

A lesson with no stored Story JSON is SKIPPED, loudly, never reconstructed.
``lesson_io._reconstruct_story`` exists for export and is lossy; guessing the
source of real content in order to overwrite that content is not a trade this
script is allowed to make.

Usage:
    uv run python scripts/rebuild_lessons_from_story.py --dry-run
    uv run python scripts/rebuild_lessons_from_story.py --go
    uv run python scripts/rebuild_lessons_from_story.py --go --day 8   # one lesson
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
from app.generation.story import build_lesson_from_story  # noqa: E402
from app.languages import (  # noqa: E402
    get_language,
    get_phoneme_planner,
    get_preprocessor,
    get_tts_locale,
    resolve_db_path,
)
from app.models.lesson import Lesson  # noqa: E402
from app.storage.store import ContentStore  # noqa: E402


def rebuild(store: ContentStore, lesson: Lesson, day: int, curriculum_id: str, language) -> Lesson | None:
    """Rebuild *lesson* from its stored Story JSON, or ``None`` if it has none.

    ``review_words`` comes from the curriculum's recorded request for that day —
    what the exported prompt actually asked for — not a fresh selection, so the
    rebuild cannot drift as SRS state moves under it. Every measured lesson has
    an empty request, which is "unmeasurable", not "nothing requested".
    """
    story = lesson.generation_metadata.get("story")
    if not story:
        return None
    curriculum = store.get_curriculum(curriculum_id)
    review_words = curriculum.review_request(day) if curriculum is not None else ()
    return build_lesson_from_story(story, language=language, review_words=review_words)


def _latest_lessons(store: ContentStore) -> list[tuple[str, str, int, Lesson]]:
    """The latest lesson per (curriculum, day) — exactly what the app serves.

    ``list_lessons`` returns superseded versions too. Rebuilding one of those
    would spend renders on a blob no reader ever opens, and (worse) ``day 8``
    in the real corpus has two lessons, so walking everything would rewrite a
    lesson the UI has already replaced.
    """
    out: list[tuple[str, str, int, Lesson]] = []
    for curriculum in store.list_curricula():
        for entry in store.get_lesson_days(curriculum["id"]):
            row = store.get_lesson_row(entry["lesson_id"])
            if row is None:  # pragma: no cover - get_lesson_days just named it
                continue
            out.append((row["id"], row["curriculum_id"], row["day"], Lesson.from_json(row["data_json"])))
    return out


def describe(stored: Lesson, rebuilt: Lesson) -> str:
    """A one-line summary of what the rebuild would change, for the plan output.

    Counts PHRASES, not sections: the number a reader wants is "how many lines
    change voice", and a section-level count hides that a section of 121 phrases
    changed 12 of them.
    """
    if [(s.section_type, len(s.phrases)) for s in stored.sections] != [
        (s.section_type, len(s.phrases)) for s in rebuilt.sections
    ]:
        return "STRUCTURE DIFFERS — not a voice swap; inspect before rebuilding"
    voices = texts = 0
    for ss, rs in zip(stored.sections, rebuilt.sections, strict=True):
        for sp, rp in zip(ss.phrases, rs.phrases, strict=True):
            voices += sp.voice_id != rp.voice_id
            texts += sp.text != rp.text
    return f"{voices} voice, {texts} text"


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--go", action="store_true")
    ap.add_argument("--day", type=int, action="append", help="only these days (repeatable)")
    ap.add_argument("--db", default=None, help="content DB path (default: the configured DB for --language)")
    ap.add_argument("--audio-dir", default=None)
    ap.add_argument("--language", default=None, help="default: settings.target_language")
    args = ap.parse_args()
    if args.dry_run == args.go:
        ap.error("pass exactly one of --dry-run / --go")

    code = args.language or settings.target_language
    # resolve_db_path, not settings.database_urls[code]: the latter KeyErrors on a
    # single-language install, where resolve_language_context falls back to the
    # singular setting. scripts/check_singular_database_url.py names this fix.
    db_path = args.db or str(resolve_db_path(code, settings))
    audio_dir = Path(args.audio_dir) if args.audio_dir else settings.audio_dir
    language = get_language(code)

    store = ContentStore(db_path)
    tts = get_tts_service(cache_dir=settings.tts_cache_dir)
    planner = get_phoneme_planner(code)
    locale = get_tts_locale(code)
    renderer = LessonRenderer(
        tts=tts,
        preprocessors={code: get_preprocessor(code)},
        pause_calculator=NaturalPauseCalculator(),
        delivery_codec=settings.audio_delivery_codec,
        delivery_bitrate=settings.audio_delivery_bitrate,
        slicers=build_slicers([code], tts, settings),
        phoneme_planners={code: planner} if planner is not None else {},
        # Without this a Multilingual voice is handed the line with no language
        # declared and guesses — measured getting it wrong on a real sentence
        # (tunatale-rag.4). The whole point of the rebuild is to put such a
        # voice into these lessons, so the seam has to be wired HERE too, not
        # only in main.py.
        tts_locales={code: locale} if locale is not None else None,
    )

    rebuilt_count = skipped = failed = 0
    for lesson_id, curriculum_id, day, stored in _latest_lessons(store):
        if stored.language_code != code:
            continue
        if args.day and day not in args.day:
            continue
        label = f"day {day:>2}  {lesson_id[:44]:46}"
        fresh = rebuild(store, stored, day, curriculum_id, language)
        if fresh is None:
            print(f"  --  {label} NO stored Story JSON; skipped (never reconstructed)")
            skipped += 1
            continue
        if fresh.to_json() == stored.to_json():
            print(f"  ==  {label} already current; skipped")
            skipped += 1
            continue
        action = describe(stored, fresh)
        if args.dry_run:
            print(f"  ->  {label} would rebuild: {action}")
            rebuilt_count += 1
            continue
        try:
            # AUDIO FIRST, then the blob. The render is handed the in-memory
            # rebuilt lesson and reads only the audio rows, so it needs no
            # persist; and a run that dies mid-render leaves the stored lesson
            # on the OLD voices, which is what makes the next run retry it.
            await reassemble_lesson_audio(
                store=store,
                renderer=renderer,
                tts=tts,
                audio_dir=audio_dir,
                lesson_id=lesson_id,
                lesson=fresh,
                # Every section, because a voice change lands in every section
                # the speaker appears in and a text fix lands in the breakdowns
                # too. Naming a subset would leave a lesson half in each voice.
                section_types=tuple({s.section_type for s in fresh.sections}),
            )
            store.update_lesson_data(lesson_id, fresh)
            print(f"  OK  {label} rebuilt: {action}")
            rebuilt_count += 1
        except Exception as e:  # noqa: BLE001 - one bad lesson must not abort the rest
            print(f"  !!  {label} FAILED: {type(e).__name__}: {e}")
            failed += 1

    verb = "would rebuild" if args.dry_run else "rebuilt"
    print(f"\n{verb}={rebuilt_count}  skipped={skipped}  failed={failed}")
    if failed:
        print("Re-run to retry the failures: the resume signal is the stored blob itself.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
