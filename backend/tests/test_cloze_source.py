"""Turning a note's own fields into a clozeable sentence (tunatale-qf6.2, piece D).

A word that images badly gets a cloze production card instead, and the cheapest
sentence source is the note itself: ``Example sentences`` is 98.7% populated on
the Norwegian deck and already glossed, which removes the dependency on lesson
coverage (6 lessons exist) and on the LLM for almost the whole deck.

The hard part is not finding a sentence, it is finding the **surface to blank**.
A dictionary headword usually appears inflected in its own example — measured
over the 1550 words awaiting promotion:

    headword verbatim in an example      994  (65.7%)
    only an inflected form appears       482  (31.8%)  ← needs `Inflections`
    no usable sentence at all             38  ( 2.5%)

So the chooser tries the headword first, then the forms the deck itself lists in
its ``Inflections`` table. That is *deck-authored* ground truth, which is why
this needs no lemmatizer in the sync path and takes no heuristic risk: a prefix
rule ("snø" matches "snøen") also matches "for" against "fordi" and would blank
the wrong word.

**Declining is the feature**, inherited from ``parse_example_sentences``: a
wrong cloze lands on a card the learner studies and is far more expensive than a
missing one. Every ambiguity here returns None.
"""

from __future__ import annotations

import pytest

from app.cards.cloze_source import ClozeChoice, choose_cloze_sentence, parse_inflection_forms

# The real shape of the deck's Inflections field: styled table, grammar labels in
# <thead>, forms in <tbody> cells, some carrying an article or auxiliary.
NOUN_TABLE = """<style type="text/css">.tg{border-collapse:collapse;}</style>
<table class="tg">
<thead>
  <tr><th class="tg-cbs6" rowspan="2"></th><th colspan="2">entall</th><th colspan="2">flertall</th></tr>
  <tr><th>ubestemt form</th><th>bestemt form</th><th>ubestemt form</th><th>bestemt form<br></th></tr>
</thead>
<tbody>
  <tr><td>hankjønn</td><td>en&nbsp;valp</td><td>valpen</td><td>valper</td><td>valpene</td></tr>
</tbody>
</table>"""

VERB_TABLE = (
    '<style type="text/css">.tg{border-collapse:collapse;}</style>'
    '<table class="tg"><thead><tr><th>infinitiv</th><th>presens</th><th>preteritum</th>'
    "<th>presens perfektum</th></tr></thead><tbody><tr>"
    "<td>å&nbsp;oppføre</td><td>oppfører</td><td>oppførte</td><td>har&nbsp;oppført</td>"
    "</tr></tbody></table>"
)


class TestParseInflectionForms:
    def test_reads_the_forms_out_of_a_noun_table(self) -> None:
        assert parse_inflection_forms(NOUN_TABLE) == ("hankjønn", "valp", "valpen", "valper", "valpene")

    def test_takes_the_last_token_of_a_cell(self) -> None:
        """Cells carry an article or auxiliary: "å oppføre", "har oppført"."""
        assert parse_inflection_forms(VERB_TABLE) == ("oppføre", "oppfører", "oppførte", "oppført")

    def test_ignores_the_grammar_labels_in_the_header(self) -> None:
        """`entall`, `presens` and friends are labels, not forms of the word.

        They live in <thead>, so reading <tbody> only excludes them structurally
        rather than by a stoplist that would need a per-language vocabulary.
        """
        forms = parse_inflection_forms(NOUN_TABLE)
        assert not {"entall", "flertall", "ubestemt", "presens", "infinitiv"} & set(forms)

    def test_skips_a_cell_with_no_word_in_it(self) -> None:
        """Layout cells (the empty corner of a gendered noun table) carry nothing."""
        table = "<table><tbody><tr><td></td><td>&nbsp;</td><td>—</td><td>valpen</td></tr></tbody></table>"
        assert parse_inflection_forms(table) == ("valpen",)

    def test_declines_anything_that_is_not_a_table(self) -> None:
        assert parse_inflection_forms("") == ()
        assert parse_inflection_forms("valpen, valper") == ()
        assert parse_inflection_forms("<style>.tg{}</style>") == ()


class TestChooseClozeSentence:
    NOUN_EXAMPLES = "Valpen er veldig leken (<i>The puppy is very playful</i>)"

    def test_blanks_the_headword_when_it_appears_verbatim(self) -> None:
        choice = choose_cloze_sentence(
            "hus", "Huset mitt er stort (<i>My house is big</i>)<br>Et hus (<i>A house</i>)", ""
        )

        assert choice == ClozeChoice(sentence="Et hus", gloss="A house", surface="hus")

    def test_falls_back_to_an_inflected_form_the_deck_lists(self) -> None:
        choice = choose_cloze_sentence("valp", self.NOUN_EXAMPLES, NOUN_TABLE)

        assert choice == ClozeChoice(
            sentence="Valpen er veldig leken",
            gloss="The puppy is very playful",
            surface="Valpen",
        )

    def test_keeps_the_surface_as_the_sentence_spells_it(self) -> None:
        """The blank must match the sentence's own casing, not the deck's."""
        choice = choose_cloze_sentence("valp", self.NOUN_EXAMPLES, NOUN_TABLE)
        assert choice is not None
        assert choice.surface in choice.sentence

    def test_prefers_the_earliest_sentence_that_works(self) -> None:
        raw = (
            "Jeg har ingen (<i>I have none</i>)<br>"
            "Valpen sover (<i>The puppy sleeps</i>)<br>"
            "Valpene leker (<i>The puppies play</i>)"
        )
        choice = choose_cloze_sentence("valp", raw, NOUN_TABLE)
        assert choice is not None
        assert choice.sentence == "Valpen sover"

    def test_declines_when_no_sentence_contains_the_word_in_any_form(self) -> None:
        assert choose_cloze_sentence("valp", "Katten sover (<i>The cat sleeps</i>)", NOUN_TABLE) is None

    def test_declines_when_the_note_has_no_usable_examples(self) -> None:
        assert choose_cloze_sentence("valp", "", NOUN_TABLE) is None
        assert choose_cloze_sentence("valp", "no gloss here", NOUN_TABLE) is None

    def test_matches_whole_words_only(self) -> None:
        """A prefix rule would blank `fordi` for the word `for` — the exact wrong
        cloze this refuses to take a chance on."""
        assert choose_cloze_sentence("for", "Jeg blir fordi det regner (<i>I stay because it rains</i>)", "") is None

    @pytest.mark.parametrize("word", ["", "   "])
    def test_declines_an_empty_word(self, word: str) -> None:
        assert choose_cloze_sentence(word, "Valpen sover (<i>The puppy sleeps</i>)", "") is None
