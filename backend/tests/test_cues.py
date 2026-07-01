"""Tests for build_cue_manifest — pure cue-ref derivation from lesson + timing."""

import pytest

from app.audio.cues import CueTiming, build_cue_manifest
from app.generation.section_builder import build_word_breakdown
from app.models.lesson import KeyPhraseInfo, Lesson, Phrase, Section, SectionType


def _kp_phrase(phrase: str, translation: str = "hello") -> KeyPhraseInfo:
    return KeyPhraseInfo(phrase=phrase, translation=translation)


class TestBuildCueManifestDialogue:
    """Ref derivation for natural_speed / slow_speed / translated sections."""

    def test_natural_speed_l2_lines_get_line_refs(self):
        """Every L2 phrase in natural_speed gets ref {kind:'line', target_index:n}."""
        lesson = Lesson(
            title="Test",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[
                        Phrase(text="Natural Speed", voice_id="en-US-GuyNeural", language_code="en", role="narrator"),
                        Phrase(text="Dober dan", voice_id="sl-SI-PetraNeural", language_code="sl", role="female-1"),
                        Phrase(text="Kako si", voice_id="sl-SI-PetraNeural", language_code="sl", role="female-1"),
                    ],
                )
            ],
        )
        timing = [
            CueTiming(section_index=0, phrase_index=0, start_frame=0, end_frame=1000),
            CueTiming(section_index=0, phrase_index=1, start_frame=2000, end_frame=3000),
            CueTiming(section_index=0, phrase_index=2, start_frame=4000, end_frame=5000),
        ]
        cues = build_cue_manifest(lesson, timing, rate=1000)

        # Section title (narrator, en) → narration
        assert cues[0].ref == {"kind": "narration"}
        # First L2 phrase → line 0
        assert cues[1].ref == {"kind": "line", "target_index": 0}
        # Second L2 phrase → line 1
        assert cues[2].ref == {"kind": "line", "target_index": 1}

    def test_translated_narrator_following_l2_refs_same_line(self):
        """In translated, a narrator cue immediately after an L2 cue refs that line."""
        lesson = Lesson(
            title="Test",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.TRANSLATED,
                    phrases=[
                        Phrase(text="Translated", voice_id="en-US-GuyNeural", language_code="en", role="narrator"),
                        Phrase(text="Dober dan", voice_id="sl-SI-PetraNeural", language_code="sl", role="female-1"),
                        Phrase(text="Good day", voice_id="en-US-GuyNeural", language_code="en", role="narrator"),
                        Phrase(text="Hvala", voice_id="sl-SI-PetraNeural", language_code="sl", role="female-1"),
                        Phrase(text="Thanks", voice_id="en-US-GuyNeural", language_code="en", role="narrator"),
                    ],
                )
            ],
        )
        timing = [
            CueTiming(section_index=0, phrase_index=0, start_frame=0, end_frame=1000),
            CueTiming(section_index=0, phrase_index=1, start_frame=2000, end_frame=3000),
            CueTiming(section_index=0, phrase_index=2, start_frame=4000, end_frame=5000),
            CueTiming(section_index=0, phrase_index=3, start_frame=6000, end_frame=7000),
            CueTiming(section_index=0, phrase_index=4, start_frame=8000, end_frame=9000),
        ]
        cues = build_cue_manifest(lesson, timing, rate=1000)

        # Section title → narration
        assert cues[0].ref == {"kind": "narration"}
        # L2 → line 0
        assert cues[1].ref == {"kind": "line", "target_index": 0}
        # Narrator following L2 → same line 0
        assert cues[2].ref == {"kind": "line", "target_index": 0}
        # L2 → line 1
        assert cues[3].ref == {"kind": "line", "target_index": 1}
        # Narrator following L2 → same line 1
        assert cues[4].ref == {"kind": "line", "target_index": 1}

    def test_narrator_not_following_l2_gets_narration_ref(self):
        """Narrator cues that don't follow an L2 (e.g. scene labels) get narration."""
        lesson = Lesson(
            title="Test",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[
                        Phrase(text="Natural Speed", voice_id="en-US-GuyNeural", language_code="en", role="narrator"),
                        Phrase(text="At the cafe", voice_id="en-US-GuyNeural", language_code="en", role="narrator"),
                        Phrase(text="Dober dan", voice_id="sl-SI-PetraNeural", language_code="sl", role="female-1"),
                    ],
                )
            ],
        )
        timing = [
            CueTiming(section_index=0, phrase_index=0, start_frame=0, end_frame=1000),
            CueTiming(section_index=0, phrase_index=1, start_frame=2000, end_frame=3000),
            CueTiming(section_index=0, phrase_index=2, start_frame=4000, end_frame=5000),
        ]
        cues = build_cue_manifest(lesson, timing, rate=1000)

        assert cues[0].ref == {"kind": "narration"}  # section title
        assert cues[1].ref == {"kind": "narration"}  # scene label
        assert cues[2].ref == {"kind": "line", "target_index": 0}  # L2

    def test_slow_speed_l2_lines_get_line_refs(self):
        """L2 phrases in slow_speed get line refs (mirroring natural_speed)."""
        lesson = Lesson(
            title="Test",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.SLOW_SPEED,
                    phrases=[
                        Phrase(text="Slow Speed", voice_id="en-US-GuyNeural", language_code="en", role="narrator"),
                        Phrase(text="Dober dan", voice_id="sl-SI-PetraNeural", language_code="sl", role="female-1"),
                    ],
                )
            ],
        )
        timing = [
            CueTiming(section_index=0, phrase_index=0, start_frame=0, end_frame=1000),
            CueTiming(section_index=0, phrase_index=1, start_frame=2000, end_frame=3000),
        ]
        cues = build_cue_manifest(lesson, timing, rate=1000)

        assert cues[0].ref == {"kind": "narration"}
        assert cues[1].ref == {"kind": "line", "target_index": 0}


class TestBuildCueManifestKeyPhrases:
    """Ref derivation for key_phrases section via deterministic builder."""

    def test_single_key_phrase_consumes_expected_count(self):
        """A single key phrase produces 2 + len(build_word_breakdown()) refs."""
        lesson = Lesson(
            title="Test",
            language_code="sl",
            key_phrases=[_kp_phrase("hvala")],
            sections=[
                Section(
                    section_type=SectionType.KEY_PHRASES,
                    phrases=[
                        Phrase(text="Key Phrases", voice_id="en-US-GuyNeural", language_code="en", role="narrator"),
                        Phrase(text="hvala", voice_id="sl-SI-PetraNeural", language_code="sl", role="female-1"),
                        Phrase(text="thanks", voice_id="en-US-GuyNeural", language_code="en", role="narrator"),
                        *[
                            Phrase(text=step, voice_id="sl-SI-PetraNeural", language_code="sl", role="female-1")
                            for step in build_word_breakdown("hvala", "sl")
                        ],
                    ],
                )
            ],
        )
        n_breakdown = len(build_word_breakdown("hvala", "sl"))
        total_kp_phrases = 2 + n_breakdown  # L2 + translation + breakdown
        total = 1 + total_kp_phrases  # + section title
        timing = [
            CueTiming(section_index=0, phrase_index=i, start_frame=i * 1000, end_frame=(i + 1) * 1000)
            for i in range(total)
        ]
        cues = build_cue_manifest(lesson, timing, rate=1000)

        # Section title → narration
        assert cues[0].ref == {"kind": "narration"}
        # All phrases for the key phrase get ref key_phrase:0
        for i in range(1, total):
            assert cues[i].ref == {"kind": "key_phrase", "target_index": 0}, f"cue[{i}] should be key_phrase:0"

    def test_two_key_phrases_consume_expected_counts(self):
        """Two key phrases are each assigned ascending target_index."""
        lesson = Lesson(
            title="Test",
            language_code="sl",
            key_phrases=[_kp_phrase("hvala"), _kp_phrase("dober dan")],
            sections=[
                Section(
                    section_type=SectionType.KEY_PHRASES,
                    phrases=[
                        Phrase(text="Key Phrases", voice_id="en-US-GuyNeural", language_code="en", role="narrator"),
                        # First key phrase: hvala
                        Phrase(text="hvala", voice_id="sl-SI-PetraNeural", language_code="sl", role="female-1"),
                        Phrase(text="thanks", voice_id="en-US-GuyNeural", language_code="en", role="narrator"),
                        *[
                            Phrase(text=step, voice_id="sl-SI-PetraNeural", language_code="sl", role="female-1")
                            for step in build_word_breakdown("hvala", "sl")
                        ],
                        # Second key phrase: dober dan
                        Phrase(text="dober dan", voice_id="sl-SI-PetraNeural", language_code="sl", role="female-1"),
                        Phrase(text="good day", voice_id="en-US-GuyNeural", language_code="en", role="narrator"),
                        *[
                            Phrase(text=step, voice_id="sl-SI-PetraNeural", language_code="sl", role="female-1")
                            for step in build_word_breakdown("dober dan", "sl")
                        ],
                    ],
                )
            ],
        )
        n0 = len(build_word_breakdown("hvala", "sl"))
        kp0_count = 2 + n0
        n1 = len(build_word_breakdown("dober dan", "sl"))
        kp1_count = 2 + n1
        total = 1 + kp0_count + kp1_count

        timing = [
            CueTiming(section_index=0, phrase_index=i, start_frame=i * 1000, end_frame=(i + 1) * 1000)
            for i in range(total)
        ]
        cues = build_cue_manifest(lesson, timing, rate=1000)

        assert cues[0].ref == {"kind": "narration"}
        # First key phrase
        for i in range(1, 1 + kp0_count):
            assert cues[i].ref == {"kind": "key_phrase", "target_index": 0}, f"cue[{i}] should be key_phrase:0"
        # Second key phrase
        for i in range(1 + kp0_count, total):
            assert cues[i].ref == {"kind": "key_phrase", "target_index": 1}, f"cue[{i}] should be key_phrase:1"

    def test_duplicate_key_phrases_are_both_tagged(self):
        """Duplicate phrases are resolved by count, not text matching."""
        lesson = Lesson(
            title="Test",
            language_code="sl",
            key_phrases=[_kp_phrase("hvala"), _kp_phrase("hvala")],
            sections=[
                Section(
                    section_type=SectionType.KEY_PHRASES,
                    phrases=[
                        Phrase(text="Key Phrases", voice_id="en-US-GuyNeural", language_code="en", role="narrator"),
                        # First "hvala"
                        Phrase(text="hvala", voice_id="sl-SI-PetraNeural", language_code="sl", role="female-1"),
                        Phrase(text="thanks", voice_id="en-US-GuyNeural", language_code="en", role="narrator"),
                        *[
                            Phrase(text=step, voice_id="sl-SI-PetraNeural", language_code="sl", role="female-1")
                            for step in build_word_breakdown("hvala", "sl")
                        ],
                        # Second "hvala"
                        Phrase(text="hvala", voice_id="sl-SI-PetraNeural", language_code="sl", role="female-1"),
                        Phrase(text="thanks again", voice_id="en-US-GuyNeural", language_code="en", role="narrator"),
                        *[
                            Phrase(text=step, voice_id="sl-SI-PetraNeural", language_code="sl", role="female-1")
                            for step in build_word_breakdown("hvala", "sl")
                        ],
                    ],
                )
            ],
        )
        n = len(build_word_breakdown("hvala", "sl"))
        kp_count = 2 + n
        total = 1 + 2 * kp_count

        timing = [
            CueTiming(section_index=0, phrase_index=i, start_frame=i * 1000, end_frame=(i + 1) * 1000)
            for i in range(total)
        ]
        cues = build_cue_manifest(lesson, timing, rate=1000)

        for i in range(1, 1 + kp_count):
            assert cues[i].ref == {"kind": "key_phrase", "target_index": 0}
        for i in range(1 + kp_count, total):
            assert cues[i].ref == {"kind": "key_phrase", "target_index": 1}

    def test_phrase_count_mismatch_raises(self):
        """A leftover phrase count after consuming all key phrases raises loudly."""
        lesson = Lesson(
            title="Test",
            language_code="sl",
            key_phrases=[_kp_phrase("hvala")],
            sections=[
                Section(
                    section_type=SectionType.KEY_PHRASES,
                    phrases=[
                        Phrase(text="Key Phrases", voice_id="en-US-GuyNeural", language_code="en", role="narrator"),
                        Phrase(text="hvala", voice_id="sl-SI-PetraNeural", language_code="sl", role="female-1"),
                        Phrase(text="thanks", voice_id="en-US-GuyNeural", language_code="en", role="narrator"),
                    ],
                )
            ],
        )
        # Only 3 phrases (title + L2 + trans), expected 1 + (2 + n_breakdown) = 1+2+2=5
        total = 3
        timing = [
            CueTiming(section_index=0, phrase_index=i, start_frame=i * 1000, end_frame=(i + 1) * 1000)
            for i in range(total)
        ]
        with pytest.raises(ValueError, match="Key phrase phrase-count mismatch"):
            build_cue_manifest(lesson, timing, rate=1000)

    def test_too_many_phrases_raises(self):
        """Extra phrases beyond expected key phrase count also raise."""
        lesson = Lesson(
            title="Test",
            language_code="sl",
            key_phrases=[_kp_phrase("hvala")],
            sections=[
                Section(
                    section_type=SectionType.KEY_PHRASES,
                    phrases=[
                        Phrase(text="Key Phrases", voice_id="en-US-GuyNeural", language_code="en", role="narrator"),
                        Phrase(text="hvala", voice_id="sl-SI-PetraNeural", language_code="sl", role="female-1"),
                        Phrase(text="thanks", voice_id="en-US-GuyNeural", language_code="en", role="narrator"),
                        Phrase(text="hvala", voice_id="sl-SI-PetraNeural", language_code="sl", role="female-1"),
                    ],
                )
            ],
        )
        total = 4
        timing = [
            CueTiming(section_index=0, phrase_index=i, start_frame=i * 1000, end_frame=(i + 1) * 1000)
            for i in range(total)
        ]
        with pytest.raises(ValueError, match="Key phrase phrase-count mismatch"):
            build_cue_manifest(lesson, timing, rate=1000)


class TestBuildCueManifestMultiSection:
    """Full manifest with title + multiple section types."""

    def test_title_cue_comes_first(self):
        """The lesson title produces a cue at index 0 with narration ref."""
        lesson = Lesson(title="Lesson 1", language_code="sl", key_phrases=[_kp_phrase("hvala")])
        # No sections — just title timing
        timing = [CueTiming(section_index=None, phrase_index=0, start_frame=0, end_frame=8000)]
        cues = build_cue_manifest(lesson, timing, rate=1000)
        assert len(cues) == 1
        assert cues[0].index == 0
        assert cues[0].start_ms == 0
        assert cues[0].end_ms == 8000
        assert cues[0].section_index is None
        assert cues[0].section_type is None
        assert cues[0].text == "Lesson 1"

    def test_title_and_section_cues_have_correct_fields(self):
        """Each cue carries index, timing, section info, role, language_code, text."""
        lesson = Lesson(
            title="My Lesson",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[
                        Phrase(text="Natural Speed", voice_id="en-US-GuyNeural", language_code="en", role="narrator"),
                        Phrase(text="Zdravo", voice_id="sl-SI-PetraNeural", language_code="sl", role="female-1"),
                    ],
                )
            ],
        )
        timing = [
            CueTiming(section_index=None, phrase_index=0, start_frame=0, end_frame=5000),
            CueTiming(section_index=0, phrase_index=0, start_frame=10000, end_frame=15000),
            CueTiming(section_index=0, phrase_index=1, start_frame=20000, end_frame=25000),
        ]
        cues = build_cue_manifest(lesson, timing, rate=5000)  # 5 kHz → 200 µs per frame

        assert len(cues) == 3
        # Title
        assert cues[0].index == 0
        assert cues[0].start_ms == 0
        assert cues[0].end_ms == 1000  # 5000 frames / 5 = 1000 ms
        assert cues[0].section_index is None
        assert cues[0].section_type is None
        assert cues[0].phrase_index == 0
        assert cues[0].role == "narrator"
        assert cues[0].language_code == "en"
        assert cues[0].text == "My Lesson"
        # Section title
        assert cues[1].index == 1
        assert cues[1].start_ms == 2000
        assert cues[1].end_ms == 3000
        assert cues[1].section_index == 0
        assert cues[1].section_type == "natural_speed"
        assert cues[1].phrase_index == 0
        assert cues[1].role == "narrator"
        assert cues[1].language_code == "en"
        assert cues[1].text == "Natural Speed"
        # L2 phrase
        assert cues[2].index == 2
        assert cues[2].start_ms == 4000
        assert cues[2].end_ms == 5000
        assert cues[2].section_index == 0
        assert cues[2].section_type == "natural_speed"
        assert cues[2].phrase_index == 1
        assert cues[2].role == "female-1"
        assert cues[2].language_code == "sl"
        assert cues[2].text == "Zdravo"

    def test_multiple_sections_accumulate_cues(self):
        """Cues from multiple sections are concatenated with correct section_index."""
        lesson = Lesson(
            title="Multi",
            language_code="sl",
            key_phrases=[_kp_phrase("hvala")],
            sections=[
                Section(
                    section_type=SectionType.KEY_PHRASES,
                    phrases=[
                        Phrase(text="Key Phrases", voice_id="en-US-GuyNeural", language_code="en", role="narrator"),
                        Phrase(text="hvala", voice_id="sl-SI-PetraNeural", language_code="sl", role="female-1"),
                        Phrase(text="thanks", voice_id="en-US-GuyNeural", language_code="en", role="narrator"),
                        *[
                            Phrase(text=step, voice_id="sl-SI-PetraNeural", language_code="sl", role="female-1")
                            for step in build_word_breakdown("hvala", "sl")
                        ],
                    ],
                ),
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[
                        Phrase(text="Natural Speed", voice_id="en-US-GuyNeural", language_code="en", role="narrator"),
                        Phrase(text="Hvala", voice_id="sl-SI-PetraNeural", language_code="sl", role="female-1"),
                    ],
                ),
            ],
        )
        kp_n = len(build_word_breakdown("hvala", "sl"))
        kp_total = 1 + 2 + kp_n  # title + L2 + trans + breakdown
        ns_total = 2  # section title + L2
        total = 1 + kp_total + ns_total  # lesson title + KP section + NS section

        timing = [
            CueTiming(section_index=None, phrase_index=0, start_frame=0, end_frame=5000),
            *[
                CueTiming(section_index=0, phrase_index=i, start_frame=(i + 1) * 5000, end_frame=(i + 2) * 5000)
                for i in range(kp_total)
            ],
            *[
                CueTiming(
                    section_index=1,
                    phrase_index=i,
                    start_frame=(i + 1 + kp_total) * 5000,
                    end_frame=(i + 2 + kp_total) * 5000,
                )
                for i in range(ns_total)
            ],
        ]
        cues = build_cue_manifest(lesson, timing, rate=1000)

        assert len(cues) == total
        assert cues[0].section_index is None
        # Key phrases section
        for i in range(1, 1 + kp_total):
            assert cues[i].section_index == 0, f"cue[{i}] should be section 0"
            assert cues[i].section_type == "key_phrases"
        # Natural speed section
        for i in range(1 + kp_total, total):
            assert cues[i].section_index == 1, f"cue[{i}] should be section 1"
            assert cues[i].section_type == "natural_speed"

    def test_whitespace_variant_phrase_is_not_confused_by_text_match(self):
        """build_word_breakdown normalizes whitespace; the manifest must not
        text-match against lesson.key_phrases[k].phrase, so whitespace variants
        in the stored text are fine."""
        # Key phrase has leading space
        lesson = Lesson(
            title="T",
            language_code="sl",
            key_phrases=[_kp_phrase("  dober  dan  ")],
            sections=[
                Section(
                    section_type=SectionType.KEY_PHRASES,
                    phrases=[
                        Phrase(text="Key Phrases", voice_id="en-US-GuyNeural", language_code="en", role="narrator"),
                        # First L2 phrase is appended RAW (no normalize) — so it has internal spaces
                        Phrase(text="dober dan", voice_id="sl-SI-PetraNeural", language_code="sl", role="female-1"),
                        Phrase(text="good day", voice_id="en-US-GuyNeural", language_code="en", role="narrator"),
                        *[
                            Phrase(text=step, voice_id="sl-SI-PetraNeural", language_code="sl", role="female-1")
                            for step in build_word_breakdown("  dober  dan  ", "sl")
                        ],
                    ],
                )
            ],
        )
        n = len(build_word_breakdown("  dober  dan  ", "sl"))
        total = 1 + 2 + n
        timing = [
            CueTiming(section_index=0, phrase_index=i, start_frame=i * 1000, end_frame=(i + 1) * 1000)
            for i in range(total)
        ]
        cues = build_cue_manifest(lesson, timing, rate=1000)

        assert cues[0].ref == {"kind": "narration"}
        for i in range(1, total):
            assert cues[i].ref == {"kind": "key_phrase", "target_index": 0}


class TestBuildCueManifestTimingMath:
    """Frame-to-ms conversion and cue field correctness."""

    def test_frame_to_ms_conversion(self):
        """Offsets convert correctly from frames at a given rate."""
        lesson = Lesson(title="T", language_code="sl")
        timing = [CueTiming(section_index=None, phrase_index=0, start_frame=48000, end_frame=96000)]
        cues = build_cue_manifest(lesson, timing, rate=48000)
        assert cues[0].start_ms == 1000
        assert cues[0].end_ms == 2000

    def test_cue_index_is_chronological(self):
        """Cue.index increments from 0 in render order."""
        lesson = Lesson(
            title="T",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[
                        Phrase(text="Natural Speed", voice_id="en-US-GuyNeural", language_code="en", role="narrator"),
                        Phrase(text="Hvala", voice_id="sl-SI-PetraNeural", language_code="sl", role="female-1"),
                    ],
                )
            ],
        )
        timing = [
            CueTiming(section_index=None, phrase_index=0, start_frame=0, end_frame=1000),
            CueTiming(section_index=0, phrase_index=0, start_frame=5000, end_frame=6000),
            CueTiming(section_index=0, phrase_index=1, start_frame=10000, end_frame=11000),
        ]
        cues = build_cue_manifest(lesson, timing, rate=1000)
        for i, c in enumerate(cues):
            assert c.index == i


class TestBuildCueManifestEdgeCases:
    """Edge cases for branch coverage (unusual or defensive paths)."""

    def test_key_phrases_empty_timing_skips_title_check(self):
        """When key_phrases section has no timing entries, no crash."""
        lesson = Lesson(
            title="T",
            language_code="sl",
            key_phrases=[_kp_phrase("hvala")],
            sections=[Section(section_type=SectionType.KEY_PHRASES, phrases=[])],
        )
        timing: list[CueTiming] = [
            CueTiming(section_index=None, phrase_index=0, start_frame=0, end_frame=1000),
        ]
        # Only title timing, no section timing → section has no timing
        cues = build_cue_manifest(lesson, timing, rate=1000)
        assert len(cues) == 1  # only title

    def test_key_phrases_extra_phrases_after_all_consumed_raises(self):
        """When extra phrases exist after consuming all key phrases, raise."""
        lesson = Lesson(
            title="Test",
            language_code="sl",
            key_phrases=[_kp_phrase("hvala")],
            sections=[
                Section(
                    section_type=SectionType.KEY_PHRASES,
                    phrases=[
                        Phrase(text="Key Phrases", voice_id="en-US-GuyNeural", language_code="en", role="narrator"),
                        Phrase(text="hvala", voice_id="sl-SI-PetraNeural", language_code="sl", role="female-1"),
                        Phrase(text="thanks", voice_id="en-US-GuyNeural", language_code="en", role="narrator"),
                        *[
                            Phrase(text=step, voice_id="sl-SI-PetraNeural", language_code="sl", role="female-1")
                            for step in build_word_breakdown("hvala", "sl")
                        ],
                        # Extra phrase beyond what the builder produces
                        Phrase(text="extra", voice_id="sl-SI-PetraNeural", language_code="sl", role="female-1"),
                    ],
                )
            ],
        )
        n = len(build_word_breakdown("hvala", "sl"))
        total = 1 + 2 + n + 1  # title + L2 + trans + breakdown + extra
        timing = [
            CueTiming(section_index=0, phrase_index=i, start_frame=i * 1000, end_frame=(i + 1) * 1000)
            for i in range(total)
        ]
        with pytest.raises(ValueError, match="Key phrase phrase-count mismatch"):
            build_cue_manifest(lesson, timing, rate=1000)

    def test_key_phrases_first_timing_not_index_zero(self):
        """First timing entry with phrase_index != 0 skips title ref (defensive)."""
        lesson = Lesson(
            title="T",
            language_code="sl",
            key_phrases=[_kp_phrase("hvala")],
            sections=[
                Section(
                    section_type=SectionType.KEY_PHRASES,
                    phrases=[
                        Phrase(text="Key Phrases", voice_id="en-US-GuyNeural", language_code="en", role="narrator"),
                        Phrase(text="hvala", voice_id="sl-SI-PetraNeural", language_code="sl", role="female-1"),
                        Phrase(text="thanks", voice_id="en-US-GuyNeural", language_code="en", role="narrator"),
                        *[
                            Phrase(text=step, voice_id="sl-SI-PetraNeural", language_code="sl", role="female-1")
                            for step in build_word_breakdown("hvala", "sl")
                        ],
                    ],
                )
            ],
        )
        n = len(build_word_breakdown("hvala", "sl"))
        total = 1 + 2 + n
        # First section timing entry has phrase_index=1 (skipping section title)
        timing = [
            CueTiming(section_index=0, phrase_index=i, start_frame=i * 1000, end_frame=(i + 1) * 1000)
            for i in range(1, total)
        ]
        cues = build_cue_manifest(lesson, timing, rate=1000)
        # Title still exists via separate timing entry
        assert len(cues) == total - 1
