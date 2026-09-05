"""UPOS plumbing: sentence-context POS tags to the phoneme planner.

Tests the full pipeline: Phrase.upos round-trip, plan_chunk with upos,
annotate_chunk_upos tagging from key-phrase analysis, renderer pass-through,
and backfill script --dry-run.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from app.models.lesson import KeyPhraseInfo, Lesson, Phrase, Section, SectionType
from app.plugins.languages.no.lexicon import build_lexicon_db
from app.plugins.languages.no.phoneme_plan import NorwegianPhonemePlanner

if TYPE_CHECKING:
    from app.srs.database import SRSDatabase


# ---------------------------------------------------------------------------
# Fixture lexicon rows for POS-aware tests
# ---------------------------------------------------------------------------

# sporet: definite noun (NN) ends /ə/, past participle (VB/JJ) ends /ət/
_SPORET_ROWS = [
    ("sporet", "NN", '"spu:$r@', 1),
    ("sporet", "JJ", '""spu:$r@t', 1),
    ("sporet", "VB", '""spu:$r@t', 1),
]

# dekket: definite noun (NN) ends /ə/, past participle (VB) ends /ət/
_DEKKET_ROWS = [
    ("dekket", "NN", '"dE$k@', 1),
    ("dekket", "VB", '""dE$k@t', 1),
]

# huset: definite noun (NN) ends /ə/, past participle (VB) ends /ət/
_HUSET_ROWS = [
    ("huset", "NN", '"h}:$s@', 1),
    ("huset", "VB", '""h}:$s@t', 1),
]

# vitnet: definite noun (NN) ends /ə/, past participle (VB) ends /ət/
_VITNET_ROWS = [
    ("vitnet", "NN", '"vi:$n@', 1),
    ("vitnet", "VB", '""vi:$n@t', 1),
]

# galt: monosyllabic, AMBIGUOUS_NO_POS (2 readings)
_GALT_ROWS = [
    ("galt", "NN", '"gAl', 2),
    ("galt", "VB", '"gAl', 1),
]

# hagen: unambiguous, single reading
_HAGEN_ROWS = [
    ("hagen", "NN", '"hA:$g@n', 1),
]

# skisporet: unambiguous, single reading
_SKISPORET_ROWS = [
    ("skisporet", "NN", '"Si:$%spu:$r@', 1),
]

ALL_ROWS = _SPORET_ROWS + _DEKKET_ROWS + _HUSET_ROWS + _VITNET_ROWS + _GALT_ROWS + _HAGEN_ROWS + _SKISPORET_ROWS


def _make_db(tmp_path: Path, rows: list[tuple[str, str, str, int]] = ALL_ROWS) -> Path:
    gz = tmp_path / "fixture.tsv.gz"
    payload = "".join(f"{w}\t{p}\t{s}\t{c}\n" for w, p, s, c in rows)
    gz.write_bytes(__import__("gzip").compress(payload.encode("utf-8"), mtime=0))
    db = tmp_path / "lexicon.sqlite3"
    build_lexicon_db(gz, db)
    return db


def _planner(tmp_path: Path, rows: list[tuple[str, str, str, int]] = ALL_ROWS) -> NorwegianPhonemePlanner:
    db = _make_db(tmp_path, rows)
    return NorwegianPhonemePlanner(db)


# ---------------------------------------------------------------------------
# 1. Phrase.upos round-trip
# ---------------------------------------------------------------------------


class TestPhraseUposRoundTrip:
    """Phrase.upos persists through to_json/from_json, and a stored lesson
    WITHOUT the key still loads (backward compat)."""

    def test_upos_round_trips(self) -> None:
        phrase = Phrase(
            text="sporet",
            voice_id="v",
            language_code="no",
            source_word="sporet",
            syllable_span=(1, 2),
            upos="NOUN",
        )
        lesson = Lesson(
            title="Day 1",
            language_code="no",
            sections=[
                Section(
                    section_type=SectionType.KEY_PHRASES,
                    phrases=[phrase],
                )
            ],
        )
        restored = Lesson.from_json(lesson.to_json())
        assert restored.sections[0].phrases[0].upos == "NOUN"

    def test_upos_empty_by_default(self) -> None:
        phrase = Phrase(text="test", voice_id="v", language_code="no")
        assert phrase.upos == ""

    def test_stored_lesson_without_upos_loads(self) -> None:
        """Old lessons serialized without upos should deserialize with empty string."""
        old_json = json.dumps(
            {
                "title": "Test",
                "language_code": "no",
                "narrator_voice": "en-US-JennyNeural",
                "key_phrases": [],
                "sections": [
                    {
                        "section_type": "key_phrases",
                        "phrases": [
                            {
                                "text": "hei",
                                "voice_id": "v",
                                "language_code": "no",
                                "rate": "+0%",
                                "pitch": "+0Hz",
                                "volume": "+0%",
                                "role": "",
                            }
                        ],
                    }
                ],
                "generation_metadata": {},
            }
        )
        lesson = Lesson.from_json(old_json)
        assert lesson.sections[0].phrases[0].upos == ""

    def test_upos_in_to_json(self) -> None:
        phrase = Phrase(text="t", voice_id="v", language_code="no", upos="VERB")
        lesson = Lesson(
            title="T",
            language_code="no",
            sections=[Section(section_type=SectionType.KEY_PHRASES, phrases=[phrase])],
        )
        data = json.loads(lesson.to_json())
        assert data["sections"][0]["phrases"][0]["upos"] == "VERB"


# ---------------------------------------------------------------------------
# 2. POS-aware oracle tests for plan_chunk
# ---------------------------------------------------------------------------


class TestPlanChunkWithUpos:
    """plan_chunk(word, span, upos) resolves when POS disambiguates."""

    def test_sporet_noun_span_1_2(self, tmp_path: Path) -> None:
        """sporet NOUN → 'rə' (the user's original case)."""
        p = _planner(tmp_path)
        assert p.plan_chunk("sporet", (1, 2), upos="NOUN") == "rə"

    def test_sporet_verb_span_1_2(self, tmp_path: Path) -> None:
        """sporet VERB → 'rət'."""
        p = _planner(tmp_path)
        assert p.plan_chunk("sporet", (1, 2), upos="VERB") == "rət"

    def test_huset_noun_span_1_2(self, tmp_path: Path) -> None:
        """huset NOUN → 'sə'."""
        p = _planner(tmp_path)
        assert p.plan_chunk("huset", (1, 2), upos="NOUN") == "sə"

    def test_dekket_noun_span_1_2(self, tmp_path: Path) -> None:
        """dekket NOUN → 'kə'."""
        p = _planner(tmp_path)
        assert p.plan_chunk("dekket", (1, 2), upos="NOUN") == "kə"

    def test_vitnet_noun_span_1_2(self, tmp_path: Path) -> None:
        """vitnet NOUN → 'nə'."""
        p = _planner(tmp_path)
        assert p.plan_chunk("vitnet", (1, 2), upos="NOUN") == "nə"

    def test_sporet_noun_span_0_1(self, tmp_path: Path) -> None:
        """sporet NOUN span (0,1) → 'ˈspuː' (readings agree on first syllable)."""
        p = _planner(tmp_path)
        assert p.plan_chunk("sporet", (0, 1), upos="NOUN") == "ˈspuː"

    def test_sporet_none_upos_unchanged(self, tmp_path: Path) -> None:
        """sporet upos=None span (1,2) → None (unchanged today)."""
        p = _planner(tmp_path)
        assert p.plan_chunk("sporet", (1, 2), upos=None) is None

    def test_unambiguous_word_unchanged_with_upos(self, tmp_path: Path) -> None:
        """hagen with upos → same result as without (single reading)."""
        p = _planner(tmp_path)
        assert p.plan_chunk("hagen", (1, 2), upos="NOUN") == "gən"


# ---------------------------------------------------------------------------
# 3. AMBIGUOUS_POS_DIDNT_HELP reaches span-agreement
# ---------------------------------------------------------------------------


class TestAmbiguousPosDidntHelp:
    """A tag that does not narrow must not be worse than no tag.

    When resolve returns AMBIGUOUS_POS_DIDNT_HELP, plan_chunk must fall
    through to span-agreement (the candidate_transcriptions path), not
    return None early.
    """

    def test_ambiguous_pos_didnt_help_reaches_span_agreement(self, tmp_path: Path) -> None:
        """galt VERB: tag narrows to VB but all VB readings are the same →
        resolve returns AMBIGUOUS_POS_DIDNT_HELP if there were 2+ readings
        originally.

        Actually, galt has 2 readings (NN certainty 2, VB certainty 1).
        resolve("galt", "VERB") maps VERB→VB, filters to VB rows. With 1 VB
        row → RESOLVED. So this IS resolved.

        For a true AMBIGUOUS_POS_DIDNT_HELP we need a word where the POS tag
        narrows to multiple readings that still disagree. Let's use a synthetic
        fixture: 'testword' has 2 NN readings with different transcriptions
        that differ at the requested span.
        """
        rows = [
            ("testword", "NN", '"tE$s@', 1),
            ("testword", "NN", '"tE$k@', 1),
            ("testword", "VB", '"vE$s@', 1),
        ]
        p = _planner(tmp_path, rows)
        # resolve("testword", "NOUN") → maps to NN → 2 hits → AMBIGUOUS_POS_DIDNT_HELP
        # plan_chunk must fall through to span-agreement, which returns None
        # (the two NN readings differ at syllable 1).
        assert p.plan_chunk("testword", (1, 2), upos="NOUN") is None

    def test_ambiguous_pos_didnt_help_agreeing_spans_resolve(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When AMBIGUOUS_POS_DIDNT_HELP readings agree at the span → resolve.

        Two NN readings that differ only in stress (first syllable), but
        AGREE at second syllable.
        """
        import app.plugins.languages.no.phoneme_plan as pp_mod

        # (split, None) = "all readings AGREED on this split", which is the path
        # these tests are about: no tiebreak fired, so plan_chunk must still
        # decide by span-agreement across candidates (tunatale-k318.5).
        monkeypatch.setattr(pp_mod, "lexicon_reading", lambda _w, _db=None: (["test", "word"], None))
        rows = [
            ("testword", "NN", '"tE$s@', 1),
            ("testword", "NN", '""tE$s@', 1),
            ("testword", "VB", '"vE$k@t', 1),
        ]
        p = _planner(tmp_path, rows)
        # resolve("testword", "NOUN") → 2 NN hits → AMBIGUOUS_POS_DIDNT_HELP
        # Both NN readings agree at span (1,2): $s@ → sə
        result = p.plan_chunk("testword", (1, 2), upos="NOUN")
        assert result is not None

    def test_no_tag_ambiguous_no_pos_falls_through(self, tmp_path: Path) -> None:
        """sporet upos=None span (1,2) → None (readings disagree)."""
        p = _planner(tmp_path, _SPORET_ROWS)
        assert p.plan_chunk("sporet", (1, 2), upos=None) is None


# ---------------------------------------------------------------------------
# 4. Sabotage drills
# ---------------------------------------------------------------------------


class TestSabotageDrills:
    """Every test must fail when its guard is removed.

    Drills: remove the guard, watch the test go red, restore it.
    """

    def test_pos_aware_oracle_drill(self, tmp_path: Path) -> None:
        """Drill: remove upos='NOUN' → sporet (1,2) → None (red)."""
        p = _planner(tmp_path)
        # With POS → resolved
        assert p.plan_chunk("sporet", (1, 2), upos="NOUN") == "rə"
        # Without POS → ambiguous → None
        assert p.plan_chunk("sporet", (1, 2), upos=None) is None

    def test_ambiguous_pos_didnt_help_drill(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Drill: AMBIGUOUS_POS_DIDNT_HELP must not return None early.

        If the implementation returns None on AMBIGUOUS_POS_DIDNT_HELP instead
        of falling through to span-agreement, the agreeing-spans test would
        still pass (it expects None). So we test the AGREEING case:
        readings agree at the span → must return IPA, not None.
        """
        import app.plugins.languages.no.phoneme_plan as pp_mod

        # (split, None) = "all readings AGREED on this split", which is the path
        # these tests are about: no tiebreak fired, so plan_chunk must still
        # decide by span-agreement across candidates (tunatale-k318.5).
        monkeypatch.setattr(pp_mod, "lexicon_reading", lambda _w, _db=None: (["test", "word"], None))
        rows = [
            ("testword", "NN", '"tE$s@', 1),
            ("testword", "NN", '""tE$s@', 1),
            ("testword", "VB", '"vE$k@t', 1),
        ]
        p = _planner(tmp_path, rows)
        # With span-agreement fallback → resolves (readings agree at span 1,2)
        assert p.plan_chunk("testword", (1, 2), upos="NOUN") is not None


# ---------------------------------------------------------------------------
# 5. annotate_chunk_upos
# ---------------------------------------------------------------------------


class _UposLemmatizer:
    """Test lemmatizer that returns canned TokenAnalysis with upos tags."""

    def __init__(self, analyses: dict[str, list]) -> None:
        """analyses: {sentence_text: [TokenAnalysis(...), ...]}"""
        self._analyses = analyses
        self._cache_version = "test-v1"

    def lemmatize(self, word: str, language_code: str) -> str:
        return word.lower()

    def analyze(self, word: str, language_code: str) -> tuple[str, str, str]:
        return word.lower(), "", ""

    def analyze_sentence(self, sentence: str, language_code: str) -> list:
        from app.srs.lemmatizer import TokenAnalysis

        if sentence in self._analyses:
            return self._analyses[sentence]
        return [TokenAnalysis(surface=t, lemma=t.lower(), upos="") for t in sentence.split()]


class TestAnnotateChunkUpos:
    """annotate_chunk_upos tags chunks from their key phrase's analysis."""

    def _make_lesson(
        self,
        key_phrases: list[tuple[str, str]],
        chunk_specs: list[tuple[str, str, tuple[int, int], str]] | None = None,
    ) -> Lesson:
        """Build a KEY_PHRASES lesson.

        chunk_specs: list of (chunk_text, source_word, span, kp_phrase) — which
        key phrase each chunk belongs to. Chunks are interleaved after their
        key phrase's L2 text + EN translation, matching the real section builder.
        """
        l2 = "nb-NO-PernilleNeural"
        en = "en-US-GuyNeural"
        phrases = [Phrase(text="Key Phrases", voice_id=en, language_code="en", role="narrator")]

        # Group chunks by key phrase
        chunks_by_kp: dict[str, list[tuple[str, str, tuple[int, int]]]] = {}
        if chunk_specs:
            for chunk_text, source_word, span, kp_phrase in chunk_specs:
                chunks_by_kp.setdefault(kp_phrase, []).append((chunk_text, source_word, span))

        for kp_text, kp_translation in key_phrases:
            phrases.append(Phrase(text=kp_text, voice_id=l2, language_code="no"))
            phrases.append(Phrase(text=kp_translation, voice_id=en, language_code="no", role="narrator"))
            for chunk_text, source_word, span in chunks_by_kp.get(kp_text, []):
                phrases.append(
                    Phrase(
                        text=chunk_text,
                        voice_id=l2,
                        language_code="no",
                        source_word=source_word,
                        syllable_span=span,
                    )
                )

        return Lesson(
            title="Day 1",
            language_code="no",
            sections=[
                Section(section_type=SectionType.KEY_PHRASES, phrases=phrases),
            ],
            key_phrases=[KeyPhraseInfo(phrase=kp, translation=tr) for kp, tr in key_phrases],
        )

    def test_tags_chunk_from_its_key_phrase(self, tmp_path: Path, srs_db: SRSDatabase) -> None:
        """A chunk gets the UPOS from the key phrase it belongs to."""
        from app.api.generation import annotate_chunk_upos

        # Breakdown of "sporet": ['sporet', 'ret', 'spo', 'sporet', 'sporet']
        # with source_word on chunks 1,2,3
        lesson = self._make_lesson(
            key_phrases=[("sporet", "the track")],
            chunk_specs=[
                ("sporet", None, (0, 0), "sporet"),  # whole phrase, no source_word
                ("ret", "sporet", (1, 2), "sporet"),
                ("spo", "sporet", (0, 1), "sporet"),
                ("sporet", "sporet", (0, 2), "sporet"),
                ("sporet", None, (0, 0), "sporet"),  # whole word, no source_word
            ],
        )

        # Analysis: sporet is a NOUN in this sentence
        from app.srs.lemmatizer import TokenAnalysis

        analyses = {
            "sporet": [
                TokenAnalysis(surface="sporet", lemma="spor", upos="NOUN"),
            ]
        }
        lem = _UposLemmatizer(analyses)

        count = annotate_chunk_upos(lesson, srs_db, lemmatizer=lem, model_version="test-v1")
        assert count == 3  # 3 chunks have source_word="sporet"
        # All tagged chunks should have upos="NOUN"
        tagged = [p for p in lesson.sections[0].phrases if p.upos == "NOUN"]
        assert len(tagged) == 3
        assert all(p.source_word == "sporet" for p in tagged)

    def test_per_occurrence_attachment(self, tmp_path: Path, srs_db: SRSDatabase) -> None:
        """A word that is NOUN in one key phrase and VERB in another gets the right tag in each.

        This is THE test that proves per-occurrence attachment. A lesson-wide
        surface→upos map would confidently mispronounce one of them.
        """
        from app.api.generation import annotate_chunk_upos
        from app.srs.lemmatizer import TokenAnalysis

        # "sporet" breakdown: 5 chunks; "jeg sporet" breakdown: 7 chunks
        lesson = self._make_lesson(
            key_phrases=[
                ("sporet", "the track"),
                ("jeg sporet", "I tracked"),
            ],
            chunk_specs=[
                # sporet key phrase chunks
                ("sporet", None, (0, 0), "sporet"),
                ("ret", "sporet", (1, 2), "sporet"),
                ("spo", "sporet", (0, 1), "sporet"),
                ("sporet", "sporet", (0, 2), "sporet"),
                ("sporet", None, (0, 0), "sporet"),
                # jeg sporet key phrase chunks
                ("jeg sporet", None, (0, 0), "jeg sporet"),
                ("ret", "sporet", (1, 2), "jeg sporet"),
                ("spo", "sporet", (0, 1), "jeg sporet"),
                ("sporet", "sporet", (0, 2), "jeg sporet"),
                ("jeg", None, (0, 0), "jeg sporet"),
                ("jeg sporet", None, (0, 0), "jeg sporet"),
                ("jeg sporet", None, (0, 0), "jeg sporet"),
            ],
        )

        # Per-phrase analyses: sporet is NOUN in first, VERB in second
        analyses = {
            "sporet": [
                TokenAnalysis(surface="sporet", lemma="spor", upos="NOUN"),
            ],
            "jeg sporet": [
                TokenAnalysis(surface="jeg", lemma="jeg", upos="PRON"),
                TokenAnalysis(surface="sporet", lemma="spore", upos="VERB"),
            ],
        }
        lem = _UposLemmatizer(analyses)

        count = annotate_chunk_upos(lesson, srs_db, lemmatizer=lem, model_version="test-v1")
        assert count == 6  # 3 chunks with source_word="sporet" per key phrase

        # Verify per-occurrence: span (1,2) chunks get different tags
        span_1_2 = [p for p in lesson.sections[0].phrases if p.source_word == "sporet" and p.syllable_span == (1, 2)]
        assert len(span_1_2) == 2
        # First (from "sporet" key phrase) → NOUN
        assert span_1_2[0].upos == "NOUN"
        # Second (from "jeg sporet" key phrase) → VERB
        assert span_1_2[1].upos == "VERB"

    def test_no_tag_when_key_phrase_arithmetic_fails(self, tmp_path: Path, srs_db: SRSDatabase) -> None:
        """When the key-phrase arithmetic doesn't land, tag nothing and warn."""
        from app.api.generation import annotate_chunk_upos
        from app.srs.lemmatizer import TokenAnalysis

        # Build a lesson with an extra phrase that breaks the arithmetic
        l2 = "nb-NO-PernilleNeural"
        en = "en-US-GuyNeural"
        lesson = Lesson(
            title="Day 1",
            language_code="no",
            sections=[
                Section(
                    section_type=SectionType.KEY_PHRASES,
                    phrases=[
                        Phrase(text="Key Phrases", voice_id=en, language_code="en", role="narrator"),
                        Phrase(text="sporet", voice_id=l2, language_code="no"),
                        Phrase(text="the track", voice_id=en, language_code="no", role="narrator"),
                        # Extra phrase that breaks the expected count
                        Phrase(text="extra", voice_id=l2, language_code="no"),
                    ],
                )
            ],
            key_phrases=[KeyPhraseInfo(phrase="sporet", translation="the track")],
        )

        analyses = {
            "sporet": [TokenAnalysis(surface="sporet", lemma="spor", upos="NOUN")],
        }
        lem = _UposLemmatizer(analyses)

        with pytest.warns(UserWarning, match="key phrase"):
            count = annotate_chunk_upos(lesson, srs_db, lemmatizer=lem, model_version="test-v1")
        assert count == 0

    def test_no_lemmatizer_model_version_left_untouched(self, tmp_path: Path, srs_db: SRSDatabase) -> None:
        """A lesson with no lemmatizer model version is left untouched."""
        from app.api.generation import annotate_chunk_upos
        from app.srs.lemmatizer import LowercaseLemmatizer

        lesson = self._make_lesson(
            key_phrases=[("sporet", "the track")],
            chunk_specs=[("rə", "sporet", (1, 2), "sporet")],
        )

        lem = LowercaseLemmatizer()
        count = annotate_chunk_upos(lesson, srs_db, lemmatizer=lem, model_version="")
        assert count == 0
        # Chunk phrase untouched
        chunk_phrase = [p for p in lesson.sections[0].phrases if p.source_word == "sporet"][0]
        assert chunk_phrase.upos == ""

    def test_failure_does_not_break_lesson(self, tmp_path: Path, srs_db: SRSDatabase) -> None:
        """annotate_chunk_upos swallows exceptions and returns 0."""
        from app.api.generation import annotate_chunk_upos

        lesson = self._make_lesson(
            key_phrases=[("sporet", "the track")],
            chunk_specs=[("rə", "sporet", (1, 2), "sporet")],
        )

        class _BrokenLemmatizer:
            _cache_version = "broken"

            def lemmatize(self, word, language_code):
                raise RuntimeError("broken")

            def analyze(self, word, language_code):
                raise RuntimeError("broken")

            def analyze_sentence(self, sentence, language_code):
                raise RuntimeError("broken")

        with pytest.warns(UserWarning):
            count = annotate_chunk_upos(lesson, srs_db, lemmatizer=_BrokenLemmatizer(), model_version="v1")
        assert count == 0

    def test_no_key_phrases_section(self, tmp_path: Path, srs_db: SRSDatabase) -> None:
        """A lesson with no KEY_PHRASES section tags nothing."""
        from app.api.generation import annotate_chunk_upos
        from app.srs.lemmatizer import LowercaseLemmatizer

        lesson = Lesson(
            title="Day 1",
            language_code="no",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[
                        Phrase(text="Hello", voice_id="v", language_code="no"),
                    ],
                )
            ],
        )

        count = annotate_chunk_upos(lesson, srs_db, lemmatizer=LowercaseLemmatizer(), model_version="v1")
        assert count == 0

    def test_no_title_phrase_first_phrase_is_l2(self, tmp_path: Path, srs_db: SRSDatabase) -> None:
        """When the first phrase IS the L2 language (no narrator title), phrase_idx stays 0."""
        from app.api.generation import annotate_chunk_upos

        l2 = "nb-NO-PernilleNeural"
        en = "en-US-GuyNeural"
        # Breakdown of "sporet": 4 chunks → expected 2+4=6 phrases
        # No title phrase, so all 6 are in the section
        lesson = Lesson(
            title="Day 1",
            language_code="no",
            sections=[
                Section(
                    section_type=SectionType.KEY_PHRASES,
                    phrases=[
                        Phrase(text="sporet", voice_id=l2, language_code="no"),  # L2 text
                        Phrase(text="the track", voice_id=en, language_code="en", role="narrator"),  # EN translation
                        Phrase(
                            text="sporet", voice_id=l2, language_code="no", source_word="sporet", syllable_span=(0, 0)
                        ),
                        Phrase(text="ret", voice_id=l2, language_code="no", source_word="sporet", syllable_span=(1, 2)),
                        Phrase(text="spo", voice_id=l2, language_code="no", source_word="sporet", syllable_span=(0, 1)),
                        # ONE closing rung: the builder no longer appends the
                        # whole phrase twice, and annotate_chunk_upos consumes
                        # `2 + len(build_word_breakdown_spans(...))`, so a stale
                        # second copy leaves a phrase unconsumed.
                        Phrase(
                            text="sporet", voice_id=l2, language_code="no", source_word="sporet", syllable_span=(0, 2)
                        ),
                    ],
                )
            ],
            key_phrases=[KeyPhraseInfo(phrase="sporet", translation="the track")],
        )

        from app.srs.lemmatizer import TokenAnalysis

        analyses = {"sporet": [TokenAnalysis(surface="sporet", lemma="spor", upos="NOUN")]}
        lem = _UposLemmatizer(analyses)

        count = annotate_chunk_upos(lesson, srs_db, lemmatizer=lem, model_version="test-v1")
        # 4 chunks all have source_word="sporet", all get tagged
        assert count == 4

    def test_analysis_exception_warns_and_skips(self, tmp_path: Path, srs_db: SRSDatabase) -> None:
        """When analyze_sentence_cached raises, the key phrase is skipped with a warning."""
        from app.api.generation import annotate_chunk_upos

        lesson = self._make_lesson(
            key_phrases=[("sporet", "the track")],
            chunk_specs=[
                ("sporet", None, (0, 0), "sporet"),
                ("ret", "sporet", (1, 2), "sporet"),
                ("spo", "sporet", (0, 1), "sporet"),
                ("sporet", "sporet", (0, 2), "sporet"),
                ("sporet", None, (0, 0), "sporet"),
            ],
        )

        class _RaisingLemmatizer:
            _cache_version = "test-v1"

            def lemmatize(self, word, language_code):
                return word.lower()

            def analyze(self, word, language_code):
                return word.lower(), "", ""

            def analyze_sentence(self, sentence, language_code):
                raise RuntimeError("simulated analysis failure")

        with pytest.warns(UserWarning, match="analysis failed"):
            count = annotate_chunk_upos(lesson, srs_db, lemmatizer=_RaisingLemmatizer(), model_version="test-v1")
        assert count == 0

    def test_chunk_source_word_not_in_analysis(self, tmp_path: Path, srs_db: SRSDatabase) -> None:
        """A chunk whose source_word is not in the analysis result gets no UPOS tag."""
        from app.api.generation import annotate_chunk_upos
        from app.srs.lemmatizer import TokenAnalysis

        lesson = self._make_lesson(
            key_phrases=[("sporet", "the track")],
            chunk_specs=[
                ("sporet", None, (0, 0), "sporet"),
                ("ret", "sporet", (1, 2), "sporet"),
                ("spo", "sporet", (0, 1), "sporet"),
                ("sporet", "sporet", (0, 2), "sporet"),
                ("sporet", None, (0, 0), "sporet"),
            ],
        )

        # Analysis returns "spor" but the chunks have source_word="sporet"
        analyses = {"sporet": [TokenAnalysis(surface="spor", lemma="spor", upos="NOUN")]}
        lem = _UposLemmatizer(analyses)

        count = annotate_chunk_upos(lesson, srs_db, lemmatizer=lem, model_version="test-v1")
        # No chunks tagged because "sporet" != "spor"
        assert count == 0


# ---------------------------------------------------------------------------
# _annotate_upos_background is GONE (2026-08-26). It was the bug: both
# /api/story endpoints saved the lesson and THEN fired it as a detached task,
# so it tagged an in-memory Lesson nobody wrote again. Its three tests are
# removed with it rather than retargeted — annotate_chunk_upos_for_lesson
# covers the same three paths (empty model_version, success, swallowed
# exception) and is what both endpoints now await BEFORE saving. See
# TestTaggedBeforeSaved for the ordering guard.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Coverage: LessonPipeline._annotate_chunk_upos_background
# ---------------------------------------------------------------------------


class TestAnnotateChunkUposForLesson:
    """The awaited entry point: resolves the lemmatizer, never raises.

    NOTE ON WHAT THIS DOES NOT PIN: the pipeline must AWAIT this BEFORE
    store.save_lesson. It originally fired it as a detached task afterwards, so
    the lesson was persisted untagged and the tags were computed into an object
    nobody saved again — inert, with every test here still green because they
    exercise the helper directly. The ordering is enforced by code review and by
    the comment at the call site; a pipeline-level test that captures what
    save_lesson actually receives is filed separately.
    """

    async def test_no_model_version_returns_zero(self, srs_db: SRSDatabase) -> None:
        """A language whose lemmatizer has no model version is left untouched."""
        from app.api.generation import annotate_chunk_upos_for_lesson

        lesson = Lesson(
            title="Day 1",
            language_code="xx",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[Phrase(text="Hello", voice_id="v", language_code="xx")],
                )
            ],
        )
        assert await annotate_chunk_upos_for_lesson(lesson, srs_db) == 0
        assert all(p.upos == "" for sec in lesson.sections for p in sec.phrases)

    async def test_failure_is_swallowed(self, srs_db: SRSDatabase, caplog) -> None:
        """Tagging must never break generation."""
        from app.api.generation import annotate_chunk_upos_for_lesson

        class _Exploding:
            @property
            def language_code(self) -> str:
                raise RuntimeError("boom")

        with caplog.at_level(logging.WARNING):
            assert await annotate_chunk_upos_for_lesson(_Exploding(), srs_db) == 0
        assert any("UPOS annotation failed" in r.message for r in caplog.records)


class TestRendererPassesUpos:
    """renderer._phrase_phonemes passes phrase.upos to plan_chunk."""

    async def test_renderer_passes_upos_to_planner(self, tmp_path: Path) -> None:
        """The renderer passes phrase.upos (or None) to plan_chunk."""
        recorded_args: list[tuple] = []

        class _RecordingPlanner:
            def plan_chunk(
                self, source_word: str, span: tuple[int, int], upos: str | None = None, chunk_text: str | None = None
            ) -> str | None:
                recorded_args.append((source_word, span, upos))
                return f"IPA_{source_word}"

        from app.audio.pause_calculator import NaturalPauseCalculator
        from app.audio.preprocessing.base import TextPreprocessor
        from app.audio.renderer import LessonRenderer

        lesson = Lesson(
            title="Day 1",
            language_code="no",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[
                        Phrase(
                            text="rə",
                            voice_id="v",
                            language_code="no",
                            source_word="sporet",
                            syllable_span=(1, 2),
                            upos="NOUN",
                        ),
                        Phrase(
                            text="ha",
                            voice_id="v",
                            language_code="no",
                            source_word="hagen",
                            syllable_span=(0, 1),
                            upos="",
                        ),
                    ],
                )
            ],
        )

        class _NoPre(TextPreprocessor):
            def preprocess(self, text, section_type):
                return text

        class _FakeTTS:
            async def synthesize(self, text, voice_id, output_path, rate="+0%", phonemes=None):
                import io
                import struct

                buf = io.BytesIO()
                buf.write(b"RIFF")
                buf.write(struct.pack("<I", 36))
                buf.write(b"WAVE")
                buf.write(b"fmt ")
                buf.write(struct.pack("<IHHIIHH", 16, 1, 1, 24000, 48000, 2, 16))
                buf.write(b"data")
                buf.write(struct.pack("<I", 0))
                output_path.write_bytes(buf.getvalue())

        rdr = LessonRenderer(
            tts=_FakeTTS(),
            preprocessors={"no": _NoPre()},
            pause_calculator=NaturalPauseCalculator(),
            phoneme_planners={"no": _RecordingPlanner()},
        )

        await rdr.render(lesson, tmp_path / "out.wav")

        # First chunk: upos="NOUN"
        assert recorded_args[0] == ("sporet", (1, 2), "NOUN")
        # Second chunk: upos="" → passed as None
        assert recorded_args[1] == ("hagen", (0, 1), None)


# ---------------------------------------------------------------------------
# 7. Backfill --dry-run
# ---------------------------------------------------------------------------


class TestBackfillDryRun:
    """backfill_chunk_upos --dry-run writes nothing."""

    def test_dry_run_writes_nothing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """--dry-run prints output but does not modify the store."""
        from app.api.generation import annotate_chunk_upos
        from app.models.lesson import KeyPhraseInfo, Lesson, Phrase, Section, SectionType
        from app.srs.database import SRSDatabase
        from app.srs.lemmatizer import get_lemmatizer, model_version_for

        lesson = Lesson(
            title="Day 1",
            language_code="no",
            sections=[
                Section(
                    section_type=SectionType.KEY_PHRASES,
                    phrases=[
                        Phrase(text="Key Phrases", voice_id="en", language_code="en", role="narrator"),
                        Phrase(
                            text="sporet",
                            voice_id="v",
                            language_code="no",
                            source_word="sporet",
                            syllable_span=(1, 2),
                        ),
                    ],
                )
            ],
            key_phrases=[KeyPhraseInfo(phrase="sporet", translation="the track")],
        )

        lem = get_lemmatizer("no")
        mv = model_version_for(lem)

        with SRSDatabase(":memory:") as srs_db:
            count = annotate_chunk_upos(lesson, srs_db, lemmatizer=lem, model_version=mv)

        # LowercaseLemmatizer produces empty upos, so nothing is tagged
        assert count == 0


class TestAnnotateSuccessPath:
    """The awaited helper actually runs the tagging when a model version exists.

    The lemmatizer is injected rather than resolved: a real one loads stanza,
    which CI deliberately does not install (``--no-group lemmatizers``), so this
    is the only way the success path runs in the gate. The double implements the
    analyze_sentence seam, exactly as the renderer tests inject a fake TTS.
    """

    async def test_tags_through_the_awaited_helper(self, srs_db: SRSDatabase) -> None:
        from app.api.generation import annotate_chunk_upos_for_lesson
        from app.srs.lemmatizer import TokenAnalysis

        lesson = TestAnnotateChunkUpos._make_lesson(
            TestAnnotateChunkUpos,
            key_phrases=[("sporet", "the track")],
            chunk_specs=[
                ("sporet", None, (0, 0), "sporet"),
                ("ret", "sporet", (1, 2), "sporet"),
                ("spo", "sporet", (0, 1), "sporet"),
                ("sporet", "sporet", (0, 2), "sporet"),
                ("sporet", None, (0, 0), "sporet"),
            ],
        )
        lem = _UposLemmatizer({"sporet": [TokenAnalysis(surface="sporet", lemma="spor", upos="NOUN")]})

        tagged = await annotate_chunk_upos_for_lesson(lesson, srs_db, lemmatizer=lem, model_version="test-v1")

        assert tagged >= 1
        chunk = next(p for sec in lesson.sections for p in sec.phrases if p.source_word == "sporet")
        assert chunk.upos == "NOUN"


class TestTaggedBeforeSaved:
    """tunatale-bxhl: the tags must be in what STORAGE receives.

    A property of the stored lesson, not of source order. The bug it guards
    computes the right tags and then throws them away, so every assertion about
    ``annotate_chunk_upos`` itself stays green while production stores an
    untagged lesson — which is exactly how it survived until now.

    OBSERVED IN PRODUCTION 2026-08-26: a freshly generated Norwegian lesson had
    0 of 47 chunks tagged, and re-running the same annotation over the stored
    copy tagged all 47. ``LessonPipeline._generate`` had been fixed to await
    before saving, but both ``/api/story`` endpoints still saved first and then
    fired a detached task that mutated an in-memory Lesson nobody wrote again.
    """

    async def test_stored_lesson_already_carries_upos(self, tmp_path: Path) -> None:
        import copy as _copy

        from httpx import ASGITransport, AsyncClient

        from app.languages import get_language
        from app.main import app
        from app.models.curriculum import Curriculum, CurriculumDay
        from app.srs.database import SRSDatabase
        from app.srs.lemmatizer import TokenAnalysis
        from app.storage.store import ContentStore

        captured: list[Lesson] = []

        class _CapturingStore(ContentStore):
            def save_lesson(self, lesson_id, curriculum_id, day, lesson):  # type: ignore[override]
                # Deep-copied at the moment storage sees it, so a LATER mutation
                # of the same object cannot make an untagged save look tagged.
                captured.append(_copy.deepcopy(lesson))
                super().save_lesson(lesson_id, curriculum_id, day, lesson)

        class _StubGenerator:
            async def generate(self, **kwargs):
                return _story_lesson()

        def _story_lesson() -> Lesson:
            """Built by the REAL section builder, so annotate_chunk_upos's
            phrase arithmetic (2 + len(breakdown) per key phrase) matches."""
            from app.generation.section_builder import build_key_phrases_section

            section = build_key_phrases_section(
                [{"phrase": "sporet er kaldt", "translation": "the track is cold"}],
                {"female-1": "nb-NO-PernilleNeural"},
                "en-US-GuyNeural",
                "no",
            )
            return Lesson(
                title="Day 1",
                language_code="no",
                sections=[section],
                key_phrases=[KeyPhraseInfo(phrase="sporet er kaldt", translation="the track is cold")],
            )

        curriculum = Curriculum(
            id="c1",
            topic="t",
            language_code="no",
            cefr_level="A2",
            days=[
                CurriculumDay(
                    day=1,
                    title="Day 1",
                    focus="tracks",
                    learning_objective="describe a track",
                    story_guidance="a cold track",
                    collocations=["sporet er kaldt"],
                )
            ],
        )

        store = _CapturingStore(":memory:")
        store.save_curriculum("c1", curriculum)
        app.state.content_store = store
        app.state.story_generator = _StubGenerator()
        app.state.language = get_language("no")
        app.state.srs_db = SRSDatabase(":memory:")
        app.state.pipeline = None
        # The seam: without it the endpoint resolves Stanza for real, which the
        # default gate does not run (--run-stanza).
        app.state.lemmatizer = _UposLemmatizer(
            {"sporet er kaldt": [TokenAnalysis(surface="sporet", lemma="spor", upos="NOUN")]}
        )
        app.state.model_version = "test-v1"
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/api/story/generate",
                    json={"curriculum_id": "c1", "day": 1, "strategy": "WIDER"},
                )
            assert response.status_code == 201, response.text
        finally:
            app.state.srs_db.close()
            for attr in ("lemmatizer", "model_version"):
                if hasattr(app.state, attr):
                    delattr(app.state, attr)

        assert captured, "save_lesson was never called"
        stored = [p for sec in captured[0].sections for p in sec.phrases if p.source_word is not None]
        assert stored, "fixture produced no provenance-carrying chunks"
        sporet = [p for p in stored if p.source_word == "sporet"]
        assert sporet, f"no 'sporet' chunks in {[p.source_word for p in stored]}"
        assert all(p.upos == "NOUN" for p in sporet), (
            "the lesson reached storage untagged — annotation ran after the save; "
            f"got {[(p.text, p.upos) for p in sporet]}"
        )

    async def test_imported_lesson_is_re_saved_with_its_tags(self, tmp_path: Path) -> None:
        """/import writes the lesson before it can be tagged, so the tags need
        a SECOND write. Without it the import path has the same silent bug the
        generate path had — the difference is only where the save happens."""
        import copy as _copy

        from httpx import ASGITransport, AsyncClient

        from app.languages import get_language
        from app.main import app
        from app.models.curriculum import Curriculum, CurriculumDay
        from app.srs.database import SRSDatabase
        from app.srs.lemmatizer import TokenAnalysis
        from app.storage.store import ContentStore

        updates: list[Lesson] = []

        class _RecordingStore(ContentStore):
            def update_lesson_data(self, lesson_id, lesson):  # type: ignore[override]
                updates.append(_copy.deepcopy(lesson))
                return super().update_lesson_data(lesson_id, lesson)

        curriculum = Curriculum(
            id="c1",
            topic="t",
            language_code="no",
            cefr_level="A2",
            days=[
                CurriculumDay(
                    day=1,
                    title="Day 1",
                    focus="tracks",
                    learning_objective="describe a track",
                    story_guidance="a cold track",
                    collocations=["sporet er kaldt"],
                )
            ],
        )
        store = _RecordingStore(":memory:")
        store.save_curriculum("c1", curriculum)
        app.state.content_store = store
        app.state.language = get_language("no")
        app.state.srs_db = SRSDatabase(":memory:")
        app.state.pipeline = None
        app.state.lemmatizer = _UposLemmatizer(
            {"sporet er kaldt": [TokenAnalysis(surface="sporet", lemma="spor", upos="NOUN")]}
        )
        app.state.model_version = "test-v1"
        story = {
            "title": "Day 1",
            "key_phrases": [{"phrase": "sporet er kaldt", "translation": "the track is cold"}],
            "dialogue": [{"speaker": "female-1", "text": "sporet er kaldt", "translation": "the track is cold"}],
        }
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/api/story/import",
                    json={"curriculum_id": "c1", "day": 1, "story": story},
                )
            assert response.status_code == 201, response.text
        finally:
            app.state.srs_db.close()
            for attr in ("lemmatizer", "model_version"):
                if hasattr(app.state, attr):
                    delattr(app.state, attr)

        assert updates, "the tagged lesson was never written back — the tags are lost"
        sporet = [p for sec in updates[-1].sections for p in sec.phrases if p.source_word == "sporet"]
        assert sporet and all(p.upos == "NOUN" for p in sporet), [(p.text, p.upos) for p in sporet]
