"""A course word's host and parts come from the lesson, not a guess (tunatale-h0q3.65, slice 6).

Wolff's build-up rows split every joined word of a line (2b line 2: ``nà`` that, ``nga``
linker), and the converter writes what they say into the lesson's dialogue gloss:
``{"word": "nang", "translation": "that", "host": "na", "parts": "nà that + -ng linker (nga)"}``.
Without it the table lemmatizer reads nang as manang (a spelling variant) and the
Grade page offered 'manang' as a card (design-short-forms-and-linkers F2, O10).

- ``host`` is the token's lemma in that lesson, in the reader and in the words a
  listen or Grade counts, so both pick the same card.
- ``parts`` is the reader's parts line for the token, in place of the decoder's.
- Neither touches a token the lesson does not name, and both are matched without case.
"""

from __future__ import annotations

from app.models.lesson import Lesson, Phrase, Section, SectionType, story_hosts, story_parts
from app.srs.database import SRSDatabase
from app.srs.lemmatizer import LowercaseLemmatizer
from app.srs.transcript import extract_transcript

NANG = {"word": "nang", "translation": "that", "host": "na", "parts": "nà that + -ng linker (nga)"}


def _lesson(text: str, glosses: list[dict] | None) -> Lesson:
    lesson = Lesson(
        title="T",
        language_code="ceb",
        sections=[
            Section(
                section_type=SectionType.NATURAL_SPEED,
                phrases=[Phrase(text=text, voice_id="female-1", language_code="ceb", role="female-1")],
            )
        ],
    )
    if glosses is not None:
        lesson.generation_metadata = {"story": {"dialogue_glosses": glosses}}
    return lesson


def _words(lesson: Lesson):
    return extract_transcript(lesson, SRSDatabase(":memory:"), LowercaseLemmatizer()).dialogue_lines[0].words


class TestStoryReadings:
    def test_hosts_and_parts_are_keyed_by_the_casefolded_word(self) -> None:
        story = {"dialogue_glosses": [{**NANG, "word": "Nang"}, {"word": "balaya", "translation": "the house"}]}
        assert story_hosts(story) == {"nang": "na"}
        assert story_parts(story) == {"nang": "nà that + -ng linker (nga)"}

    def test_a_story_without_them_has_none(self) -> None:
        assert story_hosts({}) == {} == story_parts({"dialogue_glosses": [{"word": "x", "translation": "y"}]})

    def test_an_entry_that_is_not_an_object_is_skipped(self) -> None:
        assert story_hosts({"dialogue_glosses": ["nang", NANG]}) == {"nang": "na"}


class TestReader:
    def test_a_joined_word_reads_as_its_host_with_the_lessons_parts(self) -> None:
        nang = _words(_lesson("Mao gyud nang balaya.", [NANG]))[2]
        assert (nang.surface, nang.lemma, nang.parts) == ("nang", "na", "nà that + -ng linker (nga)")

    def test_the_rest_of_the_line_is_untouched(self) -> None:
        with_hosts = _words(_lesson("Mao gyud nang balaya.", [NANG]))
        without = _words(_lesson("Mao gyud nang balaya.", None))
        assert [(w.lemma, w.parts) for w in with_hosts[:2] + with_hosts[3:]] == [
            (w.lemma, w.parts) for w in without[:2] + without[3:]
        ]

    def test_control_without_the_lessons_reading_the_token_keeps_its_own(self) -> None:
        nang = _words(_lesson("Mao gyud nang balaya.", None))[2]
        assert nang.lemma == "nang"


class TestCountedWords:
    def test_a_listen_counts_the_host_not_the_surface(self, srs_db) -> None:
        from app.api.srs import _analyze_lesson_words

        words = _analyze_lesson_words(_lesson("Mao gyud nang balaya.", [NANG]), srs_db)
        assert words.occurrences["na"] == 1
        assert "nang" not in words.occurrences
        assert words.first_surface["na"] == "nang"

    def test_control_without_the_reading(self, srs_db) -> None:
        from app.api.srs import _analyze_lesson_words

        assert "nang" in _analyze_lesson_words(_lesson("Mao gyud nang balaya.", None), srs_db).occurrences
