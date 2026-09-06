"""Tests for the cloze determinacy judge and sentence generator (tunatale-keb0).

The judge is the reusable core: it answers "is this blank DETERMINED by its
context?" for any cloze — one the deck authored, one the LLM proposed, or one
already minted into Anki. Everything else in keb0 is a consumer of it.

The oracle started as keb0's eleven-row table and was CORRECTED against the live
deck (see :class:`TestKeb0AcceptanceTable`). The seven personal pronouns are
underdetermined as the issue says — Norwegian verbs do not inflect for person —
but the issue's four "must survive" rows do not all survive contact:
``det`` and ``hverandre`` are underdetermined too, and rightly so, because
nothing in *{{c1::Det}} er en bok* or *vi liker {{c1::hverandre}}* rules out
`Dette`/`Den` or `det`/`dem`. Only ``seg`` holds up. The issue credited gender
and reflexive agreement with more work than they do: agreement discriminates
within a paradigm, and the cloze front shows no word class to say which paradigm
is wanted.

These tests mock the *client object passed in*, not a module path, so they are
outside the ``patch("app.…")`` mock-boundary gate by construction.
"""

from unittest.mock import AsyncMock

import pytest

from app.llm.cloze_quality import (
    ClozeVerdict,
    blank_out,
    generate_cloze_sentence,
    judge_cloze,
    parse_filler_response,
)


class TestBlankOut:
    """The judge must never see the answer — that is the whole design."""

    def test_blanks_cloze_marked_text(self):
        """A stored cloze arrives already marked; the marker becomes the blank."""
        assert blank_out("{{c1::han}} kommer i morgen", "han") == "___ kommer i morgen"

    def test_blanks_plain_sentence_by_surface(self):
        """`choose_cloze_sentence` returns a plain sentence plus the surface."""
        assert blank_out("Hun bosatte seg i Malmö", "seg") == "Hun bosatte ___ i Malmö"

    def test_blanks_every_occurrence(self):
        """`make_cloze_text` wraps every occurrence, so every one must be hidden.

        Leaving a second copy visible hands the model the answer and the verdict
        becomes vacuous.
        """
        assert blank_out("Han ser han", "han") == "___ ser ___"

    def test_matches_surface_case_insensitively(self):
        """`{{c1::Jeg}}` and a lowercase headword are the same word."""
        assert blank_out("Jeg liker kaffe", "jeg") == "___ liker kaffe"

    def test_respects_word_boundaries(self):
        """`for` must not blank the `for` inside `fordi` — cloze_source's own rule."""
        assert blank_out("Jeg blir fordi det regner", "for") == "Jeg blir fordi det regner"

    def test_empty_sentence_is_empty(self):
        assert blank_out("", "han") == ""


class TestParseFillerResponse:
    """Reply validation, mirroring `translate.parse_gloss_response`."""

    def test_splits_a_comma_list(self):
        assert parse_filler_response("han, hun, jeg") == ("han", "hun", "jeg")

    def test_splits_a_numbered_list(self):
        assert parse_filler_response("1. han\n2. hun") == ("han", "hun")

    def test_splits_a_bulleted_list(self):
        assert parse_filler_response("- han\n- hun") == ("han", "hun")

    def test_strips_quotes_and_trailing_punctuation(self):
        assert parse_filler_response("\"han\", 'hun'.") == ("han", "hun")

    def test_dedups_case_insensitively_keeping_first(self):
        assert parse_filler_response("Han, han, HUN") == ("Han", "HUN")

    def test_rejects_prose(self):
        """A refusal or an explanation is not a filler list.

        Same failure shape `parse_gloss_response` guards: fluent, correctly
        punctuated, about the right subject, and unusable.
        """
        assert parse_filler_response("I'm sorry, but I cannot determine what fits in this blank.") == ()

    def test_drops_multiword_items_but_keeps_the_rest(self):
        """A blank here is one word; a phrase is the model answering a different question."""
        assert parse_filler_response("han, the man who arrived, hun") == ("han", "hun")

    def test_empty_reply_is_empty(self):
        assert parse_filler_response("") == ()
        assert parse_filler_response("   ") == ()


class TestJudgeCloze:
    """The verdict rule itself."""

    @staticmethod
    def _client(reply: str) -> AsyncMock:
        client = AsyncMock()
        client.complete.return_value = reply
        return client

    @pytest.mark.asyncio
    async def test_single_filler_matching_target_is_determined(self):
        verdict = await judge_cloze(
            self._client("seg"), sentence="Hun bosatte seg i Malmö", surface="seg", language="no"
        )
        assert verdict.status == "determined"
        assert verdict.fillers == ("seg",)

    @pytest.mark.asyncio
    async def test_several_fillers_is_underdetermined(self):
        verdict = await judge_cloze(
            self._client("han, hun, jeg, du, vi, de"),
            sentence="{{c1::han}} kommer i morgen",
            surface="han",
            language="no",
        )
        assert verdict.status == "underdetermined"
        assert "hun" in verdict.fillers

    @pytest.mark.asyncio
    async def test_target_absent_from_fillers_is_underdetermined(self):
        """If the model cannot even produce the target, the sentence does not cue it."""
        verdict = await judge_cloze(
            self._client("bra, fint"), sentence="Alt er ålreit", surface="ålreit", language="no"
        )
        assert verdict.status == "underdetermined"

    @pytest.mark.asyncio
    async def test_target_match_is_case_insensitive(self):
        """`{{c1::Jeg}}` capitalised at sentence start still matches a lowercase reply."""
        verdict = await judge_cloze(
            self._client("Jeg"), sentence="{{c1::Jeg}} liker kaffe", surface="jeg", language="no"
        )
        assert verdict.status == "determined"

    @pytest.mark.asyncio
    async def test_accepted_variants_are_not_competitors(self):
        """`gjennom, igjennom` is ONE lexical item wearing two spellings.

        The same `card_surface_variants` notion `choose_cloze_sentence` already
        takes; a second spelling of the answer does not make the blank free.
        """
        verdict = await judge_cloze(
            self._client("gjennom, igjennom"),
            sentence="Gå gjennom tunnelen",
            surface="gjennom",
            language="no",
            also_accept=("igjennom",),
        )
        assert verdict.status == "determined"

    @pytest.mark.asyncio
    async def test_never_shows_the_model_the_answer(self):
        """The prompt must carry the blank, not the surface. This is the design.

        Showing the answer and asking "is this determined?" invites the model to
        rationalise a cue after the fact; making it FILL the blank measures its
        actual uncertainty instead.
        """
        client = self._client("han, hun")
        await judge_cloze(client, sentence="{{c1::han}} kommer i morgen", surface="han", language="no")
        sent = " ".join(str(v) for v in client.complete.call_args.kwargs.values())
        assert "___" in sent
        assert "han" not in sent.lower()

    @pytest.mark.asyncio
    async def test_llm_failure_is_unknown_not_a_verdict(self):
        """Fail-soft, but never claim a verdict the model did not give.

        "determined" would silently keep a bad sentence; "underdetermined" would
        burn a generation call on a good one. Neither is honest, so the caller
        is told nothing was learned.
        """
        client = AsyncMock()
        client.complete.side_effect = Exception("groq 429")
        verdict = await judge_cloze(client, sentence="Alt er ålreit", surface="ålreit", language="no")
        assert verdict.status == "unknown"

    @pytest.mark.asyncio
    async def test_unusable_reply_is_unknown(self):
        verdict = await judge_cloze(
            self._client("I cannot help with that."), sentence="Alt er ålreit", surface="ålreit", language="no"
        )
        assert verdict.status == "unknown"

    @pytest.mark.asyncio
    async def test_empty_sentence_is_unknown_without_calling_the_llm(self):
        client = self._client("han")
        verdict = await judge_cloze(client, sentence="", surface="han", language="no")
        assert verdict.status == "unknown"
        client.complete.assert_not_called()


class TestKeb0AcceptanceTable:
    """keb0's oracle, driven through the real verdict rule.

    Every ``reply`` below is an ACTUAL reply from the live model against the
    live deck (``scripts/report_cloze_quality.py``, 2026-09-06), not an invented
    one. The first cut of this file guessed the replies and put the target word
    first in each list, which quietly encoded the assumption that the model
    volunteers the stored answer — it does not, it is blind, and building an
    argmax rule on that guess passed four of the seven pronouns.

    What is under test is the RULE: given this reply, does the verdict follow?
    Whether the live model returns this reply on any given day is a separate,
    noisier question — see :class:`TestJudgeStabilityIsNotAssumed`.
    """

    #: The pronoun rows keb0 was filed about. Norwegian verbs do not inflect for
    #: person, so the model offers the whole paradigm.
    UNDERDETERMINED = [
        ("han", "{{c1::han}} kommer i morgen", "Hun, Det, Jeg, Vi, De, Noen, Alt, Dette, Den"),
        ("jeg", "{{c1::Jeg}} liker kaffe", "Du, Han, Hun, Den, Det, Vi, Dere, De"),
        ("de", "{{c1::De}} er her", "Jeg, Du, Han, Hun, Vi, Dere, Den, Det, Dette, Noen, Alt"),
        ("vi", "skal {{c1::vi}} gå nå?", "du, jeg, dere, de, han, hun, man, den, det"),
        ("du", "har {{c1::du}} hentet boka?", "jeg, han, hun, vi, dere, de, man, noen, folk, alle"),
        ("dere", "vi så {{c1::dere}} ikke", "ham, henne, dem, det, oss"),
        # keb0 called this one GOOD ("constrained by the reflexive verb"). The
        # live model disagrees and is right: nothing forces a reciprocal reading
        # of `vi liker ___`, and `vi liker det` / `vi liker dem` are ordinary
        # Norwegian. Recorded as a correction to the issue, not a judge defect.
        ("hverandre", "vi liker {{c1::hverandre}}", "det, dem"),
        # Likewise `det`: keb0 credited gender agreement, but `Dette er en bok`
        # and `Den er en bok` are both fine, so gender rules out nothing here.
        ("det", "{{c1::Det}} er en bok", "Dette, Den"),
    ]

    #: The rows that survive. `seg` is the one keb0 row the live model confirms
    #: outright — a reflexive verb plus a subject really does admit one word.
    DETERMINED = [
        ("seg", "Hun bosatte {{c1::seg}} i Malmö", "seg"),
        ("per", "Det koster 50 kroner {{c1::per}} person", "per"),
        ("hva", "{{c1::Hva}} vil du ha?", "Hva"),
        ("hvilken", "{{c1::Hvilken}} bok vil du lese?", "Hvilken"),
    ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("word", "sentence", "reply"), UNDERDETERMINED)
    async def test_a_reply_offering_alternatives_is_underdetermined(self, word, sentence, reply):
        client = AsyncMock()
        client.complete.return_value = reply
        verdict = await judge_cloze(client, sentence=sentence, surface=word, language="no")
        assert verdict.status == "underdetermined"
        assert verdict.competitors, "the alternatives are what the learner would be marked wrong for"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("word", "sentence", "reply"), DETERMINED)
    async def test_a_reply_offering_only_the_answer_is_determined(self, word, sentence, reply):
        client = AsyncMock()
        client.complete.return_value = reply
        verdict = await judge_cloze(client, sentence=sentence, surface=word, language="no")
        assert verdict.status == "determined"
        assert verdict.competitors == ()


class TestJudgeStabilityIsNotAssumed:
    """The judge is an LLM and its verdict on a borderline sentence MOVES.

    Measured, not assumed: two full passes over the same 84 live clozes, same
    prompt, temperature 0.

        run A   11 determined / 73 underdetermined / 0 unknown
        run B    8 determined / 75 underdetermined / 1 unknown
        flips    2 of 84 (97.6% agreement) — `dette` and `hun`, both OK -> BAD

    Both flips run the same way, and that is structural rather than luck:
    "determined" requires ZERO competitors, so it is the knife-edge verdict,
    while "underdetermined" only needs one competitor to survive. A sentence
    the model is genuinely unsure about therefore decays toward BAD, which is
    the safe direction — it costs a regeneration attempt, not a bad card kept.

        run A   OK   hun  ___ har på seg en hvit kjole   —
        run B   BAD  hun  ___ har på seg en hvit kjole   also fits: Han

    Neither reading is unreasonable — *kjole* (a dress) is a real cue for `hun`
    — which is why one verdict is not ground truth about a sentence.

    Pinned rather than papered over because the product design already absorbs
    it: an underdetermined cloze is still MINTED (the user's call, 2026-09-06)
    and "try again" is what a human uses on the ones they disagree with. A judge
    that were load-bearing alone would need sampling; this one is advisory.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("reply", "expected"),
        [("Han", "underdetermined"), ("hun", "determined")],
    )
    async def test_the_rule_follows_the_reply_it_is_given(self, reply, expected):
        client = AsyncMock()
        client.complete.return_value = reply
        verdict = await judge_cloze(
            client, sentence="{{c1::hun}} har på seg en hvit kjole", surface="hun", language="no"
        )
        assert verdict.status == expected


class TestGenerateClozeSentence:
    """Writing a sentence whose blank IS determined."""

    @staticmethod
    def _client(reply: str) -> AsyncMock:
        client = AsyncMock()
        client.complete.return_value = reply
        return client

    @pytest.mark.asyncio
    async def test_returns_the_generated_sentence(self):
        """An antecedent in a preceding sentence is the cue for a pronoun."""
        client = self._client("Kari kommer i morgen. Hun tar toget.")
        result = await generate_cloze_sentence(client, word="hun", gloss="she", pos="PRON", language="no")
        assert result == "Kari kommer i morgen. Hun tar toget."

    @pytest.mark.asyncio
    async def test_rejects_a_sentence_missing_the_target_word(self):
        """A sentence that does not contain the word cannot carry its blank."""
        client = self._client("Kari kommer i morgen.")
        assert await generate_cloze_sentence(client, word="hun", gloss="she", pos="PRON", language="no") is None

    @pytest.mark.asyncio
    async def test_matches_the_target_on_a_word_boundary(self):
        """`for` inside `fordi` is not an occurrence — cloze_source's rule again."""
        client = self._client("Jeg blir fordi det regner.")
        assert await generate_cloze_sentence(client, word="for", gloss="for", pos="ADP", language="no") is None

    @pytest.mark.asyncio
    async def test_rejects_a_reply_that_is_prose_about_the_task(self):
        client = self._client("Sure! Here is a sentence for you: Hun tar toget.")
        result = await generate_cloze_sentence(client, word="hun", gloss="she", pos="PRON", language="no")
        assert result == "Hun tar toget."

    @pytest.mark.asyncio
    async def test_llm_failure_returns_none(self):
        client = AsyncMock()
        client.complete.side_effect = Exception("groq 429")
        assert await generate_cloze_sentence(client, word="hun", gloss="she", pos="PRON", language="no") is None

    @pytest.mark.asyncio
    async def test_empty_word_returns_none_without_calling_the_llm(self):
        client = self._client("noe")
        assert await generate_cloze_sentence(client, word="  ", gloss="", pos="", language="no") is None
        client.complete.assert_not_called()


class TestClozeVerdict:
    def test_is_frozen(self):
        verdict = ClozeVerdict(status="determined", fillers=("seg",))
        with pytest.raises(AttributeError):
            verdict.status = "underdetermined"  # type: ignore[misc]


class TestUncoveredEdges:
    """Paths the main suites do not reach, each a real behaviour."""

    def test_blank_out_with_an_empty_surface_still_blanks_the_marker(self):
        """A stored cloze knows its own answer; the surface argument is redundant there."""
        assert blank_out("{{c1::han}} kommer", "") == "___ kommer"

    def test_parse_filler_response_drops_an_empty_item(self):
        """A trailing comma is the common shape: "han, hun," splits to three."""
        assert parse_filler_response("han, hun,") == ("han", "hun")

    @pytest.mark.asyncio
    async def test_a_surface_absent_from_its_sentence_is_unknown_without_asking(self):
        """No blank can be built, so there is no question to put to the model.

        Reachable for real: a stored cloze whose sentence was edited in Anki no
        longer contains the word TT thinks it blanks.
        """
        client = AsyncMock()
        client.complete.return_value = "whatever"
        verdict = await judge_cloze(client, sentence="Katten sover", surface="han", language="no")
        assert verdict.status == "unknown"
        client.complete.assert_not_called()

    @pytest.mark.asyncio
    async def test_generate_sends_the_gloss_and_pos_as_context(self):
        """Both are optional and both are appended when present."""
        client = AsyncMock()
        client.complete.return_value = "Bilen står foran huset."
        await generate_cloze_sentence(client, word="foran", gloss="in front of", pos="ADP", language="no")
        prompt = client.complete.call_args.kwargs["prompt"]
        assert prompt == "foran (in front of) [ADP]"

    @pytest.mark.asyncio
    async def test_generate_omits_absent_context(self):
        client = AsyncMock()
        client.complete.return_value = "Bilen står foran huset."
        await generate_cloze_sentence(client, word="foran", gloss="", pos="", language="no")
        assert client.complete.call_args.kwargs["prompt"] == "foran"

    @pytest.mark.asyncio
    async def test_an_empty_reply_produces_no_sentence(self):
        client = AsyncMock()
        client.complete.return_value = "   "
        assert await generate_cloze_sentence(client, word="foran", gloss="", pos="", language="no") is None


class TestUposForDisambig:
    """Mapping a deck's own POS label to UPOS without opening the collection.

    Exists because `is_function_word(text, lang)` WITHOUT upos returns False for
    obvious function words — False there means "no POS supplied", not "open
    class". A caller reading it as the latter classifies every closed-class word
    as open, which is the entire population `cloze_prestage` serves.
    """

    def test_maps_a_known_label(self):
        from app.cards.field_map import upos_for_disambig

        assert upos_for_disambig("preposition") == "ADP"

    def test_is_case_and_whitespace_insensitive(self):
        from app.cards.field_map import upos_for_disambig

        assert upos_for_disambig("  Preposition ") == "ADP"

    def test_an_unknown_label_is_none_not_a_guess(self):
        from app.cards.field_map import upos_for_disambig

        assert upos_for_disambig("gerundive") is None

    def test_an_empty_label_is_none(self):
        """28 rows in the real Norwegian deck carry an empty `Word class`."""
        from app.cards.field_map import upos_for_disambig

        assert upos_for_disambig("") is None
        assert upos_for_disambig("   ") is None
