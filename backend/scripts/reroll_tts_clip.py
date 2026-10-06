#!/usr/bin/env python3
"""Re-roll ONE cached Gemini TTS clip, without touching the rest of the cache.

Gemini TTS is NONDETERMINISTIC, and the TTS cache is keyed on (model, voice,
rate, text, ipa, PROMPT_VERSION). So one unlucky take is served forever: a
phrase rendered in a voice that came out English-sounding is still that file a
week later, and no amount of re-rendering the lesson will replace it. The only
lever available today is ``PROMPT_VERSION``, which invalidates EVERY cached
Gemini clip at once — for a learner with a full curriculum that is hundreds of
requests to replace one.

This script is the narrow lever. It resolves ONE clip of ONE lesson to the file
the adapter itself would write, moves that file out of the cache into a sibling
directory, and logs the eviction. The next render draws a fresh take for that
key and hits every other one.

    uv run python scripts/reroll_tts_clip.py --language ceb --lesson <id> --text 'hi'
    uv run python scripts/reroll_tts_clip.py --language ceb --lesson <id> --text 'hi' --apply
    uv run python scripts/reroll_tts_clip.py --language ceb --lesson <id> --key 947d0179a7af1158

The default is a DRY RUN: the plan is printed and nothing is moved.

Three decisions are load-bearing, and each is a refusal the script makes loudly
rather than a policy the user has to know:

- **Gemini only.** Azure synthesis is deterministic, so a re-roll there would
  send a second request for byte-identical audio and bill it again. A selection
  that resolves to Azure keys is refused, not performed.
- **The digest is never recomputed.** Every path here comes from
  ``report_render_cost.gemini_cache_path`` — the adapter's own ``resolve_ipa`` +
  ``_cache_path`` pair. A hand-rolled digest is a silent miss, and a silent miss
  in a re-roll evicts a clip from nowhere while the bad one stays.
- **Evicted, never deleted.** The file is MOVED to ``tts-cache-rerolled/`` and
  the ``mv`` that restores it is printed. That file is the revert path: if the
  new take is worse, one command puts the old audio back and the render is a hit
  again.

There is no take counter in the cache key on purpose. A counter would orphan
every cached clip exactly the way ``PROMPT_VERSION`` does. The log line beside
the file is the record instead, and a second re-roll simply evicts again.

Run it against the LIVE instance's own directories — the live content DB is not
where local settings point, hence ``--db``. Nothing here renders, calls a
vendor, or reads an Anki collection; it moves one file and writes one line.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.audio.gemini_tts import GeminiTTSService, resolve_ipa  # noqa: E402
from app.audio.slicer import PARENT_RATE, alignment_installed  # noqa: E402
from app.config import settings  # noqa: E402
from app.languages import (  # noqa: E402
    get_alignment,
    get_phoneme_planner,
    get_preprocessor,
    get_slow_word,
    get_tts_locale,
    resolve_db_path,
)
from app.storage.store import ContentStore  # noqa: E402
from scripts.report_render_cost import _SynthValue, collect_keys, gemini_cache_path  # noqa: E402

# Where evicted clips go, and where the log that records them sits. A SIBLING of
# the cache dir, not a subdirectory: the cache directory is walked by anything
# that globs *.mp3, and a re-rolled take sitting inside it would be served again
# by a directory walk, which is the one bug this whole script exists to prevent.
REROLLED_DIR_NAME = "tts-cache-rerolled"
LOG_NAME = "rerolls.jsonl"

# How many near-miss texts to offer when a --text matched nothing. Enough to find
# a fragment by, few enough that the message is still readable.
_NEAR_MATCH_LIMIT = 20


def _describe(path: Path, value: _SynthValue) -> str:
    """One key as a block of ``field<TAB>value`` lines — the shape this repo's
    reports already print, so it diffs and greps like the rest."""
    text, voice_id, rate, phonemes, _speak_locale, enunciation = value
    ipa, _phrase = resolve_ipa(text, phonemes, enunciation)
    return "\n".join(
        [
            f"digest\t{path.stem}",
            f"text\t{text}",
            f"voice\t{voice_id}",
            f"rate\t{rate}",
            f"ipa\t{ipa if ipa is not None else '(none)'}",
            f"cached\t{'yes' if path.exists() else 'no'}",
        ]
    )


def main(argv: list[str] | None = None, *, now: Callable[[], datetime] | None = None) -> int:
    """Resolve, refuse, and (with ``--apply``) evict one Gemini clip.

    ``now`` is injectable so a test can pin the eviction stamp; it is the only
    clock in this file. Nothing here calls ``date.today()``: a stamp in a
    FILENAME has to be UTC, and a local-date stamp would collide across a
    timezone change and silently refuse a legitimate re-roll.
    """
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--language", default=None, help="default: settings.target_language")
    parser.add_argument("--lesson", required=True, metavar="ID", help="the stored lesson id to re-roll within")
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument(
        "--text",
        default=None,
        metavar="TEXT",
        help="select keys whose synthesized (preprocessed) text is exactly TEXT",
    )
    selector.add_argument(
        "--key",
        default=None,
        metavar="DIGEST",
        help="select by the 16-hex cache file stem, when --text is ambiguous",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="content DB path (default: resolve_db_path(--language, settings); the live instance's "
        "DBs are not where local settings point)",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="TTS cache dir holding the clip (default: settings.tts_cache_dir)",
    )
    parser.add_argument("--apply", action="store_true", help="move the file (default: dry run)")
    args = parser.parse_args(argv)
    clock: Callable[[], datetime] = now if now is not None else lambda: datetime.now(UTC)

    code = args.language or settings.target_language
    cache_dir = args.cache_dir or settings.tts_cache_dir
    db_path = args.db if args.db is not None else resolve_db_path(code, settings)
    if not Path(db_path).exists():
        # ContentStore CREATES a missing file, so without this a typo'd --db
        # answers "no such lesson" and leaves an empty database at the typo.
        print(f"no such database: {db_path}", file=sys.stderr)
        return 1

    with ContentStore(db_path) as store:
        lesson = store.get_lesson(args.lesson)
    if lesson is None:
        print(f"no such lesson: {args.lesson}", file=sys.stderr)
        return 1

    # The resolvers are exactly report_render_cost.main's, because the whole
    # premise is that the two scripts agree about which clips a lesson renders.
    # If they disagreed, this one would evict a file no render would ever ask for.
    alignment = get_alignment(code)
    keys = collect_keys(
        [lesson],
        language_code=code,
        preprocessor=get_preprocessor(code),
        planner=get_phoneme_planner(code),
        target_locale=get_tts_locale(code),
        syllabify_fn=alignment.syllabify_fn if alignment is not None else None,
        slicer_enabled=alignment_installed() and alignment is not None,
        parent_rate=PARENT_RATE,
        slow_word_fn=get_slow_word(code),
    )
    gemini = GeminiTTSService(cache_dir=cache_dir)
    candidates: list[_SynthValue] = list(keys.gemini_values)

    if args.text is not None:
        selected = [value for value in candidates if value[0] == args.text]
        if not selected:
            if any(value[0] == args.text for value in keys.azure_values):
                print(
                    f"{args.text!r} is an Azure clip in {args.lesson}. Azure is deterministic; a re-roll "
                    f"would re-bill for identical audio. Refusing.",
                    file=sys.stderr,
                )
                return 2
            print(
                f"no Gemini key in {args.lesson} has text {args.text!r} exactly. This is the PREPROCESSED "
                f"text, so quote what the audio says rather than what the phrase stores.",
                file=sys.stderr,
            )
            needle = args.text.casefold()
            near = sorted({value[0] for value in candidates if needle in value[0].casefold()})
            if near:
                print(f"near matches ({len(near)}):", file=sys.stderr)
                for text in near[:_NEAR_MATCH_LIMIT]:
                    print(f"\t{text!r}", file=sys.stderr)
            else:
                print("no near matches", file=sys.stderr)
            return 1
    else:
        selected = [value for value in candidates if gemini_cache_path(gemini, value).stem == args.key]
        if not selected:
            print(
                f"no Gemini key in {args.lesson} has digest {args.key!r}. The digest is the cache FILE's "
                f"16-hex stem, which this lesson's own render cost report prints.",
                file=sys.stderr,
            )
            return 1

    # Two renderer keys can be ONE file: this adapter ignores the speak locale, so
    # a phrase and a narrator line with the same voice and text are one request.
    # Ambiguity is therefore counted in FILES, not in keys.
    by_path: dict[Path, _SynthValue] = {}
    for value in selected:
        by_path.setdefault(gemini_cache_path(gemini, value), value)

    if len(by_path) > 1:
        print(
            f"{len(by_path)} different cached files match in {args.lesson}; re-run with --key DIGEST:",
            file=sys.stderr,
        )
        for path, value in by_path.items():
            print(_describe(path, value), file=sys.stderr)
        return 2

    ((source, value),) = by_path.items()
    if not source.exists():
        print(
            f"{source.stem} ({value[0]!r}) is not in the cache: already a miss: the next render draws a "
            f"fresh take. Nothing to re-roll.",
            file=sys.stderr,
        )
        return 1

    reroll_dir = cache_dir.parent / REROLLED_DIR_NAME
    # ONE read of the clock: the filename stamp and the log's "at" are the same
    # instant, or the record would name a file its own timestamp does not match.
    evicted_at = clock()
    stamp = evicted_at.strftime("%Y%m%dT%H%M%SZ")
    destination = reroll_dir / f"{source.stem}.{stamp}.mp3"

    print(_describe(source, value))
    print(f"source\t{source}")
    print(f"destination\t{destination}")
    print("re-roll cost: 1 Gemini clip")
    if not args.apply:
        print(f"DRY RUN — nothing was moved. Re-run with --apply to evict {source.name}.")
        return 0

    if destination.exists():
        # Two re-rolls in the same second share a destination name, and the file
        # already there is the only copy of the take evicted by the first one.
        # Overwriting it would destroy the revert path for the very clip this
        # script exists to let a learner recover.
        print(f"refusing to overwrite {destination}; its take is the only copy of that audio.", file=sys.stderr)
        return 1

    reroll_dir.mkdir(parents=True, exist_ok=True)
    source.rename(destination)
    record = {
        "at": evicted_at.isoformat(),
        "language": code,
        "lesson_id": args.lesson,
        "digest": source.stem,
        "text": value[0],
        "voice_id": value[1],
        "rate": value[2],
        "ipa": resolve_ipa(value[0], value[3], value[5])[0],
        "evicted_to": str(destination),
    }
    with (reroll_dir / LOG_NAME).open("a", encoding="utf-8") as log:
        log.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(f"rerolled\t{source.name} -> {destination}")
    print(
        f"next: re-render the lesson, POST /api/audio/render for lesson {args.lesson}. "
        f"That draws one fresh take for this key and reuses every other cached clip."
    )
    print(f"revert: mv {destination} {source}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
