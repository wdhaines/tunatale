"""STAGE 2 acceptance tests for scripts/report_render_cost.py (brief-fjfq).

Every rule in the brief's STAGE 0 is covered by at least one test. The number
literals are MEASURED, not derived: the Tier A table is the brief's own oracle
(re-verified live 2026-09-15 against ``AzureTTSService._billable_body``), and
the Rule 1/2/3/5 totals and the cold/warm cache pair were verified against the
same functions on 2026-09-15 before being pinned. A test that restates a
condition instead of calling the function that owns it is the exact failure the
brief warns about — everything here drives ``price_lessons`` or the functions it
wires, with stubs passed as arguments (never ``patch("app.…")``).

The two aggregate wrong-answer backstops from the brief, called out by name:

- Rule 2's "skipped the phoneme leg" cannot be caught by miss count — on the
  real lesson the count stays at 265 while only characters move. The synthetic
  aggregate below reproduces that shape: misses stay 2 either way, billable
  moves 66 -> 111.
- Rule 3's "never passed speak_locale" is the quietest wrong answer (only 360
  low on the real lesson). The 96-vs-66-vs-126 discriminator below pins it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.audio.azure_tts import AzureTTSService
from app.audio.slicer import PARENT_RATE
from app.config import settings
from app.languages import resolve_db_path
from app.models.lesson import Lesson, Phrase, Section, SectionType
from app.storage.store import ContentStore
from scripts.report_render_cost import (
    LegStats,
    RenderCost,
    _memo_key,
    _phrase_phonemes,
    _print_report,
    main,
    price_lessons,
)

FINN = "nb-NO-FinnNeural"  # the language's native voice; nb-NO is its own locale
EMMA = "en-US-EmmaMultilingualNeural"  # a Multilingual voice: speak_locale matters here
TARGET_LOCALE = "nb-NO"  # get_tts_locale("no"), verified live 2026-09-15


# ---------------------------------------------------------------------------
# Tier A — the brief's oracle table, pinned: len(_billable_body(...)) is the
# billable unit. len(text) is not: the same 3-char word ranges 33..108.
# ---------------------------------------------------------------------------

_BILLABLE_BODY_ORACLE = [
    ("Hei", FINN, "+0%", None, "nb-NO", 33),
    ("Hei", FINN, "+0%", None, None, 33),  # a native voice emits no <lang> for its own locale
    ("Hei", EMMA, "+0%", None, "nb-NO", 63),
    ("Hello", EMMA, "+0%", None, "en-US", 35),
    ("Hei", FINN, "-25%", None, "nb-NO", 34),
    ("ja & nei", FINN, "+0%", None, "nb-NO", 42),
    ("sno", FINN, "+0%", {"sno": "ˈsnuː"}, "nb-NO", 78),
    ("sno", EMMA, "+0%", {"sno": "ˈsnuː"}, "nb-NO", 108),
]


@pytest.mark.parametrize(
    ("text", "voice_id", "rate", "phonemes", "speak_locale", "expected"),
    _BILLABLE_BODY_ORACLE,
)
def test_tier_a_billable_body_oracle(text, voice_id, rate, phonemes, speak_locale, expected) -> None:
    assert len(AzureTTSService._billable_body(text, voice_id, rate, phonemes, speak_locale)) == expected


# ---------------------------------------------------------------------------
# Stubs — passed as ARGUMENTS, per the brief's mock-boundary rule. Nothing here
# patches app.*.
# ---------------------------------------------------------------------------


class PassThroughPreprocessor:
    """``preprocess(text, section_type) -> text`` — the no-op baseline."""

    def preprocess(self, text: str, section_type: SectionType) -> str:
        return text


class AlterPreprocessor:
    """Rule 1's discriminator: a preprocessor that PROVABLY changes the text.

    A real-data fixture cannot tell a script that calls ``preprocess`` from one
    that does not — the Norwegian preprocessor changes 0 of 5,955 stored phrases
    (measured 2026-09-15) — so the stub must alter text, and the assertion must
    check the billable figure of the ALTERED text.
    """

    def preprocess(self, text: str, section_type: SectionType) -> str:
        return "a & b"


class RecordingPlanner:
    """A phoneme planner that returns a fixed mapping and records its calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[int, int]]] = []

    def plan_chunk(self, source_word, syllable_span, upos=None, chunk_text=None):
        self.calls.append((source_word, syllable_span))
        return "ˈsnuː"


class NullPlanner:
    """A phoneme planner whose plan_chunk returns None (word not in lexicon)."""

    def plan_chunk(self, source_word, syllable_span, upos=None, chunk_text=None):
        return None


def _phrase(text: str, **kwargs) -> Phrase:
    return Phrase(text, FINN, "no", **kwargs)


def _lesson(sections: list[Section]) -> Lesson:
    return Lesson(title="A test lesson", language_code="no", sections=sections)


def _natural(*phrases: Phrase) -> Section:
    return Section(SectionType.NATURAL_SPEED, list(phrases))


# Module-level singleton so the argument default is not a per-call function
# invocation (B008) — the preprocessor is stateless.
_PASS_THROUGH = PassThroughPreprocessor()


def _price(
    lessons: list[Lesson],
    *,
    preprocessor=_PASS_THROUGH,
    planner=None,
    target_locale: str | None = TARGET_LOCALE,
    syllabify_fn=None,
    slicer_enabled: bool = False,
    parent_rate: str = PARENT_RATE,
    cache_dir: Path,
) -> RenderCost:
    return price_lessons(
        lessons,
        language_code="no",
        preprocessor=preprocessor,
        planner=planner,
        target_locale=target_locale,
        syllabify_fn=syllabify_fn,
        slicer_enabled=slicer_enabled,
        parent_rate=parent_rate,
        cache_dir=cache_dir,
    )


# ---------------------------------------------------------------------------
# Rule 1 — the synthesized text is preprocessed text, never phrase.text.
# ---------------------------------------------------------------------------


def test_rule1_prices_preprocessed_text_not_raw(tmp_path: Path) -> None:
    lesson = _lesson([_natural(_phrase("Hei"))])
    cost = _price([lesson], preprocessor=AlterPreprocessor(), cache_dir=tmp_path / "cache")

    assert cost.phrase.distinct == 1
    assert cost.phrase.misses == 1
    # The "a & b" result of the stub, not the raw "Hei": 39 vs 33 (measured).
    assert cost.phrase.billable_chars == 39
    assert cost.phrase.billable_chars == len(AzureTTSService._billable_body("a & b", FINN, "+0%", None, TARGET_LOCALE))
    assert cost.phrase.billable_chars != len(AzureTTSService._billable_body("Hei", FINN, "+0%", None, TARGET_LOCALE))


# ---------------------------------------------------------------------------
# Rule 2 — phonemes only when BOTH source_word and syllable_span are present.
# The dead probe had this condition INVERTED; the inversion is loud (AttributeError),
# so it is pinned as the branch tests below, not as a "wrong total" test.
# ---------------------------------------------------------------------------


def test_rule2_phonemes_require_both_source_word_and_syllable_span() -> None:
    planner = RecordingPlanner()

    # No planner configured -> plain synthesis, no plan_chunk call.
    assert _phrase_phonemes(None, "no", _phrase("sno", source_word="sno", syllable_span=(0, 1))) is None

    # A phrase in another language never carries this language's phonemes.
    assert _phrase_phonemes(planner, "no", Phrase("sno", FINN, "en", source_word="sno", syllable_span=(0, 1))) is None

    # source_word missing -> None, and the planner is NOT consulted.
    assert _phrase_phonemes(planner, "no", _phrase("sno", syllable_span=(0, 1))) is None
    assert planner.calls == []

    # syllable_span missing -> None, and the planner is NOT consulted.
    assert _phrase_phonemes(planner, "no", _phrase("sno", source_word="sno")) is None
    assert planner.calls == []

    # Both present -> the mapping keyed by phrase.text.lower().
    assert _phrase_phonemes(planner, "no", _phrase("sno", source_word="sno", syllable_span=(0, 1), upos="NOUN")) == {
        "sno": "ˈsnuː"
    }
    assert planner.calls == [("sno", (0, 1))]


def test_rule2_planner_results_none_means_no_phonemes() -> None:
    planner = NullPlanner()
    result = _phrase_phonemes(planner, "no", _phrase("sno", source_word="sno", syllable_span=(0, 1)))
    assert result is None


def test_rule2_aggregate_phoneme_leg_shows_in_chars_not_misses(tmp_path: Path) -> None:
    """The brief's wrong-answer backstop, reproduced synthetically.

    Skipping the phoneme leg leaves the miss COUNT unchanged (2 here / 265 on
    the real lesson) — only the character total moves (66 -> 111). Assert on
    characters, never on miss count, for the phoneme leg.
    """
    lesson = _lesson(
        [
            _natural(
                _phrase("sno", source_word="sno", syllable_span=(0, 1)),  # 78 with phonemes, 33 without
                _phrase("nei"),  # 33 either way
            )
        ]
    )
    cost = _price([lesson], planner=RecordingPlanner(), cache_dir=tmp_path / "cache")

    assert cost.phrase.distinct == 2
    assert cost.phrase.misses == 2  # identical either way — the wrong answer gets here
    assert cost.phrase.billable_chars == 111  # 78 (planned "sno") + 33 ("nei"); phoneme-less = 66
    assert cost.slicer.distinct == 0  # "sno" carries phonemes -> never sliced (Rule 5 exclusion)


# ---------------------------------------------------------------------------
# Rule 3 — speak_locale is the target locale ONLY for phrases in the section's
# language; a narrator (English) line must get None.
# ---------------------------------------------------------------------------


def test_rule3_speak_locale_only_for_phrases_in_target_language(tmp_path: Path) -> None:
    lesson = _lesson(
        [
            _natural(
                Phrase("Hei", EMMA, "no"),  # bilingual ML voice + wrapped in <lang nb-NO>: 63
                Phrase("Hei", EMMA, "en"),  # narrator line, bare: 33
            )
        ]
    )
    cost = _price([lesson], planner=None, cache_dir=tmp_path / "cache")

    assert cost.phrase.distinct == 2  # the two speak_locales are distinct cache keys
    assert cost.phrase.misses == 2
    # 63 (no phrase, wrapped) + 33 (en phrase, unwrapped) = 96.
    # A script that never passed speak_locale bills 66; one that always passes
    # it bills 126 — 96 is the discriminator between all three.
    assert cost.phrase.billable_chars == 96


# ---------------------------------------------------------------------------
# Rule 4 — dedupe by the 5-tuple (text, voice, rate, sorted phonemes, locale),
# render-scoped and shared across ALL sections (synth_memo), not per-section.
# ---------------------------------------------------------------------------


def test_rule4_dedupes_identical_phrases_across_sections(tmp_path: Path) -> None:
    lesson = _lesson(
        [
            _natural(_phrase("Hei")),  # NATURAL_SPEED
            Section(SectionType.SLOW_SPEED, [_phrase("Hei")]),  # same 5-tuple, another section
        ]
    )
    cost = _price([lesson], planner=None, cache_dir=tmp_path / "cache")

    # One key for both sections: per-section dedupe (or none at all) would
    # report distinct=2, misses=2, billable=66.
    assert cost.phrase.distinct == 1
    assert cost.phrase.misses == 1
    assert cost.phrase.billable_chars == 33


def test_memo_key_ignores_phoneme_mapping_order() -> None:
    """The renderer sorts the phoneme mapping into the key, so dict insertion
    order cannot split one mapping across two cache keys."""
    a = _memo_key("sno", FINN, "+0%", {"sno": "ˈsnuː", "nei": "næi"}, TARGET_LOCALE)
    b = _memo_key("sno", FINN, "+0%", {"nei": "næi", "sno": "ˈsnuː"}, TARGET_LOCALE)
    assert a == b


# ---------------------------------------------------------------------------
# Rule 5 — the slicer is a second, MUTUALLY EXCLUSIVE cost source: the whole
# parent word at PARENT_RATE with phonemes=None and the slicer's speak_locale,
# deduped by (source_word, voice_id), firing only at >= 2 syllables.
# ---------------------------------------------------------------------------


def test_rule5_slicer_prices_whole_parent_at_parent_rate(tmp_path: Path) -> None:
    """Two chunks of one parent word: two phrase keys (their own synthesis) but
    ONE slicer key — dedupe by (source_word, voice_id) — for the whole parent
    "snømann" at -40%, phonemes=None, native voice (measured: 38)."""
    lesson = _lesson(
        [
            _natural(
                _phrase("snø", source_word="snømann", syllable_span=(0, 1)),
                _phrase("mann", source_word="snømann", syllable_span=(1, 2)),
            )
        ]
    )
    cost = _price(
        [lesson],
        planner=None,
        syllabify_fn=lambda word: ["snø", "mann"],
        slicer_enabled=True,
        cache_dir=tmp_path / "cache",
    )

    assert cost.phrase.distinct == 2
    assert cost.phrase.misses == 2
    assert cost.phrase.billable_chars == 67  # "snø" (33) + "mann" (34)

    assert cost.slicer.distinct == 1  # both chunks share one parent render
    assert cost.slicer.misses == 1
    assert cost.slicer.billable_chars == 38
    assert cost.slicer.billable_chars == len(
        AzureTTSService._billable_body("snømann", FINN, PARENT_RATE, None, TARGET_LOCALE)
    )
    assert cost.billable_chars == 105


def test_rule5_phoneme_chunks_never_sliced(tmp_path: Path) -> None:
    """A phrase that carries planned phonemes sits in ipa_indices, so
    _apply_slicing skips it — the slicer leg must stay empty for it."""
    lesson = _lesson([_natural(_phrase("sno", source_word="sno", syllable_span=(0, 1)))])
    cost = _price(
        [lesson],
        planner=RecordingPlanner(),
        syllabify_fn=lambda word: ["sno"],
        slicer_enabled=True,
        cache_dir=tmp_path / "cache",
    )

    assert cost.phrase.billable_chars == 78  # planned "sno"
    assert cost.slicer.distinct == 0


def test_rule5_slicer_skips_words_under_two_syllables(tmp_path: Path) -> None:
    """_build_parent returns None (no extra synthesis) when the syllabifier
    splits the word into fewer than 2 syllables — or cannot split it at all."""
    one_syllable = _lesson([_natural(_phrase("en", source_word="en", syllable_span=(0, 1)))])
    for syllabify_fn in (lambda word: ["en"], lambda word: None):
        cost = _price(
            [one_syllable],
            planner=None,
            syllabify_fn=syllabify_fn,
            slicer_enabled=True,
            cache_dir=tmp_path / "cache",
        )
        assert cost.slicer.distinct == 0
        assert cost.slicer.misses == 0


def test_rule5_slicer_gated_off_when_disabled(tmp_path: Path) -> None:
    """slicer_enabled is the renderer's own gate (build_slicers returns nothing
    when the capability is closed or the language has no alignment wiring)."""
    lesson = _lesson([_natural(_phrase("snø", source_word="snømann", syllable_span=(0, 1)))])
    cost = _price(
        [lesson],
        planner=None,
        syllabify_fn=lambda word: ["snø", "mann"],
        slicer_enabled=False,
        cache_dir=tmp_path / "cache",
    )

    assert cost.slicer.distinct == 0
    assert cost.slicer.misses == 0
    assert cost.phrase.misses == 1  # the chunk's own synthesis is still priced


# ---------------------------------------------------------------------------
# The Tier B control pair, replicated synthetically with one plain phrase: the
# same tree priced twice, differing only in cache content. A script reporting
# the same thing for both is broken, and so is one that always reports the cold
# number. The cache file lives at the ADAPTER's own _cache_path — the only
# address space that decides hit-vs-miss; content is irrelevant, .exists() is
# the whole test.
# ---------------------------------------------------------------------------


def test_cache_control_pair_cold_vs_warm(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    lesson = _lesson([_natural(_phrase("Hei"))])

    cold = _price([lesson], planner=None, cache_dir=cache_dir)
    assert cold.phrase == LegStats(distinct=1, hits=0, misses=1, billable_chars=33)

    tts = AzureTTSService(cache_dir=cache_dir)
    cached = tts._cache_path("Hei", FINN, "+0%", None, TARGET_LOCALE)
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(b"x")  # whatever the adapter would have written; .exists() decides

    warm = _price([lesson], planner=None, cache_dir=cache_dir)
    assert warm.phrase == LegStats(distinct=1, hits=1, misses=0, billable_chars=0)


# ---------------------------------------------------------------------------
# _print_report — the human-readable shape: one line per leg plus a TOTAL, then
# the monthly-allowance context.
# ---------------------------------------------------------------------------


def test_print_report_shapes(capsys, tmp_path: Path) -> None:
    lesson = Lesson(title="A test lesson", language_code="no", sections=[])
    cost = RenderCost(
        phrase=LegStats(distinct=2, hits=1, misses=1, billable_chars=96),
        slicer=LegStats(distinct=3, hits=3, misses=0, billable_chars=0),
    )
    _print_report([lesson], tmp_path, cost)
    out = capsys.readouterr().out

    assert out.splitlines() == [
        "scope\ttitle: A test lesson",
        f"cache_dir\t{tmp_path}",
        "phrase_leg\tdistinct=2\thits=1\tmisses=1\tbillable=96",
        "slicer_leg\tdistinct=3\thits=3\tmisses=0\tbillable=0",
        "TOTAL\tdistinct=5\thits=4\tmisses=1\tbillable=96",
        "monthly_allowance\t500000\tshare=0.0%",
    ]


def test_print_report_many_lessons_scope_line(capsys, tmp_path: Path) -> None:
    lessons = [Lesson(title=f"Lesson {i}", language_code="no", sections=[]) for i in range(4)]
    _print_report(lessons, tmp_path, RenderCost(LegStats.empty(), LegStats.empty()))
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "scope\t4 lessons"


# ---------------------------------------------------------------------------
# main() end-to-end. conftest pins settings.database_urls / database_url, so
# resolve_db_path("no", settings) — the SAME call main() makes — points at a
# per-test tmp sqlite. The seeded lessons carry no provenance fields, so no
# plan_chunk / syllabify call ever fires regardless of alignment_installed().
# Every invocation passes --cache-dir so the REAL ~/.tunatale/tts-cache is
# never read or written.
# ---------------------------------------------------------------------------


def _seed(lesson: Lesson, lesson_id: str = "lesson-1") -> None:
    store = ContentStore(resolve_db_path("no", settings))
    try:
        store.save_lesson(lesson_id, "curriculum-1", 1, lesson)
    finally:
        store.close()


def test_main_prices_seeded_lesson_by_id(capsys, tmp_path: Path) -> None:
    _seed(_lesson([_natural(_phrase("Hei"))]))
    rc = main(
        [
            "--language",
            "no",
            "--lesson",
            "lesson-1",
            "--cache-dir",
            str(tmp_path / "cache"),
        ]
    )
    captured = capsys.readouterr()
    assert rc == 0
    assert captured.err == ""
    assert "scope\ttitle: A test lesson" in captured.out
    assert "phrase_leg\tdistinct=1\thits=0\tmisses=1\tbillable=33" in captured.out
    assert "slicer_leg\tdistinct=0\thits=0\tmisses=0\tbillable=0" in captured.out
    assert "TOTAL\tdistinct=1\thits=0\tmisses=1\tbillable=33" in captured.out


def test_main_all_prices_every_stored_lesson(capsys, tmp_path: Path) -> None:
    _seed(_lesson([_natural(_phrase("Hei"))]), lesson_id="lesson-1")
    _seed(_lesson([_natural(_phrase("Hei"))]), lesson_id="lesson-2")
    rc = main(["--language", "no", "--all", "--cache-dir", str(tmp_path / "cache")])
    captured = capsys.readouterr()
    assert rc == 0
    # _print_report names up to 3 lessons by title; only beyond 3 does it print
    # "N lessons" (exercised by test_print_report_many_lessons_scope_line).
    assert "scope\ttitle: A test lesson, title: A test lesson" in captured.out
    # The two lessons' identical 5-tuples dedupe into one cold request.
    assert "TOTAL\tdistinct=1\thits=0\tmisses=1\tbillable=33" in captured.out


def test_main_no_lessons_returns_1(capsys, tmp_path: Path) -> None:
    rc = main(["--language", "no", "--all", "--cache-dir", str(tmp_path / "cache")])
    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    assert "no lessons to price for language 'no'" in captured.err


def test_main_unknown_lesson_id_returns_1(capsys, tmp_path: Path) -> None:
    rc = main(
        [
            "--language",
            "no",
            "--lesson",
            "does-not-exist",
            "--cache-dir",
            str(tmp_path / "cache"),
        ]
    )
    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""
    assert "no such lesson: does-not-exist" in captured.err


def test_main_prices_named_foreign_lesson_with_note(capsys, tmp_path: Path) -> None:
    """A named lesson of another language is priced as asked but called out, so
    a mis-typed id cannot silently report another language's cost."""
    store = ContentStore(resolve_db_path("no", settings))
    try:
        store.save_lesson(
            "en-lesson",
            "curriculum-1",
            1,
            Lesson(
                title="An English lesson",
                language_code="en",
                sections=[Section(SectionType.NATURAL_SPEED, [Phrase("Hello", FINN, "en")])],
            ),
        )
    finally:
        store.close()
    rc = main(
        [
            "--language",
            "no",
            "--lesson",
            "en-lesson",
            "--cache-dir",
            str(tmp_path / "cache"),
        ]
    )
    captured = capsys.readouterr()
    assert rc == 0
    assert "note: en-lesson is 'en', not 'no'; pricing it anyway" in captured.err
    assert "scope\ttitle: An English lesson" in captured.out
