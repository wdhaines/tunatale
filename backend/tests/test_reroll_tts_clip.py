"""Acceptance tests for scripts/reroll_tts_clip.py (tunatale-u8nz.18).

The bead's oracle is the whole point of the script: a re-roll of one key must
leave EXACTLY ONE miss in the lesson and every other key a hit. O1 below is that
oracle, measured by ``price_lessons`` — the tracked instrument — rather than by
this script's own output, so a re-roll that claimed success while evicting the
wrong file (or two) fails here.

Nothing patches ``app.*``. Every input is a real ``ContentStore`` on ``tmp_path``
holding synthetic ``Lesson`` objects, the cache is warmed by writing bytes at the
path ``gemini_cache_path`` returns, and ``--db`` / ``--cache-dir`` / ``now`` are
passed explicitly — so the real content DB, the real ``~/.tunatale/tts-cache``
and the wall clock are all out of reach of this suite.

The language is ``ceb`` because the Gemini voice is, and because ``ceb`` has no
``AlignmentConfig``: the slicer leg is then empty regardless of whether the
alignment model is installed, which keeps these tests identical locally and in
CI. That is a real limitation and it is deliberate — a lesson with slicer legs
would need the real Norwegian syllabifier, i.e. a 1.2 GB model download, which
the coverage config forbids outright.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.audio.gemini_tts import GeminiTTSService, resolve_ipa
from app.audio.slicer import PARENT_RATE
from app.languages import get_preprocessor
from app.models.lesson import Lesson, Phrase, Section, SectionType
from app.storage.store import ContentStore
from scripts.report_render_cost import GeminiLegStats, collect_keys, gemini_cache_path, price_lessons
from scripts.reroll_tts_clip import main

KORE = "ceb-PH-KoreGemini"  # the Gemini voice; routed by suffix, not by this test
OTHER_GEMINI = "ceb-PH-CebuanoGemini"  # a SECOND Gemini voice, for the ambiguity case
FINN = "nb-NO-FinnNeural"  # Azure, and deterministic: a re-roll there is refused
LANGUAGE = "ceb"
LOCALE = "ceb-PH"  # get_tts_locale("ceb")
BYTES = b"\x00fake-mp3\x00"  # content is irrelevant; .exists() decides
REROLLED_DIR = "tts-cache-rerolled"
LOG_NAME = "rerolls.jsonl"


# ---------------------------------------------------------------------------
# Fixtures-as-helpers: a real store on tmp_path, a real cache dir, and a
# pinned clock. The clock is the only reason O4 can exist — a stamp is a second
# of wall time, and two re-rolls in the same second are one collision.
# ---------------------------------------------------------------------------

T0 = datetime(2026, 9, 28, 12, 0, 0, tzinfo=UTC)


def _at(*, hours: int = 0) -> Callable[[], datetime]:
    return lambda: T0 + timedelta(hours=hours)


def _phrase(text: str, voice: str = KORE, **kwargs) -> Phrase:
    return Phrase(text, voice, LANGUAGE, **kwargs)


def _lesson(*phrases: Phrase) -> Lesson:
    return Lesson(
        title="A test lesson",
        language_code=LANGUAGE,
        sections=[Section(SectionType.NATURAL_SPEED, list(phrases))],
    )


def _value(text: str, voice: str = KORE, rate: str = "+0%", phonemes=None, speak_locale: str = LOCALE) -> tuple:
    """The value tuple ``collect_keys`` stores for a Cebuano phrase key."""
    return (text, voice, rate, phonemes, speak_locale)


def _seed(lesson: Lesson, db_path: Path, lesson_id: str = "lesson-1") -> None:
    store = ContentStore(db_path)
    try:
        store.save_lesson(lesson_id, "curriculum-1", 1, lesson)
    finally:
        store.close()


def _warm(gemini: GeminiTTSService, value: tuple) -> Path:
    """Create the cache file for *value* at the ADAPTER's own path."""
    path = gemini_cache_path(gemini, value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(BYTES)
    return path


def _mp3s(reroll_dir: Path) -> list[Path]:
    """Evicted mp3s in *reroll_dir*; a dir that was never made counts as empty."""
    return sorted(reroll_dir.glob("*.mp3")) if reroll_dir.is_dir() else []


def _log_lines(reroll_dir: Path) -> list[str]:
    log = reroll_dir / LOG_NAME
    return log.read_text().splitlines() if log.exists() else []


def _args(
    db_path: Path,
    cache_dir: Path,
    *selector: str,
    lesson_id: str = "lesson-1",
    apply: bool = False,
) -> list[str]:
    argv = [
        "--language",
        LANGUAGE,
        "--lesson",
        lesson_id,
        "--db",
        str(db_path),
        "--cache-dir",
        str(cache_dir),
        *selector,
    ]
    if apply:
        argv.append("--apply")
    return argv


def _price(lesson: Lesson, cache_dir: Path):
    """``price_lessons`` on the same resolvers the script wires for ``ceb``.

    Deliberately the REAL preprocessor and a null planner: the script resolves
    the lesson's own language, so an oracle that used a different preprocessor
    could disagree with it and the disagreement would read as a re-roll bug.
    """
    return price_lessons(
        [lesson],
        language_code=LANGUAGE,
        preprocessor=get_preprocessor(LANGUAGE),
        planner=None,
        target_locale=LOCALE,
        syllabify_fn=None,
        slicer_enabled=False,
        parent_rate=PARENT_RATE,
        cache_dir=cache_dir,
    )


# ---------------------------------------------------------------------------
# O1 — the bead's acceptance oracle: exactly one miss, and it is the one key.
# ---------------------------------------------------------------------------

_THREE_TEXTS = ("Maayong buntag", "Kumusta kaayo", "Adlawon ako")


def test_o1_a_reroll_leaves_exactly_one_miss_and_it_is_the_re_rolled_key(tmp_path: Path) -> None:
    db_path = tmp_path / "content.db"
    cache_dir = tmp_path / "tts-cache"
    reroll_dir = tmp_path / REROLLED_DIR
    lesson = _lesson(*(_phrase(text) for text in _THREE_TEXTS))
    _seed(lesson, db_path)
    gemini = GeminiTTSService(cache_dir=cache_dir)
    target, *kept = (_value(text) for text in _THREE_TEXTS)
    for value in (target, *kept):
        _warm(gemini, value)

    assert _price(lesson, cache_dir).gemini_phrase == GeminiLegStats(3, 3, 0, chars=0, audio_tokens=0.0)

    rc = main(_args(db_path, cache_dir, "--text", target[0], "--apply"), now=_at())

    assert rc == 0
    cost = _price(lesson, cache_dir)
    assert cost.gemini_phrase.distinct == 3  # the RENDER still asks for three clips
    assert cost.gemini_phrase.hits == 2  # every other key survives
    assert cost.gemini_phrase.misses == 1  # and only the re-rolled one
    # The miss is the evicted file, named by its own adapter path — not merely
    # "some file": a reroll of the wrong key would also report one miss.
    assert not gemini_cache_path(gemini, target).exists()
    for value in kept:
        assert gemini_cache_path(gemini, value).exists()
    assert len(_mp3s(reroll_dir)) == 1
    # Nothing reached Azure, so the Azure legs stay empty on both sides.
    assert cost.billable_chars == 0


# ---------------------------------------------------------------------------
# O2 — the default touches nothing. A re-roll that fires without --apply would
# re-bill a clip nobody asked to re-roll.
# ---------------------------------------------------------------------------


def test_o2_a_dry_run_moves_nothing_and_writes_no_log(tmp_path: Path, capsys) -> None:
    db_path = tmp_path / "content.db"
    cache_dir = tmp_path / "tts-cache"
    reroll_dir = tmp_path / REROLLED_DIR
    lesson = _lesson(_phrase("Maayong buntag"))
    _seed(lesson, db_path)
    gemini = GeminiTTSService(cache_dir=cache_dir)
    src = _warm(gemini, _value("Maayong buntag"))

    rc = main(_args(db_path, cache_dir, "--text", "Maayong buntag"), now=_at())

    out = capsys.readouterr().out
    assert rc == 0
    assert "DRY RUN" in out
    assert "re-roll cost: 1 Gemini clip" in out
    assert src.exists() and src.read_bytes() == BYTES
    assert _mp3s(reroll_dir) == []
    assert _log_lines(reroll_dir) == []
    # And the report still sees a hit: nothing was evicted.
    assert _price(lesson, cache_dir).gemini_phrase.misses == 0


# ---------------------------------------------------------------------------
# O3 — the evicted file is a MOVE, not a delete: its bytes survive, and the log
# line names a file that exists. This is the revert path.
# ---------------------------------------------------------------------------


def test_o3_the_evicted_bytes_survive_and_the_log_names_an_existing_file(tmp_path: Path) -> None:
    db_path = tmp_path / "content.db"
    cache_dir = tmp_path / "tts-cache"
    reroll_dir = tmp_path / REROLLED_DIR
    lesson = _lesson(_phrase("Maayong buntag"))
    _seed(lesson, db_path)
    gemini = GeminiTTSService(cache_dir=cache_dir)
    src = _warm(gemini, _value("Maayong buntag"))

    assert main(_args(db_path, cache_dir, "--text", "Maayong buntag", "--apply"), now=_at()) == 0

    (evicted,) = _mp3s(reroll_dir)
    assert evicted.read_bytes() == BYTES  # byte-identical: a copy, not a re-render
    assert not src.exists()
    (line,) = _log_lines(reroll_dir)
    record = json.loads(line)
    assert record["evicted_to"] == str(evicted)
    assert Path(record["evicted_to"]).exists()
    assert record["digest"] == src.stem
    assert record["text"] == "Maayong buntag"
    assert record["voice_id"] == KORE
    assert record["rate"] == "+0%"
    assert record["language"] == LANGUAGE
    assert record["lesson_id"] == "lesson-1"
    # The stamp is the injected clock, not the wall clock.
    assert record["at"] == T0.isoformat()
    assert evicted.name == f"{src.stem}.20260928T120000Z.mp3"


def test_the_printed_plan_names_the_reverting_move(tmp_path: Path, capsys) -> None:
    db_path = tmp_path / "content.db"
    cache_dir = tmp_path / "tts-cache"
    lesson = _lesson(_phrase("Maayong buntag"))
    _seed(lesson, db_path)
    gemini = GeminiTTSService(cache_dir=cache_dir)
    src = _warm(gemini, _value("Maayong buntag"))

    assert main(_args(db_path, cache_dir, "--text", "Maayong buntag", "--apply"), now=_at()) == 0

    out = capsys.readouterr().out
    (evicted,) = _mp3s(tmp_path / REROLLED_DIR)
    # Both halves of the next step: re-render to draw a new take, and the exact
    # `mv` that puts the old one back if the new one is worse.
    assert "/api/audio/render" in out and "lesson-1" in out
    assert f"mv {evicted} {src}" in out


# ---------------------------------------------------------------------------
# O4 — a second report rolls again. Evict-and-log has no take counter to trip,
# so re-creating the cache file and re-running must evict a SECOND file, and two
# log lines must accumulate.
# ---------------------------------------------------------------------------


def test_o4_a_second_reroll_at_a_later_stamp_evicts_again_and_appends(tmp_path: Path) -> None:
    db_path = tmp_path / "content.db"
    cache_dir = tmp_path / "tts-cache"
    reroll_dir = tmp_path / REROLLED_DIR
    lesson = _lesson(_phrase("Maayong buntag"))
    _seed(lesson, db_path)
    gemini = GeminiTTSService(cache_dir=cache_dir)
    value = _value("Maayong buntag")
    _warm(gemini, value)

    assert main(_args(db_path, cache_dir, "--text", "Maayong buntag", "--apply"), now=_at()) == 0
    # The render drew a fresh take; the user dislikes it too.
    _warm(gemini, value)
    assert main(_args(db_path, cache_dir, "--text", "Maayong buntag", "--apply"), now=_at(hours=1)) == 0

    first, second = _mp3s(reroll_dir)
    assert first != second
    assert first.stem.split(".")[-1] == "20260928T120000Z"
    assert second.stem.split(".")[-1] == "20260928T130000Z"
    assert len(_log_lines(reroll_dir)) == 2
    assert not gemini_cache_path(gemini, value).exists()


# ---------------------------------------------------------------------------
# O5 — one text, two files. The user re-runs with --key; nothing is moved,
# because picking one of two files on the user's behalf is a coin toss.
# ---------------------------------------------------------------------------


def test_o5_two_files_for_one_text_exit_2_list_both_and_move_nothing(tmp_path: Path, capsys) -> None:
    db_path = tmp_path / "content.db"
    cache_dir = tmp_path / "tts-cache"
    reroll_dir = tmp_path / REROLLED_DIR
    lesson = _lesson(_phrase("Maayong buntag"), _phrase("Maayong buntag", voice=OTHER_GEMINI))
    _seed(lesson, db_path)
    gemini = GeminiTTSService(cache_dir=cache_dir)
    first = _warm(gemini, _value("Maayong buntag"))
    second = _warm(gemini, _value("Maayong buntag", voice=OTHER_GEMINI))

    rc = main(_args(db_path, cache_dir, "--text", "Maayong buntag", "--apply"), now=_at())

    out, err = capsys.readouterr()
    assert rc == 2
    assert "--key" in err  # the user is told how to disambiguate
    for path, voice in ((first, KORE), (second, OTHER_GEMINI)):
        assert path.stem in err
        assert voice in err
        assert path.exists()  # nothing moved
    assert _mp3s(reroll_dir) == []
    assert _log_lines(reroll_dir) == []
    assert out == ""


# ---------------------------------------------------------------------------
# O6 — Azure is deterministic, so a re-roll there re-bills for the same audio.
# ---------------------------------------------------------------------------


def test_o6_an_azure_only_text_is_refused_with_its_reason(tmp_path: Path, capsys) -> None:
    db_path = tmp_path / "content.db"
    cache_dir = tmp_path / "tts-cache"
    reroll_dir = tmp_path / REROLLED_DIR
    lesson = _lesson(_phrase("Maayong buntag"), _phrase("Hei", voice=FINN))
    _seed(lesson, db_path)

    rc = main(_args(db_path, cache_dir, "--text", "Hei", "--apply"), now=_at())

    err = capsys.readouterr().err
    assert rc == 2
    assert "Azure is deterministic" in err
    assert "re-bill for identical audio" in err
    assert _mp3s(reroll_dir) == []
    assert _log_lines(reroll_dir) == []


# ---------------------------------------------------------------------------
# The candidate set the script searches: BOTH Gemini legs, and neither Azure one.
#
# Pinned at the ``collect_keys`` level, not through ``main``, and that is a
# deliberate limit rather than a shortcut: the script derives ``slicer_enabled``
# from the language, and Cebuano has no ``AlignmentConfig`` while
# ``alignment_installed()`` itself is True locally and False in CI (AGENTS.md
# § "Testing Quirks": CI installs lean). A slicer test through ``main`` would
# therefore assert on whichever machine ran it. This asserts the same property in a place that is
# the same everywhere — the two legs are merged, and the Azure legs are absent
# from the Gemini candidate set entirely.
# ---------------------------------------------------------------------------


def _keys_for(lesson: Lesson, *, slicer_enabled: bool, syllabify_fn=None) -> object:
    return collect_keys(
        [lesson],
        language_code=LANGUAGE,
        preprocessor=get_preprocessor(LANGUAGE),
        planner=None,
        target_locale=LOCALE,
        syllabify_fn=syllabify_fn,
        slicer_enabled=slicer_enabled,
        parent_rate=PARENT_RATE,
    )


def test_both_gemini_legs_are_candidates_and_no_azure_leg_is(tmp_path: Path) -> None:
    lesson = _lesson(
        _phrase("buntag", source_word="buntag", syllable_span=(0, 2)),
        _phrase("Hei", voice=FINN, source_word="Hei", syllable_span=(0, 1)),
    )
    gemini = GeminiTTSService(cache_dir=tmp_path / "cache")

    off = _keys_for(lesson, slicer_enabled=False)
    assert off.gemini_values == [_value("buntag")]
    assert off.azure_values == [_value("Hei", voice=FINN)]

    # A two-syllable split, or _build_parent would decline and there is no leg.
    on = _keys_for(lesson, slicer_enabled=True, syllabify_fn=lambda word: ["bun", "tag"])
    # The parent's text is the WHOLE source word, at PARENT_RATE with no phonemes.
    assert on.gemini_values == [_value("buntag"), _value("buntag", rate=PARENT_RATE, phonemes=None)]
    assert gemini_cache_path(gemini, _value("buntag")) != gemini_cache_path(
        gemini, _value("buntag", rate=PARENT_RATE, phonemes=None)
    )  # two legs, two FILES: so --text alone is ambiguous, and --key is not
    assert on.azure_values == [
        _value("Hei", voice=FINN),
        _value("Hei", voice=FINN, rate=PARENT_RATE, phonemes=None),
    ]


def test_two_renderer_keys_can_be_one_file(tmp_path: Path) -> None:
    """The dedupe the script's candidate set must survive: this adapter ignores
    ``speak_locale``, so a Cebuano-tagged phrase and an English narrator line
    with the same voice are ONE request and ONE file. Counting them as two
    candidates would report an ambiguity where there is none."""
    lesson = _lesson(_phrase("Maayong buntag"), Phrase("Maayong buntag", KORE, "en"))
    keys = _keys_for(lesson, slicer_enabled=False)
    gemini = GeminiTTSService(cache_dir=tmp_path / "cache")

    assert len(keys.gemini_values) == 2
    paths = {gemini_cache_path(gemini, value) for value in keys.gemini_values}
    assert len(paths) == 1


# ---------------------------------------------------------------------------
# O7 — the refactor guard: the re-roll script and the cost report must agree on
# which file a key IS, to the byte. A hand-rolled digest in either is a silent
# miss, and a silent miss in a re-roll evicts a clip from nowhere.
# ---------------------------------------------------------------------------


def test_o7_gemini_cache_path_agrees_with_the_adapter_itself(tmp_path: Path) -> None:
    gemini = GeminiTTSService(cache_dir=tmp_path / "cache")

    plain = _value("hi")
    prompted = ("Maayong buntag", KORE, "+0%", {"maayong": "ˈmaʔa", "buntag": "ˈbuntag"}, LOCALE)

    for text, voice, rate, phonemes, _locale in (plain, prompted):
        value = (text, voice, rate, phonemes, _locale)
        ipa, _phrase_flag = resolve_ipa(text, phonemes)
        assert gemini_cache_path(gemini, value) == GeminiTTSService(cache_dir=tmp_path / "cache")._cache_path(
            text, voice, rate, ipa
        )

    # The prompted case must actually be PROMPTED, or this guard would pass on a
    # gemini_cache_path that dropped resolve_ipa entirely.
    assert resolve_ipa("Maayong buntag", {"maayong": "ˈmaʔa", "buntag": "ˈbuntag"})[0] == "ˈmaʔa ˈbuntag"
    assert gemini_cache_path(gemini, prompted) != gemini_cache_path(
        gemini, ("Maayong buntag", KORE, "+0%", None, LOCALE)
    )


# ---------------------------------------------------------------------------
# The remaining refusals, each LOUD: a non-zero exit and a message on stderr.
# A quiet 0 for any of these is the failure mode — the user would believe a
# clip was re-rolled.
# ---------------------------------------------------------------------------


def test_an_unknown_lesson_exits_1(tmp_path: Path, capsys) -> None:
    db_path = tmp_path / "content.db"
    _seed(_lesson(_phrase("Maayong buntag")), db_path)

    rc = main(_args(db_path, tmp_path / "tts-cache", "--text", "Maayong buntag", lesson_id="nope"), now=_at())

    captured = capsys.readouterr()
    assert rc == 1
    assert "no such lesson: nope" in captured.err
    assert captured.out == ""


def test_text_matching_nothing_exits_1_and_lists_near_matches(tmp_path: Path, capsys) -> None:
    db_path = tmp_path / "content.db"
    cache_dir = tmp_path / "tts-cache"
    lesson = _lesson(_phrase("Maayong buntag"), _phrase("Kumusta kaayo"))
    _seed(lesson, db_path)

    rc = main(_args(db_path, cache_dir, "--text", "buntag"), now=_at())

    err = capsys.readouterr().err
    assert rc == 1
    assert "Maayong buntag" in err  # a case-insensitive CONTAINING match
    assert "Kumusta kaayo" not in err


def test_text_matching_nothing_at_all_says_so(tmp_path: Path, capsys) -> None:
    db_path = tmp_path / "content.db"
    cache_dir = tmp_path / "tts-cache"
    _seed(_lesson(_phrase("Maayong buntag")), db_path)

    rc = main(_args(db_path, cache_dir, "--text", "zzzz"), now=_at())

    err = capsys.readouterr().err
    assert rc == 1
    assert "no near matches" in err


def test_a_digest_outside_the_lesson_exits_1(tmp_path: Path, capsys) -> None:
    db_path = tmp_path / "content.db"
    cache_dir = tmp_path / "tts-cache"
    lesson = _lesson(_phrase("Maayong buntag"))
    _seed(lesson, db_path)
    gemini = GeminiTTSService(cache_dir=cache_dir)
    # A REAL digest — of this lesson's own text — but at the wrong rate, so it is
    # a real key-shaped thing that is not in scope. "0" is not tested on purpose:
    # it must not be a place a well-formed-but-wrong id slips through as a hit.
    other = gemini_cache_path(gemini, _value("Maayong buntag", rate="-20%")).stem

    rc = main(_args(db_path, cache_dir, "--key", other), now=_at())

    err = capsys.readouterr().err
    assert rc == 1
    assert other in err


def test_a_key_already_missing_exits_1_rather_than_rerolling(tmp_path: Path, capsys) -> None:
    """A cache miss is ALREADY a re-roll: the next render draws a fresh take. So
    this must say so, and must not move a file that was never there."""
    db_path = tmp_path / "content.db"
    cache_dir = tmp_path / "tts-cache"
    _seed(_lesson(_phrase("Maayong buntag")), db_path)
    gemini = GeminiTTSService(cache_dir=cache_dir)
    value = _value("Maayong buntag")
    assert not gemini_cache_path(gemini, value).exists()

    rc = main(_args(db_path, cache_dir, "--text", "Maayong buntag", "--apply"), now=_at())

    err = capsys.readouterr().err
    assert rc == 1
    assert "already a miss" in err
    assert "the next render draws a fresh take" in err
    assert _mp3s(tmp_path / REROLLED_DIR) == []
    assert _log_lines(tmp_path / REROLLED_DIR) == []


def test_an_existing_destination_is_never_overwritten(tmp_path: Path, capsys) -> None:
    """The same second twice is one destination name, and the first re-roll's
    file is still the only copy of that take. Overwriting it would destroy the
    only revert path for the key the script is being asked to protect."""
    db_path = tmp_path / "content.db"
    cache_dir = tmp_path / "tts-cache"
    reroll_dir = tmp_path / REROLLED_DIR
    _seed(_lesson(_phrase("Maayong buntag")), db_path)
    gemini = GeminiTTSService(cache_dir=cache_dir)
    value = _value("Maayong buntag")
    _warm(gemini, value)

    assert main(_args(db_path, cache_dir, "--text", "Maayong buntag", "--apply"), now=_at()) == 0
    (first,) = _mp3s(reroll_dir)
    _warm(gemini, value)  # the render drew a new take at the same wall second

    rc = main(_args(db_path, cache_dir, "--text", "Maayong buntag", "--apply"), now=_at())

    err = capsys.readouterr().err
    assert rc == 1
    assert "refusing to overwrite" in err
    assert _mp3s(reroll_dir) == [first]
    assert first.read_bytes() == BYTES
    assert len(_log_lines(reroll_dir)) == 1
    assert gemini_cache_path(gemini, value).exists()  # the second take was left alone


# ---------------------------------------------------------------------------
# --key as the disambiguator: the selector O5 tells the user to reach for.
# ---------------------------------------------------------------------------


def test_a_digest_selects_one_of_two_files_for_the_same_text(tmp_path: Path, capsys) -> None:
    db_path = tmp_path / "content.db"
    cache_dir = tmp_path / "tts-cache"
    lesson = _lesson(_phrase("Maayong buntag"), _phrase("Maayong buntag", voice=OTHER_GEMINI))
    _seed(lesson, db_path)
    gemini = GeminiTTSService(cache_dir=cache_dir)
    chosen = _warm(gemini, _value("Maayong buntag", voice=OTHER_GEMINI))
    other = _warm(gemini, _value("Maayong buntag"))

    rc = main(_args(db_path, cache_dir, "--key", chosen.stem, "--apply"), now=_at())

    out = capsys.readouterr().out
    assert rc == 0
    assert other.exists()  # the sibling voice's file is untouched
    assert not chosen.exists()
    (evicted,) = _mp3s(tmp_path / REROLLED_DIR)
    assert evicted.stem.startswith(chosen.stem)
    assert OTHER_GEMINI in out  # the plan says WHICH file it is rolling


def test_a_missing_db_exits_1_and_creates_nothing(tmp_path: Path, capsys) -> None:
    """A mistyped --db must not leave an empty database behind it.

    Opening a ContentStore on a missing path CREATES one, so without a check the
    typo was answered with "no such lesson" and a new empty file at the typo'd
    path — a clean negative that reads as "the lesson is not in this DB".
    """
    db_path = tmp_path / "typo.db"

    rc = main(_args(db_path, tmp_path / "tts-cache", "--text", "Maayong buntag"), now=_at())

    assert rc == 1
    assert "no such database" in capsys.readouterr().err
    assert not db_path.exists()


def test_the_filename_stamp_and_the_log_time_are_one_instant(tmp_path: Path) -> None:
    """A clock that ticks between reads must not split the record in two."""
    db_path = tmp_path / "content.db"
    cache_dir = tmp_path / "tts-cache"
    _seed(_lesson(_phrase("Maayong buntag")), db_path)
    _warm(GeminiTTSService(cache_dir=cache_dir), _value("Maayong buntag"))
    ticks = iter([T0, T0 + timedelta(seconds=1), T0 + timedelta(seconds=2)])

    assert main(_args(db_path, cache_dir, "--text", "Maayong buntag", "--apply"), now=lambda: next(ticks)) == 0

    (evicted,) = _mp3s(tmp_path / REROLLED_DIR)
    record = json.loads(_log_lines(tmp_path / REROLLED_DIR)[0])
    assert evicted.name.endswith(".20260928T120000Z.mp3")
    assert record["at"] == T0.isoformat()
