"""Tests for build_cue_manifest — pure cue-ref derivation from lesson + timing."""

import pytest

from app.audio.cues import CueTiming, build_cue_manifest
from app.generation.section_builder import build_key_phrases_section, build_word_breakdown
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
                        Phrase(text="English After", voice_id="en-US-GuyNeural", language_code="en", role="narrator"),
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
                        Phrase(text="Enunciated", voice_id="en-US-GuyNeural", language_code="en", role="narrator"),
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

    def test_slow_translated_narrator_following_l2_refs_same_line(self):
        """In slow_translated, a narrator cue immediately after an L2 cue refs that line."""
        lesson = Lesson(
            title="Test",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.SLOW_TRANSLATED,
                    phrases=[
                        Phrase(
                            text="Enunciated, English After",
                            voice_id="en-US-GuyNeural",
                            language_code="en",
                            role="narrator",
                        ),
                        Phrase(
                            text="Dober ... dan!", voice_id="sl-SI-PetraNeural", language_code="sl", role="female-1"
                        ),
                        Phrase(text="Good day!", voice_id="en-US-GuyNeural", language_code="en", role="narrator"),
                        Phrase(
                            text="Prosim ... kavo.", voice_id="sl-SI-PetraNeural", language_code="sl", role="female-1"
                        ),
                        Phrase(
                            text="A coffee please.", voice_id="en-US-GuyNeural", language_code="en", role="narrator"
                        ),
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

    def test_en_translated_narrator_preceding_l2_refs_same_line(self):
        """In en_translated the translation PRECEDES its L2 line and shares its line ref."""
        lesson = Lesson(
            title="Test",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.EN_TRANSLATED,
                    phrases=[
                        Phrase(text="English Before", voice_id="en-US-GuyNeural", language_code="en", role="narrator"),
                        Phrase(text="At the cafe", voice_id="en-US-GuyNeural", language_code="en", role="narrator"),
                        Phrase(text="Good day", voice_id="en-US-GuyNeural", language_code="en", role="narrator"),
                        Phrase(text="Dober dan", voice_id="sl-SI-PetraNeural", language_code="sl", role="female-1"),
                        Phrase(text="Thanks", voice_id="en-US-GuyNeural", language_code="en", role="narrator"),
                        Phrase(text="Hvala", voice_id="sl-SI-PetraNeural", language_code="sl", role="female-1"),
                    ],
                )
            ],
        )
        timing = [
            CueTiming(section_index=0, phrase_index=i, start_frame=i * 2000, end_frame=i * 2000 + 1000)
            for i in range(6)
        ]
        cues = build_cue_manifest(lesson, timing, rate=1000)

        assert cues[0].ref == {"kind": "narration"}  # section title
        assert cues[1].ref == {"kind": "narration"}  # scene label (narrator followed by narrator)
        assert cues[2].ref == {"kind": "line", "target_index": 0}  # EN translation precedes L2 line 0
        assert cues[3].ref == {"kind": "line", "target_index": 0}  # L2 line 0
        assert cues[4].ref == {"kind": "line", "target_index": 1}  # EN translation precedes L2 line 1
        assert cues[5].ref == {"kind": "line", "target_index": 1}  # L2 line 1

    def test_slow_en_translated_narrator_preceding_l2_refs_same_line(self):
        """slow_en_translated pairs the preceding translation with its slowed L2 line."""
        lesson = Lesson(
            title="Test",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.SLOW_EN_TRANSLATED,
                    phrases=[
                        Phrase(
                            text="Enunciated, English Before",
                            voice_id="en-US-GuyNeural",
                            language_code="en",
                            role="narrator",
                        ),
                        Phrase(text="Good day!", voice_id="en-US-GuyNeural", language_code="en", role="narrator"),
                        Phrase(
                            text="Dober ... dan!", voice_id="sl-SI-PetraNeural", language_code="sl", role="female-1"
                        ),
                    ],
                )
            ],
        )
        timing = [
            CueTiming(section_index=0, phrase_index=i, start_frame=i * 2000, end_frame=i * 2000 + 1000)
            for i in range(3)
        ]
        cues = build_cue_manifest(lesson, timing, rate=1000)

        assert cues[0].ref == {"kind": "narration"}  # section title
        assert cues[1].ref == {"kind": "line", "target_index": 0}  # EN translation precedes L2
        assert cues[2].ref == {"kind": "line", "target_index": 0}  # slowed L2 line 0

    def test_en_translated_trailing_narrator_gets_narration_ref(self):
        """An en_translated narrator with no following L2 line (e.g. a trailing scene label) is narration."""
        lesson = Lesson(
            title="Test",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.EN_TRANSLATED,
                    phrases=[
                        Phrase(text="English Before", voice_id="en-US-GuyNeural", language_code="en", role="narrator"),
                        Phrase(text="At the cafe", voice_id="en-US-GuyNeural", language_code="en", role="narrator"),
                    ],
                )
            ],
        )
        timing = [
            CueTiming(section_index=0, phrase_index=0, start_frame=0, end_frame=1000),
            CueTiming(section_index=0, phrase_index=1, start_frame=2000, end_frame=3000),
        ]
        cues = build_cue_manifest(lesson, timing, rate=1000)

        assert cues[0].ref == {"kind": "narration"}  # section title
        assert cues[1].ref == {"kind": "narration"}  # trailing narrator, no L2 after


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

    def test_key_phrase_with_no_breakdown_chunks_is_a_valid_group(self):
        """A group is head + translation; zero breakdown chunks is a legal shape.

        This test used to assert ``ValueError``, deriving the expected count as
        ``2 + len(build_word_breakdown("hvala", "sl"))``. The section is
        structurally sound — the L2 phrase at index 1 is followed by a narrator —
        so it is one group of length 2 against one declared key phrase, and the
        manifest builds.
        """
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
        total = 3
        timing = [
            CueTiming(section_index=0, phrase_index=i, start_frame=i * 1000, end_frame=(i + 1) * 1000)
            for i in range(total)
        ]
        cues = build_cue_manifest(lesson, timing, rate=1000)

        assert _target_indices(cues) == ["narration", 0, 0]

    def test_trailing_l2_phrase_is_a_chunk_of_the_last_group(self):
        """A trailing L2 phrase with no narrator after it is a chunk, not a leftover.

        This test used to assert ``ValueError`` for 'too many phrases'. A head is
        defined by having a narrator SUCCESSOR, so the final L2 phrase cannot be
        one: it belongs to the last group. That is exactly how a stored section
        carrying one more chunk than today's rules would read.
        """
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
        cues = build_cue_manifest(lesson, timing, rate=1000)

        assert _target_indices(cues) == ["narration", 0, 0, 0]


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

    def test_extra_chunk_after_a_complete_breakdown_is_kept(self):
        """A full builder breakdown plus one extra chunk is one longer group.

        This test used to assert ``ValueError`` for phrases 'left over after
        consuming all key phrases'. The trailing L2 phrase cannot be a head, so
        the group simply runs one phrase longer — the shape a section stored
        under an older, chunk-richer rule has.
        """
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
        cues = build_cue_manifest(lesson, timing, rate=1000)

        assert _target_indices(cues) == ["narration"] + [0] * (2 + n + 1)

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


# ---------------------------------------------------------------------------
# A stored KEY_PHRASES section is a SNAPSHOT taken at generation time. Anything
# that re-derives how many phrases a key phrase occupies — by re-running
# today's breakdown rules over its text — silently assumes the rules have not
# changed since. They have (measured 2026-09-29: 8 of 11 stored Norwegian
# lessons fail today's arithmetic), and the failure lands as a ValueError
# AFTER all the TTS is done. These tests pin the structural segmentation
# instead: a group is delimited by the section's own shape.
# ---------------------------------------------------------------------------

_NO_KP_PHRASES = [
    {"phrase": "sporet er kaldt", "translation": "the track is cold"},
    {"phrase": "jeg vil ha en kaffe", "translation": "I would like a coffee"},
]


def _no_lesson() -> tuple[Lesson, Section]:
    """A Norwegian lesson whose KEY_PHRASES section is built by the real builder."""
    section = build_key_phrases_section(_NO_KP_PHRASES, {"female-1": "nb-NO-PernilleNeural"}, "en-US-GuyNeural", "no")
    lesson = Lesson(
        title="Day 1",
        language_code="no",
        sections=[section],
        key_phrases=[KeyPhraseInfo(phrase=kp["phrase"], translation=kp["translation"]) for kp in _NO_KP_PHRASES],
    )
    return lesson, section


def _timing_for(section: Section) -> list[CueTiming]:
    """One timing entry per phrase, in order."""
    return [
        CueTiming(section_index=0, phrase_index=i, start_frame=i * 1000, end_frame=(i + 1) * 1000)
        for i in range(len(section.phrases))
    ]


def _target_indices(cues) -> list[int | str]:
    """A cue's ref target, or "narration" for the section title."""
    return ["narration" if c.ref == {"kind": "narration"} else c.ref["target_index"] for c in cues]


class TestStoredSectionIsNotRederived:
    """Cues are segmented by the stored section's structure, not by today's rules."""

    def test_freshly_built_section_matches_literal_refs(self):
        """A5 — behaviour preservation, pinned against LITERALS not against the new code.

        ``build_key_phrases_section`` emits title + (L2, translation, chunks…) per
        key phrase: group 0 is phrases 1..10 and group 1 is phrases 11..24.
        """
        lesson, section = _no_lesson()
        assert len(section.phrases) == 25

        cues = build_cue_manifest(lesson, _timing_for(section), rate=1000)

        assert _target_indices(cues) == (["narration"] + [0] * 10 + [1] * 14), (
            "group 0 is phrases 1..10 and group 1 is phrases 11..24 — assert the FULL list, not a count"
        )

    def test_missing_breakdown_chunk_does_not_break_the_manifest(self):
        """A1 — a chunk DELETED from group 0 (a lesson stored under older rules).

        Group 0 becomes phrases 1..9, group 1 phrases 10..23. The old arithmetic
        demanded 10 for key_phrase[0] and raised here, after all the TTS.
        """
        lesson, section = _no_lesson()
        del section.phrases[5]  # an L2 breakdown chunk inside group 0
        assert len(section.phrases) == 24

        cues = build_cue_manifest(lesson, _timing_for(section), rate=1000)

        assert _target_indices(cues) == ["narration"] + [0] * 9 + [1] * 14

    def test_extra_breakdown_chunk_does_not_break_the_manifest(self):
        """A1 mirror — a chunk INSERTED into group 0.

        Group 0 becomes phrases 1..11, group 1 phrases 12..25. The extra chunk is
        plain L2, and L2 is never followed by a narrator phrase, so it stays a
        chunk of group 0 rather than being mistaken for a group head.
        """
        lesson, section = _no_lesson()
        section.phrases.insert(6, Phrase(text="kald", voice_id="nb-NO-PernilleNeural", language_code="no"))
        assert len(section.phrases) == 26

        cues = build_cue_manifest(lesson, _timing_for(section), rate=1000)

        assert _target_indices(cues) == ["narration"] + [0] * 11 + [1] * 14


class TestGroupCountMismatchStillRaises:
    """A structural inconsistency must stay LOUD — only the arithmetic moved."""

    def test_more_groups_than_key_phrases_raises(self):
        """A3(a) — the section holds 2 groups, the lesson declares 1 key phrase."""
        lesson, section = _no_lesson()
        lesson.key_phrases = lesson.key_phrases[:1]

        with pytest.raises(ValueError, match="^Key phrase phrase-count mismatch"):
            build_cue_manifest(lesson, _timing_for(section), rate=1000)

    def test_more_key_phrases_than_groups_raises(self):
        """A3(b) — the section holds 1 group, the lesson declares 2 key phrases."""
        lesson, section = _no_lesson()
        del section.phrases[10:]

        with pytest.raises(ValueError, match="^Key phrase phrase-count mismatch"):
            build_cue_manifest(lesson, _timing_for(section), rate=1000)

    def test_timing_entry_outside_every_group_raises(self):
        """An L2 phrase sitting BEFORE the first group head is in no group at all.

        Phrase 1 ('stray') is L2 but is followed by another L2 phrase, so it is
        not a head; phrase 2 is the first head. A timing entry for phrase 1 has
        nowhere to attach and is an inconsistency, not a silent skip.
        """
        lesson, section = _no_lesson()
        section.phrases.insert(1, Phrase(text="stray", voice_id="nb-NO-PernilleNeural", language_code="no"))
        section.phrases.insert(2, Phrase(text="sporet er kaldt", voice_id="nb-NO-PernilleNeural", language_code="no"))

        with pytest.raises(ValueError, match="^Key phrase phrase-count mismatch"):
            build_cue_manifest(lesson, _timing_for(section), rate=1000)
