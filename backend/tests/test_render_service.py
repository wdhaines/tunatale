"""Tests for render_service pure helpers — per-section cue derivation."""

from app.audio.cues import Cue
from app.audio.render_service import derive_section_cues
from app.models.lesson import Lesson, Phrase, Section, SectionType


def _cue(
    *,
    index: int = 0,
    start_ms: int = 0,
    end_ms: int = 1000,
    section_index: int | None = 0,
    section_type: str = "natural_speed",
    phrase_index: int = 0,
    role: str = "female-1",
    language_code: str = "sl",
    text: str = "text",
    ref: dict | None = None,
) -> Cue:
    return Cue(
        index=index,
        start_ms=start_ms,
        end_ms=end_ms,
        section_index=section_index,
        section_type=section_type,
        phrase_index=phrase_index,
        role=role,
        language_code=language_code,
        text=text,
        ref=ref or {"kind": "line", "target_index": 0},
    )


class TestDeriveSectionCuesRebasing:
    """Per-section cues are rebased to start at frame 0."""

    def test_single_section_rebased_to_zero(self):
        """A section whose cues start at 5000ms gets rebased so first cue starts at 0ms."""
        lesson = Lesson(
            title="T",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[
                        Phrase(text="Natural Speed", voice_id="n", language_code="en", role="narrator"),
                        Phrase(text="Dober dan", voice_id="v", language_code="sl", role="f1"),
                    ],
                )
            ],
        )
        cues = [
            _cue(
                index=0,
                start_ms=5000,
                end_ms=6000,
                section_index=0,
                section_type="natural_speed",
                phrase_index=0,
                role="narrator",
                language_code="en",
                text="Natural Speed",
                ref={"kind": "narration"},
            ),
            _cue(
                index=1,
                start_ms=7000,
                end_ms=8000,
                section_index=0,
                section_type="natural_speed",
                phrase_index=1,
                role="f1",
                language_code="sl",
                text="Dober dan",
                ref={"kind": "line", "target_index": 0},
            ),
        ]
        result = derive_section_cues(cues, lesson)
        assert 0 in result
        assert len(result[0]) == 2
        assert result[0][0].start_ms == 0
        assert result[0][0].end_ms == 1000
        assert result[0][1].start_ms == 2000
        assert result[0][1].end_ms == 3000

    def test_title_cue_excluded(self):
        """Cues with section_index=None (lesson title) are excluded."""
        lesson = Lesson(
            title="T",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[
                        Phrase(text="Natural Speed", voice_id="n", language_code="en", role="narrator"),
                    ],
                )
            ],
        )
        cues = [
            _cue(
                index=0,
                start_ms=0,
                end_ms=1000,
                section_index=None,
                section_type=None,
                phrase_index=0,
                role="narrator",
                language_code="en",
                text="T",
                ref={"kind": "narration"},
            ),
            _cue(
                index=1,
                start_ms=2000,
                end_ms=3000,
                section_index=0,
                section_type="natural_speed",
                phrase_index=0,
                role="narrator",
                language_code="en",
                text="Natural Speed",
                ref={"kind": "narration"},
            ),
        ]
        result = derive_section_cues(cues, lesson)
        assert None not in result
        assert 0 in result
        assert len(result[0]) == 1


class TestDeriveSectionCuesOverwrite:
    """For slow_speed/slow_translated, L2 line cue text is overwritten with natural text."""

    def test_slow_speed_overwrites_with_natural_text(self):
        """slow_speed L2 line cues get the natural_speed text, not the ellipsis text."""
        lesson = Lesson(
            title="T",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[
                        Phrase(text="Natural Speed", voice_id="n", language_code="en", role="narrator"),
                        Phrase(text="Dober dan", voice_id="v", language_code="sl", role="f1"),
                    ],
                ),
                Section(
                    section_type=SectionType.SLOW_SPEED,
                    phrases=[
                        Phrase(text="Slow Speed", voice_id="n", language_code="en", role="narrator"),
                        Phrase(text="Dober ... dan", voice_id="v", language_code="sl", role="f1"),
                    ],
                ),
            ],
        )
        cues = [
            # natural_speed section (index 0)
            _cue(
                index=0,
                start_ms=0,
                end_ms=1000,
                section_index=0,
                section_type="natural_speed",
                phrase_index=0,
                role="narrator",
                language_code="en",
                text="Natural Speed",
                ref={"kind": "narration"},
            ),
            _cue(
                index=1,
                start_ms=2000,
                end_ms=3000,
                section_index=0,
                section_type="natural_speed",
                phrase_index=1,
                role="f1",
                language_code="sl",
                text="Dober dan",
                ref={"kind": "line", "target_index": 0},
            ),
            # slow_speed section (index 1)
            _cue(
                index=2,
                start_ms=5000,
                end_ms=6000,
                section_index=1,
                section_type="slow_speed",
                phrase_index=0,
                role="narrator",
                language_code="en",
                text="Slow Speed",
                ref={"kind": "narration"},
            ),
            _cue(
                index=3,
                start_ms=7000,
                end_ms=8000,
                section_index=1,
                section_type="slow_speed",
                phrase_index=1,
                role="f1",
                language_code="sl",
                text="Dober ... dan",
                ref={"kind": "line", "target_index": 0},
            ),
        ]
        result = derive_section_cues(cues, lesson)
        slow_cues = result[1]
        l2_cues = [c for c in slow_cues if c.ref and c.ref.get("kind") == "line"]
        assert len(l2_cues) == 1
        assert l2_cues[0].text == "Dober dan"

    def test_slow_translated_overwrites_with_natural_text(self):
        """slow_translated L2 line cues get the natural text from the translated section."""
        lesson = Lesson(
            title="T",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[
                        Phrase(text="Natural Speed", voice_id="n", language_code="en", role="narrator"),
                        Phrase(text="Dober dan", voice_id="v", language_code="sl", role="f1"),
                    ],
                ),
                Section(
                    section_type=SectionType.TRANSLATED,
                    phrases=[
                        Phrase(text="Translated", voice_id="n", language_code="en", role="narrator"),
                        Phrase(text="Dober dan", voice_id="v", language_code="sl", role="f1"),
                        Phrase(text="Good day!", voice_id="n", language_code="en", role="narrator"),
                    ],
                ),
                Section(
                    section_type=SectionType.SLOW_TRANSLATED,
                    phrases=[
                        Phrase(text="Slow Translated", voice_id="n", language_code="en", role="narrator"),
                        Phrase(text="Dober ... dan", voice_id="v", language_code="sl", role="f1"),
                        Phrase(text="Good day!", voice_id="n", language_code="en", role="narrator"),
                    ],
                ),
            ],
        )
        cues = [
            # natural_speed section (index 0)
            _cue(
                index=0,
                start_ms=0,
                end_ms=1000,
                section_index=0,
                section_type="natural_speed",
                phrase_index=0,
                role="narrator",
                language_code="en",
                text="Natural Speed",
                ref={"kind": "narration"},
            ),
            _cue(
                index=1,
                start_ms=2000,
                end_ms=3000,
                section_index=0,
                section_type="natural_speed",
                phrase_index=1,
                role="f1",
                language_code="sl",
                text="Dober dan",
                ref={"kind": "line", "target_index": 0},
            ),
            # translated section (index 1) — L2 cue carries natural text
            _cue(
                index=2,
                start_ms=4000,
                end_ms=5000,
                section_index=1,
                section_type="translated",
                phrase_index=0,
                role="narrator",
                language_code="en",
                text="Translated",
                ref={"kind": "narration"},
            ),
            _cue(
                index=3,
                start_ms=6000,
                end_ms=7000,
                section_index=1,
                section_type="translated",
                phrase_index=1,
                role="f1",
                language_code="sl",
                text="Dober dan",
                ref={"kind": "line", "target_index": 0},
            ),
            _cue(
                index=4,
                start_ms=8000,
                end_ms=9000,
                section_index=1,
                section_type="translated",
                phrase_index=2,
                role="narrator",
                language_code="en",
                text="Good day!",
                ref={"kind": "line", "target_index": 0},
            ),
            # slow_translated section (index 2)
            _cue(
                index=5,
                start_ms=10000,
                end_ms=11000,
                section_index=2,
                section_type="slow_translated",
                phrase_index=0,
                role="narrator",
                language_code="en",
                text="Slow Translated",
                ref={"kind": "narration"},
            ),
            _cue(
                index=6,
                start_ms=12000,
                end_ms=13000,
                section_index=2,
                section_type="slow_translated",
                phrase_index=1,
                role="f1",
                language_code="sl",
                text="Dober ... dan",
                ref={"kind": "line", "target_index": 0},
            ),
            _cue(
                index=7,
                start_ms=14000,
                end_ms=15000,
                section_index=2,
                section_type="slow_translated",
                phrase_index=2,
                role="narrator",
                language_code="en",
                text="Good day!",
                ref={"kind": "line", "target_index": 0},
            ),
        ]
        result = derive_section_cues(cues, lesson)
        slow_cues = result[2]
        l2_cues = [c for c in slow_cues if c.ref and c.ref.get("kind") == "line" and c.language_code == "sl"]
        assert len(l2_cues) == 1
        assert l2_cues[0].text == "Dober dan"
        # English narrator cue should NOT be overwritten
        en_cues = [c for c in slow_cues if c.ref and c.ref.get("kind") == "line" and c.language_code == "en"]
        assert len(en_cues) == 1
        assert en_cues[0].text == "Good day!"


class TestDeriveSectionCuesUntranslatedLineNoTranslation:
    """The A6 correction test: a line with text but no translation.

    In slow_translated, this line should NOT appear (it was skipped by
    the section_builder because it lacks a translation). The remaining
    line cues still carry the correct natural text via target_index alignment.
    """

    def test_slow_translated_untranslated_line_remaining_cues_correct(self):
        """One dialogue line has text but no translation; slow_translated's
        remaining line cues still carry the correct natural text."""
        lesson = Lesson(
            title="T",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[
                        Phrase(text="Natural Speed", voice_id="n", language_code="en", role="narrator"),
                        Phrase(text="Dober dan", voice_id="v", language_code="sl", role="f1"),
                        Phrase(text="Kava prosim", voice_id="v", language_code="sl", role="f1"),
                    ],
                ),
                Section(
                    section_type=SectionType.TRANSLATED,
                    phrases=[
                        Phrase(text="Translated", voice_id="n", language_code="en", role="narrator"),
                        Phrase(text="Dober dan", voice_id="v", language_code="sl", role="f1"),
                        Phrase(text="Good day!", voice_id="n", language_code="en", role="narrator"),
                        # Line 2 (Kava prosim) has no translation → skipped by section_builder
                    ],
                ),
                Section(
                    section_type=SectionType.SLOW_TRANSLATED,
                    phrases=[
                        Phrase(text="Slow Translated", voice_id="n", language_code="en", role="narrator"),
                        Phrase(text="Dober ... dan", voice_id="v", language_code="sl", role="f1"),
                        Phrase(text="Good day!", voice_id="n", language_code="en", role="narrator"),
                        # Line 2 (Kava prosim) also skipped here
                    ],
                ),
            ],
        )
        cues = [
            # natural_speed (section 0): 3 cues (title + 2 L2 lines)
            _cue(
                index=0,
                start_ms=0,
                end_ms=1000,
                section_index=0,
                section_type="natural_speed",
                phrase_index=0,
                role="narrator",
                language_code="en",
                text="Natural Speed",
                ref={"kind": "narration"},
            ),
            _cue(
                index=1,
                start_ms=2000,
                end_ms=3000,
                section_index=0,
                section_type="natural_speed",
                phrase_index=1,
                role="f1",
                language_code="sl",
                text="Dober dan",
                ref={"kind": "line", "target_index": 0},
            ),
            _cue(
                index=2,
                start_ms=4000,
                end_ms=5000,
                section_index=0,
                section_type="natural_speed",
                phrase_index=2,
                role="f1",
                language_code="sl",
                text="Kava prosim",
                ref={"kind": "line", "target_index": 1},
            ),
            # translated (section 1): title + L2(line0) + EN(line0) = 3 cues
            _cue(
                index=3,
                start_ms=6000,
                end_ms=7000,
                section_index=1,
                section_type="translated",
                phrase_index=0,
                role="narrator",
                language_code="en",
                text="Translated",
                ref={"kind": "narration"},
            ),
            _cue(
                index=4,
                start_ms=8000,
                end_ms=9000,
                section_index=1,
                section_type="translated",
                phrase_index=1,
                role="f1",
                language_code="sl",
                text="Dober dan",
                ref={"kind": "line", "target_index": 0},
            ),
            _cue(
                index=5,
                start_ms=10000,
                end_ms=11000,
                section_index=1,
                section_type="translated",
                phrase_index=2,
                role="narrator",
                language_code="en",
                text="Good day!",
                ref={"kind": "line", "target_index": 0},
            ),
            # slow_translated (section 2): title + L2(line0) + EN(line0) = 3 cues
            _cue(
                index=6,
                start_ms=12000,
                end_ms=13000,
                section_index=2,
                section_type="slow_translated",
                phrase_index=0,
                role="narrator",
                language_code="en",
                text="Slow Translated",
                ref={"kind": "narration"},
            ),
            _cue(
                index=7,
                start_ms=14000,
                end_ms=15000,
                section_index=2,
                section_type="slow_translated",
                phrase_index=1,
                role="f1",
                language_code="sl",
                text="Dober ... dan",
                ref={"kind": "line", "target_index": 0},
            ),
            _cue(
                index=8,
                start_ms=16000,
                end_ms=17000,
                section_index=2,
                section_type="slow_translated",
                phrase_index=2,
                role="narrator",
                language_code="en",
                text="Good day!",
                ref={"kind": "line", "target_index": 0},
            ),
        ]
        result = derive_section_cues(cues, lesson)

        # slow_translated section
        slow_cues = result[2]
        l2_cues = [c for c in slow_cues if c.ref and c.ref.get("kind") == "line" and c.language_code == "sl"]
        assert len(l2_cues) == 1
        # The L2 line cue should have natural text, never "..."
        assert l2_cues[0].text == "Dober dan"
        assert "..." not in l2_cues[0].text
        # English narrator cue should be untouched
        en_cues = [c for c in slow_cues if c.ref and c.ref.get("kind") == "line" and c.language_code == "en"]
        assert en_cues[0].text == "Good day!"

    def test_slow_translated_no_natural_speed_section_uses_translated(self):
        """When there's no natural_speed section (edge case), slow_translated
        falls back to translated for its text source."""
        lesson = Lesson(
            title="T",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.TRANSLATED,
                    phrases=[
                        Phrase(text="Translated", voice_id="n", language_code="en", role="narrator"),
                        Phrase(text="Dober dan", voice_id="v", language_code="sl", role="f1"),
                        Phrase(text="Good day!", voice_id="n", language_code="en", role="narrator"),
                    ],
                ),
                Section(
                    section_type=SectionType.SLOW_TRANSLATED,
                    phrases=[
                        Phrase(text="Slow Translated", voice_id="n", language_code="en", role="narrator"),
                        Phrase(text="Dober ... dan", voice_id="v", language_code="sl", role="f1"),
                        Phrase(text="Good day!", voice_id="n", language_code="en", role="narrator"),
                    ],
                ),
            ],
        )
        cues = [
            _cue(
                index=0,
                start_ms=0,
                end_ms=1000,
                section_index=0,
                section_type="translated",
                phrase_index=0,
                role="narrator",
                language_code="en",
                text="Translated",
                ref={"kind": "narration"},
            ),
            _cue(
                index=1,
                start_ms=2000,
                end_ms=3000,
                section_index=0,
                section_type="translated",
                phrase_index=1,
                role="f1",
                language_code="sl",
                text="Dober dan",
                ref={"kind": "line", "target_index": 0},
            ),
            _cue(
                index=2,
                start_ms=4000,
                end_ms=5000,
                section_index=0,
                section_type="translated",
                phrase_index=2,
                role="narrator",
                language_code="en",
                text="Good day!",
                ref={"kind": "line", "target_index": 0},
            ),
            _cue(
                index=3,
                start_ms=6000,
                end_ms=7000,
                section_index=1,
                section_type="slow_translated",
                phrase_index=0,
                role="narrator",
                language_code="en",
                text="Slow Translated",
                ref={"kind": "narration"},
            ),
            _cue(
                index=4,
                start_ms=8000,
                end_ms=9000,
                section_index=1,
                section_type="slow_translated",
                phrase_index=1,
                role="f1",
                language_code="sl",
                text="Dober ... dan",
                ref={"kind": "line", "target_index": 0},
            ),
            _cue(
                index=5,
                start_ms=10000,
                end_ms=11000,
                section_index=1,
                section_type="slow_translated",
                phrase_index=2,
                role="narrator",
                language_code="en",
                text="Good day!",
                ref={"kind": "line", "target_index": 0},
            ),
        ]
        result = derive_section_cues(cues, lesson)
        slow_cues = result[1]
        l2_cues = [c for c in slow_cues if c.ref and c.ref.get("kind") == "line" and c.language_code == "sl"]
        assert l2_cues[0].text == "Dober dan"

    def test_untranslated_line_middle_discriminates_source(self):
        """Slow_translated scrub source must be translated, not natural_speed.

        Three lines: A, B (untranslated → skipped), C.
        In natural_speed:  A→0, B→1, C→2
        In translated:     A→0, C→1  (B skipped)
        In slow_translated: A→0, C→1

        If scrubbed from natural_speed, C at target_index 1 would get B's text.
        If scrubbed from translated, C at target_index 1 gets C's text.
        This test must go red if _SLOW_TEXT_SOURCE[SLOW_TRANSLATED] = NATURAL_SPEED.
        """
        lesson = Lesson(
            title="T",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[
                        Phrase(text="Natural Speed", voice_id="n", language_code="en", role="narrator"),
                        Phrase(text="Dober dan", voice_id="v", language_code="sl", role="f1"),
                        Phrase(text="Hvala lepa", voice_id="v", language_code="sl", role="f1"),
                        Phrase(text="Adijo", voice_id="v", language_code="sl", role="f1"),
                    ],
                ),
                Section(
                    section_type=SectionType.TRANSLATED,
                    phrases=[
                        Phrase(text="Translated", voice_id="n", language_code="en", role="narrator"),
                        Phrase(text="Dober dan", voice_id="v", language_code="sl", role="f1"),
                        Phrase(text="Good day!", voice_id="n", language_code="en", role="narrator"),
                        # Line B "Hvala lepa" has no translation → skipped
                        Phrase(text="Adijo", voice_id="v", language_code="sl", role="f1"),
                        Phrase(text="Goodbye!", voice_id="n", language_code="en", role="narrator"),
                    ],
                ),
                Section(
                    section_type=SectionType.SLOW_TRANSLATED,
                    phrases=[
                        Phrase(text="Slow Translated", voice_id="n", language_code="en", role="narrator"),
                        Phrase(text="Dober ... dan", voice_id="v", language_code="sl", role="f1"),
                        Phrase(text="Good day!", voice_id="n", language_code="en", role="f1"),
                        Phrase(text="A ... dijo", voice_id="v", language_code="sl", role="f1"),
                        Phrase(text="Goodbye!", voice_id="n", language_code="en", role="f1"),
                    ],
                ),
            ],
        )
        cues = [
            # natural_speed (section 0): 3 L2 lines → target_index 0, 1, 2
            _cue(
                index=0,
                start_ms=0,
                end_ms=1000,
                section_index=0,
                section_type="natural_speed",
                phrase_index=0,
                role="narrator",
                language_code="en",
                text="Natural Speed",
                ref={"kind": "narration"},
            ),
            _cue(
                index=1,
                start_ms=2000,
                end_ms=3000,
                section_index=0,
                section_type="natural_speed",
                phrase_index=1,
                role="f1",
                language_code="sl",
                text="Dober dan",
                ref={"kind": "line", "target_index": 0},
            ),
            _cue(
                index=2,
                start_ms=4000,
                end_ms=5000,
                section_index=0,
                section_type="natural_speed",
                phrase_index=2,
                role="f1",
                language_code="sl",
                text="Hvala lepa",
                ref={"kind": "line", "target_index": 1},
            ),
            _cue(
                index=3,
                start_ms=6000,
                end_ms=7000,
                section_index=0,
                section_type="natural_speed",
                phrase_index=3,
                role="f1",
                language_code="sl",
                text="Adijo",
                ref={"kind": "line", "target_index": 2},
            ),
            # translated (section 1): B skipped → 2 L2 lines at target_index 0, 1
            _cue(
                index=4,
                start_ms=8000,
                end_ms=9000,
                section_index=1,
                section_type="translated",
                phrase_index=0,
                role="narrator",
                language_code="en",
                text="Translated",
                ref={"kind": "narration"},
            ),
            _cue(
                index=5,
                start_ms=10000,
                end_ms=11000,
                section_index=1,
                section_type="translated",
                phrase_index=1,
                role="f1",
                language_code="sl",
                text="Dober dan",
                ref={"kind": "line", "target_index": 0},
            ),
            _cue(
                index=6,
                start_ms=12000,
                end_ms=13000,
                section_index=1,
                section_type="translated",
                phrase_index=2,
                role="narrator",
                language_code="en",
                text="Good day!",
                ref={"kind": "line", "target_index": 0},
            ),
            _cue(
                index=7,
                start_ms=14000,
                end_ms=15000,
                section_index=1,
                section_type="translated",
                phrase_index=3,
                role="f1",
                language_code="sl",
                text="Adijo",
                ref={"kind": "line", "target_index": 1},
            ),
            _cue(
                index=8,
                start_ms=16000,
                end_ms=17000,
                section_index=1,
                section_type="translated",
                phrase_index=4,
                role="narrator",
                language_code="en",
                text="Goodbye!",
                ref={"kind": "line", "target_index": 1},
            ),
            # slow_translated (section 2): same target_index layout as translated (0, 1)
            _cue(
                index=9,
                start_ms=18000,
                end_ms=19000,
                section_index=2,
                section_type="slow_translated",
                phrase_index=0,
                role="narrator",
                language_code="en",
                text="Slow Translated",
                ref={"kind": "narration"},
            ),
            _cue(
                index=10,
                start_ms=20000,
                end_ms=21000,
                section_index=2,
                section_type="slow_translated",
                phrase_index=1,
                role="f1",
                language_code="sl",
                text="Dober ... dan",
                ref={"kind": "line", "target_index": 0},
            ),
            _cue(
                index=11,
                start_ms=22000,
                end_ms=23000,
                section_index=2,
                section_type="slow_translated",
                phrase_index=2,
                role="f1",
                language_code="sl",
                text="A ... dijo",
                ref={"kind": "line", "target_index": 1},
            ),
        ]
        result = derive_section_cues(cues, lesson)
        slow_cues = result[2]
        l2_by_target = {
            c.ref["target_index"]: c
            for c in slow_cues
            if c.ref and c.ref.get("kind") == "line" and c.language_code == "sl"
        }
        assert l2_by_target[0].text == "Dober dan"
        # This is the discriminator: target_index 1 in translated is "Adijo" (C),
        # not "Hvala lepa" (B).  If the scrub source were natural_speed,
        # target_index 1 would map to B's text and this assertion would fail.
        assert l2_by_target[1].text == "Adijo"


class TestDeriveSectionCuesExactness:
    """Verify exact rebasing math and that narrator cues in slow sections are untouched."""

    def test_rebasing_is_exact_subtraction(self):
        """Each cue's start/end is the original minus the group's first cue start."""
        lesson = Lesson(
            title="T",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[
                        Phrase(text="Natural Speed", voice_id="n", language_code="en", role="narrator"),
                        Phrase(text="A", voice_id="v", language_code="sl", role="f1"),
                        Phrase(text="B", voice_id="v", language_code="sl", role="f1"),
                    ],
                )
            ],
        )
        cues = [
            _cue(
                index=0,
                start_ms=10000,
                end_ms=11000,
                section_index=0,
                section_type="natural_speed",
                phrase_index=0,
                role="narrator",
                language_code="en",
                text="Natural Speed",
                ref={"kind": "narration"},
            ),
            _cue(
                index=1,
                start_ms=12000,
                end_ms=13500,
                section_index=0,
                section_type="natural_speed",
                phrase_index=1,
                role="f1",
                language_code="sl",
                text="A",
                ref={"kind": "line", "target_index": 0},
            ),
            _cue(
                index=2,
                start_ms=15000,
                end_ms=16000,
                section_index=0,
                section_type="natural_speed",
                phrase_index=2,
                role="f1",
                language_code="sl",
                text="B",
                ref={"kind": "line", "target_index": 1},
            ),
        ]
        result = derive_section_cues(cues, lesson)
        sec = result[0]
        assert sec[0].start_ms == 0
        assert sec[0].end_ms == 1000
        assert sec[1].start_ms == 2000
        assert sec[1].end_ms == 3500
        assert sec[2].start_ms == 5000
        assert sec[2].end_ms == 6000

    def test_slow_speed_narrator_cue_text_unchanged(self):
        """Narrator cues (section title, scene labels) are not overwritten."""
        lesson = Lesson(
            title="T",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[
                        Phrase(text="Natural Speed", voice_id="n", language_code="en", role="narrator"),
                        Phrase(text="Dober dan", voice_id="v", language_code="sl", role="f1"),
                    ],
                ),
                Section(
                    section_type=SectionType.SLOW_SPEED,
                    phrases=[
                        Phrase(text="Slow Speed", voice_id="n", language_code="en", role="narrator"),
                        Phrase(text="Dober ... dan", voice_id="v", language_code="sl", role="f1"),
                    ],
                ),
            ],
        )
        cues = [
            _cue(
                index=0,
                start_ms=0,
                end_ms=1000,
                section_index=0,
                section_type="natural_speed",
                phrase_index=0,
                role="narrator",
                language_code="en",
                text="Natural Speed",
                ref={"kind": "narration"},
            ),
            _cue(
                index=1,
                start_ms=2000,
                end_ms=3000,
                section_index=0,
                section_type="natural_speed",
                phrase_index=1,
                role="f1",
                language_code="sl",
                text="Dober dan",
                ref={"kind": "line", "target_index": 0},
            ),
            _cue(
                index=2,
                start_ms=5000,
                end_ms=6000,
                section_index=1,
                section_type="slow_speed",
                phrase_index=0,
                role="narrator",
                language_code="en",
                text="Slow Speed",
                ref={"kind": "narration"},
            ),
            _cue(
                index=3,
                start_ms=7000,
                end_ms=8000,
                section_index=1,
                section_type="slow_speed",
                phrase_index=1,
                role="f1",
                language_code="sl",
                text="Dober ... dan",
                ref={"kind": "line", "target_index": 0},
            ),
        ]
        result = derive_section_cues(cues, lesson)
        slow_cues = result[1]
        narrator_cues = [c for c in slow_cues if c.ref and c.ref.get("kind") == "narration"]
        assert narrator_cues[0].text == "Slow Speed"


class TestDeriveSectionCuesEdgeCases:
    """Branch coverage for uncovered paths in derive_section_cues."""

    def test_slow_section_with_no_twin(self):
        """Slow section whose twin section type is absent → no scrub map."""
        lesson = Lesson(
            title="T",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.SLOW_SPEED,
                    phrases=[
                        Phrase(text="Slow Speed", voice_id="n", language_code="en", role="narrator"),
                        Phrase(text="Dober ... dan", voice_id="v", language_code="sl", role="f1"),
                    ],
                ),
            ],
        )
        cues = [
            _cue(
                index=0,
                start_ms=0,
                end_ms=1000,
                section_index=0,
                section_type="slow_speed",
                phrase_index=0,
                role="narrator",
                language_code="en",
                text="Slow Speed",
                ref={"kind": "narration"},
            ),
            _cue(
                index=1,
                start_ms=2000,
                end_ms=3000,
                section_index=0,
                section_type="slow_speed",
                phrase_index=1,
                role="f1",
                language_code="sl",
                text="Dober ... dan",
                ref={"kind": "line", "target_index": 0},
            ),
        ]
        result = derive_section_cues(cues, lesson)
        assert len(result[0]) == 2
        l2 = [c for c in result[0] if c.ref and c.ref.get("kind") == "line" and c.language_code == "sl"]
        assert l2[0].text == "Dober ... dan"

    def test_twin_has_no_l2_line_cues(self):
        """Slow section's twin exists but has no L2 line cues → empty scrub map."""
        lesson = Lesson(
            title="T",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[
                        Phrase(text="Natural Speed", voice_id="n", language_code="en", role="narrator"),
                    ],
                ),
                Section(
                    section_type=SectionType.SLOW_SPEED,
                    phrases=[
                        Phrase(text="Slow Speed", voice_id="n", language_code="en", role="narrator"),
                        Phrase(text="Dober ... dan", voice_id="v", language_code="sl", role="f1"),
                    ],
                ),
            ],
        )
        cues = [
            _cue(
                index=0,
                start_ms=0,
                end_ms=1000,
                section_index=0,
                section_type="natural_speed",
                phrase_index=0,
                role="narrator",
                language_code="en",
                text="Natural Speed",
                ref={"kind": "narration"},
            ),
            _cue(
                index=1,
                start_ms=5000,
                end_ms=6000,
                section_index=1,
                section_type="slow_speed",
                phrase_index=0,
                role="narrator",
                language_code="en",
                text="Slow Speed",
                ref={"kind": "narration"},
            ),
            _cue(
                index=2,
                start_ms=7000,
                end_ms=8000,
                section_index=1,
                section_type="slow_speed",
                phrase_index=1,
                role="f1",
                language_code="sl",
                text="Dober ... dan",
                ref={"kind": "line", "target_index": 0},
            ),
        ]
        result = derive_section_cues(cues, lesson)
        l2 = [c for c in result[1] if c.ref and c.ref.get("kind") == "line" and c.language_code == "sl"]
        assert l2[0].text == "Dober ... dan"

    def test_target_index_not_in_scrub_map(self):
        """Slow section cue with target_index absent from scrub map → text preserved."""
        lesson = Lesson(
            title="T",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[
                        Phrase(text="Natural Speed", voice_id="n", language_code="en", role="narrator"),
                        Phrase(text="Dober dan", voice_id="v", language_code="sl", role="f1"),
                    ],
                ),
                Section(
                    section_type=SectionType.SLOW_SPEED,
                    phrases=[
                        Phrase(text="Slow Speed", voice_id="n", language_code="en", role="narrator"),
                        Phrase(text="Dober ... dan", voice_id="v", language_code="sl", role="f1"),
                        Phrase(text="Kava ... prosim", voice_id="v", language_code="sl", role="f1"),
                    ],
                ),
            ],
        )
        cues = [
            _cue(
                index=0,
                start_ms=0,
                end_ms=1000,
                section_index=0,
                section_type="natural_speed",
                phrase_index=0,
                role="narrator",
                language_code="en",
                text="Natural Speed",
                ref={"kind": "narration"},
            ),
            _cue(
                index=1,
                start_ms=2000,
                end_ms=3000,
                section_index=0,
                section_type="natural_speed",
                phrase_index=1,
                role="f1",
                language_code="sl",
                text="Dober dan",
                ref={"kind": "line", "target_index": 0},
            ),
            _cue(
                index=2,
                start_ms=5000,
                end_ms=6000,
                section_index=1,
                section_type="slow_speed",
                phrase_index=0,
                role="narrator",
                language_code="en",
                text="Slow Speed",
                ref={"kind": "narration"},
            ),
            _cue(
                index=3,
                start_ms=7000,
                end_ms=8000,
                section_index=1,
                section_type="slow_speed",
                phrase_index=1,
                role="f1",
                language_code="sl",
                text="Dober ... dan",
                ref={"kind": "line", "target_index": 0},
            ),
            _cue(
                index=4,
                start_ms=9000,
                end_ms=10000,
                section_index=1,
                section_type="slow_speed",
                phrase_index=2,
                role="f1",
                language_code="sl",
                text="Kava ... prosim",
                ref={"kind": "line", "target_index": 1},
            ),
        ]
        result = derive_section_cues(cues, lesson)
        slow_cues = result[1]
        l2 = [c for c in slow_cues if c.ref and c.ref.get("kind") == "line" and c.language_code == "sl"]
        by_target = {c.ref["target_index"]: c for c in l2}
        assert by_target[0].text == "Dober dan"
        assert by_target[1].text == "Kava ... prosim"


class TestDeriveSectionCuesEnFirst:
    """slow_en_translated scrubs its L2 subtitle text from the en_translated twin."""

    def test_slow_en_translated_overwrites_with_natural_text(self):
        from app.audio.cues import CueTiming, build_cue_manifest
        from app.generation.section_builder import (
            build_en_translated_section,
            build_slow_en_translated_section,
        )

        scenes = [{"label": "Cafe", "lines": [{"speaker": "female-1", "text": "Dober dan", "translation": "Good day"}]}]
        voice_map = {"narrator": "n", "female-1": "v"}
        lesson = Lesson(
            title="T",
            language_code="sl",
            sections=[
                build_en_translated_section(scenes, voice_map, "n", "sl"),
                build_slow_en_translated_section(scenes, voice_map, "n", "sl"),
            ],
        )

        # Realistic timing: title (section None) + every phrase in order.
        timing = [CueTiming(section_index=None, phrase_index=0, start_frame=0, end_frame=1000)]
        frame = 2000
        for si, section in enumerate(lesson.sections):
            for pi in range(len(section.phrases)):
                timing.append(CueTiming(section_index=si, phrase_index=pi, start_frame=frame, end_frame=frame + 1000))
                frame += 2000
        cues = build_cue_manifest(lesson, timing, rate=1000)

        result = derive_section_cues(cues, lesson)
        slow_cues = result[1]  # slow_en_translated
        l2 = [c for c in slow_cues if c.ref and c.ref.get("kind") == "line" and c.language_code == "sl"]
        assert len(l2) == 1
        # Subtitle shows natural text, not the "..."-broken slowed text.
        assert l2[0].text == "Dober dan"
        # English narrator cue is left untouched.
        en = [c for c in slow_cues if c.ref and c.ref.get("kind") == "line" and c.language_code == "en"]
        assert len(en) == 1
        assert en[0].text == "Good day"
