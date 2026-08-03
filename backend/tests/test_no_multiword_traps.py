"""Don't card a word that only ever appeared inside a fixed multiword expression.

The `går` bug: the story sentence was *"Jeg så spor i snøen i går"* — "i går"
means *yesterday*. Stanza tags the second token as a standalone NOUN with lemma
`går`, so TT minted a card fronted `går` and glossed "go / walk" (the verb `gå`),
which is a different word entirely.

The trap list is plugin data, not core logic: it is a fact about Norwegian.
Core reaches it via ``get_multiword_traps``.
"""

from __future__ import annotations

from app.languages import get_multiword_traps
from app.plugins.languages.no.multiword import parse_pairs, trap_second_words, trapped_pairs


class TestTrapData:
    def test_i_gar_is_a_trap(self):
        assert ("i", "går") in trapped_pairs()

    def test_traps_are_lowercased_pairs(self):
        for first, second in trapped_pairs():
            assert first == first.casefold()
            assert second == second.casefold()

    def test_second_words_are_derived_from_the_pairs(self):
        assert "går" in trap_second_words()
        assert trap_second_words() == {second for _, second in trapped_pairs()}

    def test_registry_exposes_the_traps(self):
        assert ("i", "går") in get_multiword_traps("no")

    def test_languages_without_traps_get_an_empty_set(self):
        assert get_multiword_traps("sl") == frozenset()
        assert get_multiword_traps("en") == frozenset()


class TestParsePairs:
    def test_ignores_comments_and_blank_lines(self):
        assert parse_pairs("# a comment\n\ni går\n") == frozenset({("i", "går")})

    def test_ignores_lines_that_are_not_exactly_two_words(self):
        """A one- or three-word line is a data error, not a trap — skip it."""
        assert parse_pairs("går\ni går\npå den andre siden\n") == frozenset({("i", "går")})

    def test_casefolds(self):
        assert parse_pairs("I Går\n") == frozenset({("i", "går")})


class TestIsTrapped:
    def _trapped(self, prev, cur):
        from app.srs.multiword import is_trapped_occurrence

        return is_trapped_occurrence(prev, cur, "no")

    def test_i_gar_is_trapped(self):
        assert self._trapped("i", "går") is True

    def test_case_and_punctuation_insensitive(self):
        assert self._trapped("I", "Går") is True
        assert self._trapped("i", "går.") is True

    def test_standalone_gar_is_not_trapped(self):
        """`Han går hjem` is the real verb — it must still be cardable."""
        assert self._trapped("han", "går") is False

    def test_no_previous_word_is_not_trapped(self):
        assert self._trapped("", "går") is False

    def test_unknown_language_is_never_trapped(self):
        from app.srs.multiword import is_trapped_occurrence

        assert is_trapped_occurrence("i", "går", "sl") is False


class TestAnalyzeLessonWordsSkipsTraps:
    """End-to-end: the trapped occurrence never reaches the candidate pool.

    Uses the default LowercaseLemmatizer (the test pin), so the lemma is the
    lowercased surface — enough to prove the suppression, which happens before
    lemmatization is consulted.
    """

    def _lesson(self, *texts):
        from app.models.lesson import Lesson, Phrase, Section, SectionType

        return Lesson(
            title="T",
            language_code="no",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[Phrase(text=t, voice_id="nb-NO-PernilleNeural", language_code="no") for t in texts],
                )
            ],
        )

    def _occurrences(self, srs_db, *texts):
        from app.api.srs import _analyze_lesson_words

        return _analyze_lesson_words(self._lesson(*texts), srs_db).occurrences

    def test_gar_inside_i_gar_is_not_a_candidate(self, srs_db):
        occ = self._occurrences(srs_db, "Jeg så spor i snøen i går")
        assert "går" not in occ
        assert "snøen" in occ  # the rest of the sentence is unaffected

    def test_standalone_gar_still_counts(self, srs_db):
        occ = self._occurrences(srs_db, "Han går hjem")
        assert occ["går"] == 1

    def test_only_the_trapped_occurrence_is_dropped(self, srs_db):
        """The word stays cardable when it also appears on its own."""
        occ = self._occurrences(srs_db, "Han går hjem", "Jeg kom i går")
        assert occ["går"] == 1

    def test_the_first_word_of_the_pair_is_untouched(self, srs_db):
        occ = self._occurrences(srs_db, "Jeg kom i går")
        assert occ["i"] == 1
