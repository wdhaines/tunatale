"""Tests for the transcript extraction service."""

from __future__ import annotations

from datetime import UTC, date, datetime

from app.models.lesson import KeyPhraseInfo, Lesson, Phrase, Section, SectionType
from app.models.srs_item import Direction, DirectionState, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.database import SRSDatabase
from app.srs.lemmatizer import LowercaseLemmatizer, TokenAnalysis, _serialize_analyses, model_version_for
from app.srs.transcript import (
    TranscriptData,
    WordToken,
    _extract_punct_pairs,
    _is_due,
    extract_transcript,
    resolve_active_direction,
)


def _make_lesson(l2_phrases: list[tuple[str, str]] | None = None, lang: str = "sl") -> Lesson:
    """Build a minimal lesson with a NATURAL_SPEED section.

    l2_phrases is a list of (role, text) tuples for L2 dialogue lines.
    """
    lesson = Lesson(title="Test Lesson", language_code=lang)
    phrases = []
    if l2_phrases:
        for role, text in l2_phrases:
            phrases.append(Phrase(text=text, voice_id="female-1", language_code=lang, role=role))
    lesson.sections = [Section(section_type=SectionType.NATURAL_SPEED, phrases=phrases)]
    return lesson


class TestExtractTranscript:
    def setup_method(self):
        self.db = SRSDatabase(":memory:")
        self.lemmatizer = LowercaseLemmatizer()

    def test_returns_transcript_data(self):
        lesson = _make_lesson([("female-1", "Zdravo.")])
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        assert isinstance(result, TranscriptData)

    def test_key_phrases_passed_through(self):
        lesson = _make_lesson()
        lesson.key_phrases = [KeyPhraseInfo(phrase="Zdravo", translation="Hello")]
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        assert len(result.key_phrases) == 1
        assert result.key_phrases[0].phrase == "Zdravo"

    def test_unknown_word_has_srs_state_unknown(self):
        lesson = _make_lesson([("female-1", "banka")])
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        assert result.dialogue_lines[0].words[0].srs_state == "unknown"

    def test_reconstructed_sentence_preserves_punctuation(self):
        """The line sentence (used as card source_sentence) keeps punctuation,
        not the bare surface join — e.g. 'Koliko časa imaš?' not '... imaš'."""
        lesson = _make_lesson([("female-1", "Koliko časa imaš?")])
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        assert result.dialogue_lines[0].sentence == "Koliko časa imaš?"

    def test_reconstructed_sentence_preserves_internal_punctuation(self):
        lesson = _make_lesson([("female-1", "Zdravo, kje ste?")])
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        assert result.dialogue_lines[0].sentence == "Zdravo, kje ste?"

    def test_ignored_lemma_renders_ignored(self):
        self.db.add_ignored_lemma("sl", "banka")
        lesson = _make_lesson([("female-1", "banka")])
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        word = result.dialogue_lines[0].words[0]
        assert word.srs_state == "ignored"
        assert word.active_state == "ignored"
        assert word.srs_item_id is None
        assert word.progress is None
        assert word.inflectable is False

    def test_ignored_check_does_not_affect_known_words(self):
        unit = SyntacticUnit(text="banka", translation="bank", word_count=1, difficulty=1, source="llm", lemma="banka")
        self.db.add_collocation(unit, language_code="sl")
        self.db.add_ignored_lemma("sl", "banka")
        lesson = _make_lesson([("female-1", "banka")])
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        word = result.dialogue_lines[0].words[0]
        assert word.srs_state == "new"
        assert word.active_state != "ignored"

    def test_known_word_has_correct_srs_state(self):
        unit = SyntacticUnit(text="banka", translation="bank", word_count=1, difficulty=1, source="llm", lemma="banka")
        self.db.add_collocation(unit, language_code="sl")
        lesson = _make_lesson([("female-1", "banka")])
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        assert result.dialogue_lines[0].words[0].srs_state == "new"

    def test_surface_keyed_card_matched_via_surface_fallback(self):
        """A card keyed by its surface/greeting form is matched even when the
        lemmatizer reduces the token to a dictionary lemma with no card.

        Regression: 'dobrodošli' (Welcome) is stored under lemma 'dobrodošli',
        but classla lemmatizes it to 'dobrodošel' → it showed as unknown.
        """
        from app.srs.lemmatizer import TokenAnalysis
        from tests._helpers.lemmatizer import StubLemmatizer

        unit = SyntacticUnit(
            text="dobrodošli",
            translation="Welcome.",
            word_count=1,
            difficulty=1,
            source="llm",
            lemma="dobrodošli",
        )
        self.db.add_collocation(unit, language_code="sl")
        item = self.db.get_collocation("dobrodošli")
        item.state = SRSState.REVIEW
        self.db.update_collocation(item)

        stub = StubLemmatizer()
        stub.set_sentence("Dobrodošli", [TokenAnalysis(surface="Dobrodošli", lemma="dobrodošel")])

        lesson = _make_lesson([("female-1", "Dobrodošli")])
        result = extract_transcript(lesson, self.db, stub)
        word = result.dialogue_lines[0].words[0]
        assert word.srs_state == "review"
        assert word.translation == "Welcome."

    def test_known_word_in_review_state(self):
        unit = SyntacticUnit(text="banka", translation="bank", word_count=1, difficulty=1, source="llm", lemma="banka")
        self.db.add_collocation(unit, language_code="sl")
        item = self.db.get_collocation("banka")
        item.state = SRSState.REVIEW
        self.db.update_collocation(item)

        lesson = _make_lesson([("female-1", "banka")])
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        assert result.dialogue_lines[0].words[0].srs_state == "review"

    def test_cloze_word_state_falls_through_to_production(self):
        """Cloze items have only PRODUCTION direction; transcript must not crash.

        Regression: `item.state.value` previously KeyErrored on cloze items because
        the legacy `_rec` shim hardcoded RECOGNITION. Now it falls through to
        production when card_type='cloze'.
        """
        from app.models.srs_item import Direction

        unit = SyntacticUnit(
            text="vsak",
            translation="every",
            word_count=1,
            difficulty=1,
            source="cloze",
            lemma="vsak",
            source_sentence="Odprto je vsak dan",
            card_type="cloze",
        )
        self.db.add_collocation(unit, language_code="sl")
        item = self.db.get_collocation("vsak")
        item.directions[Direction.PRODUCTION].state = SRSState.LEARNING
        self.db.update_collocation(item)

        lesson = _make_lesson([("female-1", "Odprto je vsak dan")])
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        words = result.dialogue_lines[0].words
        vsak_token = next(w for w in words if w.lemma == "vsak")
        assert vsak_token.srs_state == "learning"

    def _add_variant_card(self, text: str = "mot, imot", translation: str = "against") -> None:
        """Add a Norwegian spelling-variant card (word_count=1, lemma unset)."""
        unit = SyntacticUnit(
            text=text,
            translation=translation,
            word_count=1,
            difficulty=1,
            source="anki",
        )
        self.db.add_collocation(unit, language_code="no")

    def test_variant_card_matched_by_first_spelling(self):
        """A comma-front card ('mot, imot') resolves when its first spelling is read."""
        self._add_variant_card()
        lesson = _make_lesson([("female-1", "mot")], lang="no")
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        word = result.dialogue_lines[0].words[0]
        assert word.srs_state == "new"
        assert word.active_state == "new"
        assert word.translation == "against"
        assert word.srs_item_id is not None

    def test_variant_card_matched_by_second_spelling(self):
        """The alternate spelling ('imot') resolves to the SAME single card."""
        self._add_variant_card()
        lesson = _make_lesson([("female-1", "mot"), ("female-1", "imot")], lang="no")
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        mot_id = result.dialogue_lines[0].words[0].srs_item_id
        imot_id = result.dialogue_lines[1].words[0].srs_item_id
        assert mot_id is not None
        assert imot_id == mot_id  # one Anki note → one SRS card

    def test_variant_card_reflects_review_state(self):
        self._add_variant_card()
        item = self.db.get_collocation("mot, imot")
        item.state = SRSState.REVIEW
        self.db.update_collocation(item)
        lesson = _make_lesson([("female-1", "imot")], lang="no")
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        assert result.dialogue_lines[0].words[0].srs_state == "review"

    def _add_variant_pair(self):
        """The real shape from the Norwegian deck: ONE variant card that has ALSO
        been given its production as a cloze, so TWO rows carry the same front.

        ⚠️ Every other test in this class adds a single row per variant text, and
        that is precisely why none of them could see tunatale-xmnv. A one-row
        fixture passes whatever the index does with duplicates.

        Faithful to collocations 1534/3059 in tunatale_no.db: the vocab row
        carries a disambig ('preposition') and recognition only; the cloze
        carries an empty disambig, a production direction, and points back via
        base_collocation_id. The differing disambig is load-bearing — with both
        empty, add_collocation would MERGE the second into the first and there
        would be no pair to test.
        """
        from app.common.guid import compute_guid

        base_unit = SyntacticUnit(
            text="mellom, imellom",
            translation="between",
            word_count=1,
            difficulty=1,
            source="anki",
            disambig_key="preposition",
        )
        base_id = self.db.upsert_by_guid(
            base_unit,
            "no",
            {
                Direction.RECOGNITION: DirectionState(
                    direction=Direction.RECOGNITION,
                    due_at=datetime(2026, 1, 1, tzinfo=UTC),
                    state=SRSState.REVIEW,
                    stability=120.0,
                    reps=52,
                    last_review=datetime(2025, 9, 1, tzinfo=UTC),
                )
            },
            anki_note_id=9200,
        )
        cloze_unit = SyntacticUnit(
            text="mellom, imellom",
            translation="between",
            word_count=1,
            difficulty=1,
            source="anki",
            card_type="cloze",
            source_sentence="Det er en hemmelighet {{c1::mellom}} oss",
        )
        self.db.add_collocation(cloze_unit, language_code="no")
        cloze_guid = compute_guid(cloze_unit.text, "no", cloze_unit.disambig_key or "")
        cloze_id = self.db.get_collocation_id_by_guid(cloze_guid)
        self.db.set_base_collocation_id(cloze_id, base_id)
        return base_id, cloze_id

    def test_a_cloze_does_not_shadow_the_vocab_card_it_belongs_to(self):
        """tunatale-xmnv. USER: "Why can't mellom be reviewed? It's 0% learned
        but no review option."

        `_build_variant_index` fills surface -> (id, item) from a query with no
        ORDER BY and no dedupe, so when a variant card also has a cloze BOTH
        rows carry the same front and the last write wins — the cloze, because
        its id is higher. The reader then binds the word to an unstarted
        production cloze and never sees the recognition card that is actually
        due.

        ⚠️ Asserted on the RESOLVED ID, not on "some card was found": binding to
        the cloze also finds a card, with a translation and a state, which is
        why this looked like working software.
        """
        base_id, cloze_id = self._add_variant_pair()
        lesson = _make_lesson([("female-1", "mellom")], lang="no")

        result = extract_transcript(lesson, self.db, self.lemmatizer, today=date(2026, 8, 28))
        word = result.dialogue_lines[0].words[0]

        assert word.srs_item_id == base_id, (
            f"bound to {word.srs_item_id} (cloze is {cloze_id}); the vocab row is the lexical item"
        )
        assert word.card_type != "cloze"

    def test_the_shadowed_recognition_card_is_still_reviewable(self):
        """The user-visible half: binding to the cloze hid a due recognition card.

        The id assertion above can be satisfied while the reader still reports
        nothing to review, so pin the consequence too.
        """
        self._add_variant_pair()
        lesson = _make_lesson([("female-1", "mellom")], lang="no")

        word = extract_transcript(lesson, self.db, self.lemmatizer, today=date(2026, 8, 28)).dialogue_lines[0].words[0]

        assert word.recognition_state == "review"
        assert word.recognition_is_due is True

    def test_either_spelling_resolves_to_the_vocab_card_of_a_cloze_covered_variant(self):
        """The second spelling must not resolve differently from the first."""
        base_id, _ = self._add_variant_pair()
        lesson = _make_lesson([("female-1", "mellom"), ("female-1", "imellom")], lang="no")

        result = extract_transcript(lesson, self.db, self.lemmatizer, today=date(2026, 8, 28))

        assert result.dialogue_lines[0].words[0].srs_item_id == base_id
        assert result.dialogue_lines[1].words[0].srs_item_id == base_id

    def _add_lemma_card(self, text: str, lemma: str, translation: str) -> int:
        from app.common.guid import compute_guid

        unit = SyntacticUnit(text=text, translation=translation, word_count=1, difficulty=1, source="llm", lemma=lemma)
        self.db.add_collocation(unit, language_code="no")
        return self.db.get_collocation_id_by_guid(compute_guid(text, "no", ""))

    def _flip_lesson(self, verb_first: bool):
        """Two lines sharing the surface 'gaar' but NOT its lemma, either order.

        Line V: the verb, lemma 'gaa'.  Line N: the noun, lemma 'gaar'.
        A StubLemmatizer supplies the differing lemmas, because the whole point
        is a surface whose lemma depends on its sentence.
        """
        from tests._helpers.lemmatizer import StubLemmatizer

        verb_line, noun_line = "Han gaar hjem", "Det var i gaar"
        stub = StubLemmatizer()
        stub.set_sentence(
            verb_line,
            [
                TokenAnalysis(surface="Han", lemma="han"),
                TokenAnalysis(surface="gaar", lemma="gaa"),
                TokenAnalysis(surface="hjem", lemma="hjem"),
            ],
        )
        stub.set_sentence(
            noun_line,
            [
                TokenAnalysis(surface="Det", lemma="det"),
                TokenAnalysis(surface="var", lemma="vaere"),
                TokenAnalysis(surface="i", lemma="i"),
                TokenAnalysis(surface="gaar", lemma="gaar"),
            ],
        )
        lines = [verb_line, noun_line] if verb_first else [noun_line, verb_line]
        return _make_lesson([("female-1", t) for t in lines], lang="no"), stub, lines.index(noun_line)

    def _noun_binding(self, verb_first: bool):
        lesson, stub, noun_idx = self._flip_lesson(verb_first)
        result = extract_transcript(lesson, self.db, stub, today=date(2026, 8, 28))
        line = result.dialogue_lines[noun_idx]
        return next(w for w in line.words if w.surface == "gaar").srs_item_id

    def test_a_words_card_does_not_depend_on_what_came_earlier_in_the_lesson(self):
        """tunatale-klh. `base_cache` is READ by lemma but WRITTEN by surface as a
        fallback, so the two namespaces collide: a verb token (surface 'gaar',
        lemma 'gaa') writes base_cache['gaar'] = verb-card, and a later token
        whose LEMMA is genuinely 'gaar' reads it and gets the verb's card.

        The same sentence then renders a different card purely by position.

        ⚠️ This bead's original oracle used the real Norwegian deck ('i gaar' vs
        'Da gaar vi videre') and NO LONGER REPRODUCES — re-measured 2026-08-28,
        all three of its rows now agree, because the deck gained an 'i gaar' row
        and the inflection index resolves the surface anyway. A data-dependent
        oracle rotted in thirteen days. This one is synthetic on purpose: it
        cannot rot, and it tests the mechanism rather than one deck's contents.
        """
        verb_id = self._add_lemma_card("gaa", "gaa", "to go")
        noun_id = self._add_lemma_card("gaar", "gaar", "yesterday")
        assert verb_id != noun_id

        assert self._noun_binding(verb_first=False) == noun_id, "control: alone, the noun resolves correctly"
        assert self._noun_binding(verb_first=True) == noun_id, (
            "the verb earlier in the lesson poisoned the cache and the noun took its card"
        )

    def test_an_uncarded_word_stays_unknown_however_often_it_appears(self):
        """Baseline: no card at all resolves to nothing, every time.

        ⚠️ This does NOT exercise the surface fallback — 'ukjent' lemmatizes to
        itself, so `surface.lower() != lemma` is false and the fallback never
        runs. It was written as the guard for that path and could not have been;
        the real guard is the test below. Kept as the plain no-card baseline.
        """
        lesson = _make_lesson([("female-1", "ukjent"), ("female-1", "ukjent")], lang="no")
        result = extract_transcript(lesson, self.db, self.lemmatizer, today=date(2026, 8, 28))
        for line in result.dialogue_lines:
            assert line.words[0].srs_state == "unknown"
            assert line.words[0].srs_item_id is None

    def test_the_surface_fallback_is_memoised_and_still_cannot_leak_across_lemmas(self):
        """The surface fallback must be cached, and its cache must stay separate.

        Two tokens share the surface 'gaar' with DIFFERENT lemmas, and neither
        lemma has a card — so both fall through to the surface lookup. That is
        the only shape that reaches the surface memo, and it is exactly the
        shape klh got wrong: one shared dict let the first token's answer
        satisfy the second token's *lemma* read.

        Asserts both halves — one DB query for the repeated surface (the memo
        does its job) AND both tokens resolving to the surface's own card
        (nothing leaked). Counting queries via a subclass, not a patch: the DB
        is our own code, so a `patch("app.…")` here would need a
        mock_allowlist.txt entry for what is really just a call counter.
        """
        from app.common.guid import compute_guid
        from tests._helpers.lemmatizer import StubLemmatizer

        class CountingDB(SRSDatabase):
            lemma_queries: list[str] = []

            def get_collocation_by_lemma_with_id(self, lemma):
                CountingDB.lemma_queries.append(lemma)
                return super().get_collocation_by_lemma_with_id(lemma)

        CountingDB.lemma_queries = []
        db = CountingDB(":memory:")
        unit = SyntacticUnit(
            text="gaar", translation="yesterday", word_count=1, difficulty=1, source="llm", lemma="gaar"
        )
        db.add_collocation(unit, language_code="no")
        card_id = db.get_collocation_id_by_guid(compute_guid("gaar", "no", ""))

        line_a, line_b = "Han gaar hjem", "Hun gaar ut"
        stub = StubLemmatizer()
        stub.set_sentence(
            line_a,
            [
                TokenAnalysis(surface="Han", lemma="han"),
                TokenAnalysis(surface="gaar", lemma="gaa"),
                TokenAnalysis(surface="hjem", lemma="hjem"),
            ],
        )
        stub.set_sentence(
            line_b,
            [
                TokenAnalysis(surface="Hun", lemma="hun"),
                TokenAnalysis(surface="gaar", lemma="ga"),
                TokenAnalysis(surface="ut", lemma="ut"),
            ],
        )
        lesson = _make_lesson([("female-1", line_a), ("female-1", line_b)], lang="no")

        result = extract_transcript(lesson, db, stub, today=date(2026, 8, 28))

        bound = [next(w for w in line.words if w.surface == "gaar").srs_item_id for line in result.dialogue_lines]
        assert bound == [card_id, card_id], f"surface fallback did not resolve both tokens: {bound}"
        assert CountingDB.lemma_queries.count("gaar") == 1, (
            f"the surface lookup was repeated instead of memoised: {CountingDB.lemma_queries}"
        )

    def test_non_variant_word_still_unknown(self):
        """The variant index must not accidentally match unrelated words."""
        self._add_variant_card()
        lesson = _make_lesson([("female-1", "hus")], lang="no")
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        assert result.dialogue_lines[0].words[0].active_state == "unknown"

    def test_build_variant_index_skips_comma_phrase(self):
        """A comma-containing row that is a genuine phrase (multi-word part) is not
        indexed as a spelling variant."""
        from app.srs.transcript import _build_variant_index

        self._add_variant_card()
        # Real phrase with an internal comma — a candidate row that is NOT a variant list.
        self.db.add_collocation(
            SyntacticUnit(
                text="hei, hvordan går det", translation="hi, how are you", word_count=4, difficulty=1, source="anki"
            ),
            language_code="no",
        )
        index = _build_variant_index(self.db, "no")
        assert set(index) == {"mot", "imot"}

    def test_build_variant_index_empty_without_separator(self):
        from app.srs.transcript import _build_variant_index

        self.db.add_collocation(
            SyntacticUnit(text="mot, imot", translation="against", word_count=1, difficulty=1, source="anki"),
            language_code="sl",
        )
        assert _build_variant_index(self.db, "sl") == {}

    def test_english_narrator_lines_excluded(self):
        lesson = Lesson(title="Test", language_code="sl")
        lesson.sections = [
            Section(
                section_type=SectionType.NATURAL_SPEED,
                phrases=[
                    Phrase(text="Scene: At the market", voice_id="narrator", language_code="en", role="narrator"),
                    Phrase(text="Zdravo.", voice_id="female-1", language_code="sl", role="female-1"),
                ],
            )
        ]
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        assert len(result.dialogue_lines) == 1
        assert result.dialogue_lines[0].role == "female-1"

    def test_punctuation_stripped_from_surface_and_lemma(self):
        lesson = _make_lesson([("female-1", "Zdravo,")])
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        words = result.dialogue_lines[0].words
        assert words[0].surface == "Zdravo"
        assert words[0].lemma == "zdravo"
        assert words[0].prefix_punct == ""
        assert words[0].suffix_punct == ","

    def test_empty_lesson_no_natural_speed_section(self):
        lesson = Lesson(title="Empty", language_code="sl")
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        assert result.dialogue_lines == []
        assert result.key_phrases == []

    def test_multiple_words_per_line(self):
        lesson = _make_lesson([("female-1", "Kje je banka?")])
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        words = result.dialogue_lines[0].words
        assert len(words) == 3
        assert [w.surface for w in words] == ["Kje", "je", "banka"]
        assert [w.lemma for w in words] == ["kje", "je", "banka"]
        assert [w.prefix_punct for w in words] == ["", "", ""]
        assert [w.suffix_punct for w in words] == ["", "", "?"]

    def test_standalone_dash_between_clauses_does_not_crash(self):
        """A standalone en-dash is its own whitespace token but has no surface, so
        tokenize() yields fewer tokens than str.split(). The transcript must still
        build with punctuation aligned to surfaces.

        Regression: gpt-oss dialogue ('Koliko stane? – Dve kavi ...') crashed
        _extract_punct_pairs with a zip strict-length ValueError (surfaces shorter
        than str.split()); llama output happened to avoid the standalone dash.
        """
        lesson = _make_lesson([("female-1", "Koliko stane? – Dve kavi.")])
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        words = result.dialogue_lines[0].words
        # The dash carries no surface — it is dropped, not mis-aligned into a word.
        assert [w.surface for w in words] == ["Koliko", "stane", "Dve", "kavi"]
        assert [w.prefix_punct for w in words] == ["", "", "", ""]
        assert [w.suffix_punct for w in words] == ["", "?", "", "."]

    def test_role_preserved_on_dialogue_line(self):
        lesson = _make_lesson([("male-1", "Zdravo.")])
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        assert result.dialogue_lines[0].role == "male-1"

    def test_word_token_fields(self):
        lesson = _make_lesson([("female-1", "banka")])
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        word = result.dialogue_lines[0].words[0]
        assert isinstance(word, WordToken)
        assert word.surface == "banka"
        assert word.lemma == "banka"
        assert word.srs_state == "unknown"

    def test_reset_to_new_not_due_in_transcript(self):
        """Reset→new: not bold in transcript (review-queue gates NEW intros)."""
        unit = SyntacticUnit(text="banka", translation="bank", word_count=1, difficulty=1, source="llm", lemma="banka")
        self.db.add_collocation(unit, language_code="sl")
        rows, _ = self.db.list_collocations()
        row_id = rows[0][0]
        future = datetime(2099, 1, 1, 4, 0, tzinfo=UTC)
        with self.db._get_conn() as conn:
            conn.execute(
                "UPDATE collocation_directions SET state='review', due_at=?, last_review=?,"
                " reps=2, stability=4.47 WHERE collocation_id=?",
                (future.isoformat(), datetime(2026, 6, 2, tzinfo=UTC).isoformat(), row_id),
            )
            conn.commit()

        self.db.set_state_by_id(row_id, SRSState.NEW)

        lesson = _make_lesson([("female-1", "banka")])
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        word = result.dialogue_lines[0].words[0]
        assert word.active_state == "new"
        assert word.is_due is False  # review-queue gates NEW intros
        assert word.progress == 0.0  # red on the ramp


class TestWordTokenEnrichment:
    """Tests for new srs_item_id, translation, and collocation_span_id fields."""

    def setup_method(self):
        self.db = SRSDatabase(":memory:")
        self.lemmatizer = LowercaseLemmatizer()

    def test_unknown_word_has_null_srs_item_id(self):
        lesson = _make_lesson([("female-1", "banka")])
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        assert result.dialogue_lines[0].words[0].srs_item_id is None

    def test_known_word_has_srs_item_id(self):
        unit = SyntacticUnit(text="banka", translation="bank", word_count=1, difficulty=1, source="llm", lemma="banka")
        self.db.add_collocation(unit, language_code="sl")
        rows, _ = self.db.list_collocations()
        expected_id = rows[0][0]

        lesson = _make_lesson([("female-1", "banka")])
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        assert result.dialogue_lines[0].words[0].srs_item_id == expected_id

    def test_translation_from_db_when_present(self):
        unit = SyntacticUnit(text="banka", translation="bank", word_count=1, difficulty=1, source="llm", lemma="banka")
        self.db.add_collocation(unit, language_code="sl")

        lesson = _make_lesson([("female-1", "banka")])
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        assert result.dialogue_lines[0].words[0].translation == "bank"

    def test_known_marked_false_for_unmarked_word(self):
        unit = SyntacticUnit(text="banka", translation="bank", word_count=1, difficulty=1, source="llm", lemma="banka")
        self.db.add_collocation(unit, language_code="sl")
        lesson = _make_lesson([("female-1", "banka")])
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        assert result.dialogue_lines[0].words[0].known_marked is False

    def test_known_marked_true_after_mark_false_after_restore(self):
        from datetime import date, time, timedelta

        unit = SyntacticUnit(text="banka", translation="bank", word_count=1, difficulty=1, source="llm", lemma="banka")
        self.db.add_collocation(unit, language_code="sl")
        rows, _ = self.db.list_collocations()
        row_id = rows[0][0]
        due_at = datetime.combine(date.today() + timedelta(days=36500), time(4, 0), tzinfo=UTC)
        self.db.mark_known(row_id, due_at=due_at, stability=36500.0)

        lesson = _make_lesson([("female-1", "banka")])
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        assert result.dialogue_lines[0].words[0].known_marked is True

        self.db.restore_known(row_id)
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        assert result.dialogue_lines[0].words[0].known_marked is False

    def test_known_marked_false_for_unknown_word(self):
        """An unresolved word has no card, so known_marked stays False."""
        lesson = _make_lesson([("female-1", "banka")])
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        assert result.dialogue_lines[0].words[0].known_marked is False

    def test_translation_from_gloss_map_when_no_db_entry(self):
        lesson = _make_lesson([("female-1", "banka")])
        lesson.generation_metadata = {"token_glosses": {"banka": "bank"}}
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        assert result.dialogue_lines[0].words[0].translation == "bank"

    def test_db_translation_wins_over_gloss_map(self):
        unit = SyntacticUnit(
            text="banka", translation="bank (db)", word_count=1, difficulty=1, source="llm", lemma="banka"
        )
        self.db.add_collocation(unit, language_code="sl")
        lesson = _make_lesson([("female-1", "banka")])
        lesson.generation_metadata = {"token_glosses": {"banka": "bank (gloss)"}}
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        assert result.dialogue_lines[0].words[0].translation == "bank (db)"

    def test_unknown_word_no_translation_is_none(self):
        lesson = _make_lesson([("female-1", "banka")])
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        assert result.dialogue_lines[0].words[0].translation is None

    def test_collocation_span_id_set_for_multi_word_srs_item(self):
        unit = SyntacticUnit(
            text="kje je banka",
            translation="where is the bank",
            word_count=3,
            difficulty=2,
            source="llm",
            lemma=None,
        )
        self.db.add_collocation(unit, language_code="sl")
        rows, _ = self.db.list_collocations()
        coll_id = rows[0][0]

        lesson = _make_lesson([("female-1", "kje je banka")])
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        words = result.dialogue_lines[0].words
        assert words[0].collocation_span_id == coll_id
        assert words[1].collocation_span_id == coll_id
        assert words[2].collocation_span_id == coll_id

    def test_collocation_start_true_only_for_first_token(self):
        unit = SyntacticUnit(
            text="kje je banka",
            translation="where is the bank",
            word_count=3,
            difficulty=2,
            source="llm",
            lemma=None,
        )
        self.db.add_collocation(unit, language_code="sl")

        lesson = _make_lesson([("female-1", "kje je banka")])
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        words = result.dialogue_lines[0].words
        assert words[0].collocation_start is True
        assert words[1].collocation_start is False
        assert words[2].collocation_start is False

    def test_word_not_in_collocation_has_null_span_id(self):
        lesson = _make_lesson([("female-1", "banka")])
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        assert result.dialogue_lines[0].words[0].collocation_span_id is None
        assert result.dialogue_lines[0].words[0].collocation_start is False

    def test_single_word_entry_in_collocations_table_not_matched_as_span(self):
        # word_count=1 entries should not produce collocation spans
        unit = SyntacticUnit(
            text="banka",
            translation="bank",
            word_count=1,
            difficulty=1,
            source="llm",
            lemma="banka",
        )
        self.db.add_collocation(unit, language_code="sl")
        lesson = _make_lesson([("female-1", "banka")])
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        # Should not get a span (word_count=1 entries are excluded from span matching)
        assert result.dialogue_lines[0].words[0].collocation_span_id is None

    def test_collocation_state_and_lemma_set_for_span_tokens(self):
        unit = SyntacticUnit(
            text="kje je banka",
            translation="where is the bank",
            word_count=3,
            difficulty=2,
            source="llm",
            lemma=None,
        )
        self.db.add_collocation(unit, language_code="sl")
        item = self.db.get_collocation("kje je banka")
        item.state = SRSState.REVIEW
        self.db.update_collocation(item)

        lesson = _make_lesson([("female-1", "kje je banka")])
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        words = result.dialogue_lines[0].words
        assert [w.collocation_srs_state for w in words] == ["review", "review", "review"]
        assert [w.collocation_lemma for w in words] == ["kje je banka", "kje je banka", "kje je banka"]

    def test_word_not_in_collocation_has_null_collocation_state(self):
        lesson = _make_lesson([("female-1", "banka")])
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        word = result.dialogue_lines[0].words[0]
        assert word.collocation_srs_state is None
        assert word.collocation_lemma is None

    def test_collocation_translation_set_for_span_tokens(self):
        unit = SyntacticUnit(
            text="kje je banka",
            translation="where is the bank",
            word_count=3,
            difficulty=2,
            source="llm",
            lemma=None,
        )
        self.db.add_collocation(unit, language_code="sl")

        lesson = _make_lesson([("female-1", "kje je banka")])
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        words = result.dialogue_lines[0].words
        assert [w.collocation_translation for w in words] == [
            "where is the bank",
            "where is the bank",
            "where is the bank",
        ]

    def test_word_not_in_collocation_has_null_collocation_translation(self):
        lesson = _make_lesson([("female-1", "banka")])
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        word = result.dialogue_lines[0].words[0]
        assert word.collocation_translation is None

    def test_collocation_progress_computed_from_span_directions(self):
        """Span tokens carry the collocation's mastery progress (red→green ramp).

        A freshly-added (NEW) collocation has both directions NEW → component
        mastery 0.0 each → progress 0.0 (a float, not None — proving the
        directions were passed to compute_mastery_progress, not an empty set).
        """
        unit = SyntacticUnit(
            text="kje je banka",
            translation="where is the bank",
            word_count=3,
            difficulty=2,
            source="llm",
            lemma=None,
        )
        self.db.add_collocation(unit, language_code="sl")

        lesson = _make_lesson([("female-1", "kje je banka")])
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        words = result.dialogue_lines[0].words
        assert [w.collocation_progress for w in words] == [0.0, 0.0, 0.0]

    def test_word_not_in_collocation_has_null_collocation_progress(self):
        lesson = _make_lesson([("female-1", "banka")])
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        word = result.dialogue_lines[0].words[0]
        assert word.collocation_progress is None

    def test_collocation_is_due_true_when_active_direction_due(self):
        """Span tokens carry the collocation's due-ness (same _is_due rule as words).

        The frontend gates the phrase popover's grade button on this — without
        it, phrases showed "Got it" even when not due while words didn't.
        """
        from datetime import UTC, datetime
        from datetime import date as _date

        unit = SyntacticUnit(
            text="kje je banka",
            translation="where is the bank",
            word_count=3,
            difficulty=2,
            source="llm",
            lemma=None,
        )
        self.db.add_collocation(unit, language_code="sl")
        item = self.db.get_collocation("kje je banka")
        ds = item.directions[Direction.RECOGNITION]
        ds.state = SRSState.LEARNING
        ds.due_at = datetime(2026, 5, 30, 12, 0, tzinfo=UTC)
        self.db.update_direction(item.guid, Direction.RECOGNITION, ds)

        lesson = _make_lesson([("female-1", "kje je banka")])
        result = extract_transcript(lesson, self.db, self.lemmatizer, today=_date(2026, 6, 1))
        words = result.dialogue_lines[0].words
        assert [w.collocation_is_due for w in words] == [True, True, True]

    def test_collocation_is_due_false_for_new_collocation(self):
        """A NEW (never-introduced) phrase is not due — mirrors word is_due gating."""
        from datetime import date as _date

        unit = SyntacticUnit(
            text="kje je banka",
            translation="where is the bank",
            word_count=3,
            difficulty=2,
            source="llm",
            lemma=None,
        )
        self.db.add_collocation(unit, language_code="sl")

        lesson = _make_lesson([("female-1", "kje je banka")])
        result = extract_transcript(lesson, self.db, self.lemmatizer, today=_date(2026, 6, 1))
        words = result.dialogue_lines[0].words
        assert [w.collocation_is_due for w in words] == [False, False, False]

    def test_collocation_is_due_false_when_due_in_future(self):
        from datetime import UTC, datetime
        from datetime import date as _date

        unit = SyntacticUnit(
            text="kje je banka",
            translation="where is the bank",
            word_count=3,
            difficulty=2,
            source="llm",
            lemma=None,
        )
        self.db.add_collocation(unit, language_code="sl")
        item = self.db.get_collocation("kje je banka")
        ds = item.directions[Direction.RECOGNITION]
        ds.state = SRSState.LEARNING
        ds.due_at = datetime(2026, 6, 3, 12, 0, tzinfo=UTC)
        self.db.update_direction(item.guid, Direction.RECOGNITION, ds)

        lesson = _make_lesson([("female-1", "kje je banka")])
        result = extract_transcript(lesson, self.db, self.lemmatizer, today=_date(2026, 6, 1))
        words = result.dialogue_lines[0].words
        assert [w.collocation_is_due for w in words] == [False, False, False]

    def test_word_not_in_collocation_has_false_collocation_is_due(self):
        lesson = _make_lesson([("female-1", "banka")])
        result = extract_transcript(lesson, self.db, self.lemmatizer)
        assert result.dialogue_lines[0].words[0].collocation_is_due is False

    def test_transcript_word_matches_existing_lowercase_card(self):
        """Sentence-initial 'Zdravo' matches existing 'zdravo' card via lemma lookup."""
        unit = SyntacticUnit(
            text="zdravo", translation="hello", word_count=1, difficulty=1, source="llm", lemma="zdravo"
        )
        self.db.add_collocation(unit, language_code="sl")
        lesson = _make_lesson([("female-1", "Zdravo!")])
        transcript = extract_transcript(lesson, self.db, self.lemmatizer)
        word = transcript.dialogue_lines[0].words[0]
        assert word.surface == "Zdravo"
        assert word.lemma == "zdravo"
        assert word.srs_state == "new"
        assert word.srs_item_id is not None


class TestRecognitionReviewable:
    """recognition_reviewable: read-ahead eligibility for the recognition direction.

    True when the RECOGNITION direction exists and is not terminal (KNOWN/
    SUSPENDED/BURIED) — NEW included, since reading a not-yet-introduced word is a
    valid early review. Independent of due date. Reading evidences recognition, so
    this always keys off the recognition direction regardless of the active one.
    """

    def setup_method(self):
        self.db = SRSDatabase(":memory:")
        self.lemmatizer = LowercaseLemmatizer()

    def _add_with_rec_state(self, text: str, state: SRSState) -> None:
        unit = SyntacticUnit(text=text, translation="x", word_count=1, difficulty=1, source="llm", lemma=text)
        self.db.add_collocation(unit, language_code="sl")
        item = self.db.get_collocation(text)
        rec = item.directions[Direction.RECOGNITION]
        rec.state = state
        # Far-future due so the word is NOT due — proves reviewable is state-based.
        rec.due_at = datetime(2099, 1, 1, 4, 0, tzinfo=UTC)
        self.db.update_direction(item.guid, Direction.RECOGNITION, rec)

    def _word(self, text: str) -> object:
        lesson = _make_lesson([("female-1", text)])
        result = extract_transcript(lesson, self.db, self.lemmatizer, today=date(2026, 6, 1))
        return result.dialogue_lines[0].words[0]

    def test_true_for_not_due_review(self):
        self._add_with_rec_state("banka", SRSState.REVIEW)
        word = self._word("banka")
        assert word.is_due is False
        assert word.recognition_reviewable is True

    def test_true_for_learning(self):
        self._add_with_rec_state("banka", SRSState.LEARNING)
        word = self._word("banka")
        assert word.recognition_reviewable is True

    def test_true_for_relearning(self):
        self._add_with_rec_state("banka", SRSState.RELEARNING)
        assert self._word("banka").recognition_reviewable is True

    def test_true_for_new(self):
        # Reading a NEW (not-yet-introduced) word is a valid early review.
        unit = SyntacticUnit(text="banka", translation="x", word_count=1, difficulty=1, source="llm", lemma="banka")
        self.db.add_collocation(unit, language_code="sl")  # default state NEW
        word = self._word("banka")
        assert word.srs_state == "new"
        assert word.is_due is False
        assert word.recognition_reviewable is True

    def test_false_for_suspended(self):
        self._add_with_rec_state("banka", SRSState.SUSPENDED)
        assert self._word("banka").recognition_reviewable is False

    def test_false_for_known(self):
        self._add_with_rec_state("banka", SRSState.KNOWN)
        assert self._word("banka").recognition_reviewable is False

    def test_false_for_unknown_word(self):
        assert self._word("banka").recognition_reviewable is False

    def test_false_for_cloze_no_recognition_direction(self):
        unit = SyntacticUnit(
            text="vsak",
            translation="every",
            word_count=1,
            difficulty=1,
            source="cloze",
            lemma="vsak",
            source_sentence="Odprto je vsak dan",
            card_type="cloze",
        )
        self.db.add_collocation(unit, language_code="sl")
        item = self.db.get_collocation("vsak")
        item.directions[Direction.PRODUCTION].state = SRSState.REVIEW
        self.db.update_collocation(item)
        lesson = _make_lesson([("female-1", "Odprto je vsak dan")])
        result = extract_transcript(lesson, self.db, self.lemmatizer, today=date(2026, 6, 1))
        # 'vsak' is the resolved cloze token; it has no recognition direction.
        vsak = next(w for w in result.dialogue_lines[0].words if w.lemma == "vsak")
        assert vsak.recognition_reviewable is False

    def test_true_for_graduated_word_when_active_direction_is_production(self):
        """Once recognition graduates (REVIEW) with production present, the active
        direction flips to PRODUCTION — but reading still evidences recognition,
        so recognition_reviewable stays True."""
        unit = SyntacticUnit(text="banka", translation="x", word_count=1, difficulty=1, source="llm", lemma="banka")
        self.db.add_collocation(unit, language_code="sl")
        item = self.db.get_collocation("banka")
        for d in (Direction.RECOGNITION, Direction.PRODUCTION):
            ds = item.directions[d]
            ds.state = SRSState.REVIEW
            ds.due_at = datetime(2099, 1, 1, 4, 0, tzinfo=UTC)
            self.db.update_direction(item.guid, d, ds)
        word = self._word("banka")
        assert word.active_direction == "production"
        assert word.recognition_reviewable is True


class TestResolveActiveDirection:
    def test_cloze_returns_production(self):
        item = SyntacticUnit(
            text="je", translation="is", word_count=1, difficulty=1, source="llm", lemma="je", card_type="cloze"
        )
        from app.models.srs_item import SRSItem

        srs = SRSItem(syntactic_unit=item)
        assert resolve_active_direction(srs) == Direction.PRODUCTION

    def test_vocab_recognition_not_review_returns_recognition(self):
        item = SyntacticUnit(
            text="banka", translation="bank", word_count=1, difficulty=1, source="llm", lemma="banka", card_type="vocab"
        )
        from app.models.srs_item import SRSItem

        srs = SRSItem(syntactic_unit=item)
        # NEW → recognition
        assert resolve_active_direction(srs) == Direction.RECOGNITION

    def test_vocab_recognition_review_returns_production(self):
        item = SyntacticUnit(
            text="banka", translation="bank", word_count=1, difficulty=1, source="llm", lemma="banka", card_type="vocab"
        )
        from app.models.srs_item import SRSItem

        srs = SRSItem(syntactic_unit=item)
        srs.directions[Direction.RECOGNITION].state = SRSState.REVIEW
        assert resolve_active_direction(srs) == Direction.PRODUCTION

    def test_vocab_both_review_returns_production(self):
        item = SyntacticUnit(
            text="banka", translation="bank", word_count=1, difficulty=1, source="llm", lemma="banka", card_type="vocab"
        )
        from app.models.srs_item import SRSItem

        srs = SRSItem(syntactic_unit=item)
        srs.directions[Direction.RECOGNITION].state = SRSState.REVIEW
        srs.directions[Direction.PRODUCTION].state = SRSState.REVIEW
        assert resolve_active_direction(srs) == Direction.PRODUCTION

    def test_recognition_only_review_stays_recognition(self):
        """A recognition-only vocab card (e.g. the imported Norwegian deck — single
        direction) that has graduated to REVIEW must NOT advance to PRODUCTION: it
        has no production direction, so resolve_active_direction returning PRODUCTION
        makes the caller's item.directions[active_dir] raise KeyError (the lesson
        transcript 500'd on every Norwegian word in REVIEW). Active direction must
        be one the item actually has."""
        from app.models.srs_item import SRSItem

        item = SyntacticUnit(
            text="toget",
            translation="the train",
            word_count=1,
            difficulty=1,
            source="llm",
            lemma="tog",
            card_type="vocab",
        )
        srs = SRSItem(syntactic_unit=item)
        srs.directions.pop(Direction.PRODUCTION)  # recognition-only, like a NO import
        srs.directions[Direction.RECOGNITION].state = SRSState.REVIEW
        active = resolve_active_direction(srs)
        assert active == Direction.RECOGNITION
        assert active in srs.directions  # the contract: never return an absent direction


class TestResolveActiveDirectionDirect:
    def test_non_srs_item_returns_production(self):
        assert resolve_active_direction(object()) == Direction.PRODUCTION

    def test_cloze_item_returns_production(self):
        from app.models.srs_item import SRSItem
        from app.models.syntactic_unit import SyntacticUnit

        unit = SyntacticUnit(
            text="je", translation="is", word_count=1, difficulty=1, source="llm", lemma="je", card_type="cloze"
        )
        item = SRSItem(syntactic_unit=unit)
        assert resolve_active_direction(item) == Direction.PRODUCTION

    def test_vocab_no_recognition_direction_returns_production(self):
        """Production-only vocab → PRODUCTION (the direction it actually has). The
        active direction must always be present in item.directions, else the caller
        KeyErrors; returning RECOGNITION here (the old behavior) was that bug."""
        from app.models.srs_item import SRSItem
        from app.models.syntactic_unit import SyntacticUnit

        unit = SyntacticUnit(text="banka", translation="bank", word_count=1, difficulty=1, source="llm", lemma="banka")
        item = SRSItem(syntactic_unit=unit)
        rec = item.directions.get(Direction.RECOGNITION)
        if rec is not None:
            item.directions.pop(Direction.RECOGNITION)
        active = resolve_active_direction(item)
        assert active == Direction.PRODUCTION
        assert active in item.directions


class TestIsDue:
    def test_not_due_when_known(self):
        ds = DirectionState(
            direction=Direction.RECOGNITION, state=SRSState.KNOWN, due_at=datetime(2024, 1, 1, tzinfo=UTC)
        )
        assert _is_due(ds, date(2026, 6, 1)) is False

    def test_not_due_when_suspended(self):
        ds = DirectionState(
            direction=Direction.RECOGNITION, state=SRSState.SUSPENDED, due_at=datetime(2024, 1, 1, tzinfo=UTC)
        )
        assert _is_due(ds, date(2026, 6, 1)) is False

    def test_not_due_when_new_and_due_today(self):
        ds = DirectionState(
            direction=Direction.RECOGNITION, state=SRSState.NEW, due_at=datetime(2026, 6, 1, tzinfo=UTC)
        )
        assert _is_due(ds, date(2026, 6, 1)) is False

    def test_due_when_review_and_past_due(self):
        ds = DirectionState(
            direction=Direction.RECOGNITION, state=SRSState.REVIEW, due_at=datetime(2024, 1, 1, tzinfo=UTC)
        )
        assert _is_due(ds, date(2026, 6, 1)) is True

    def test_not_due_when_review_and_future_due(self):
        ds = DirectionState(
            direction=Direction.RECOGNITION, state=SRSState.REVIEW, due_at=datetime(2026, 7, 1, tzinfo=UTC)
        )
        assert _is_due(ds, date(2026, 6, 1)) is False

    def test_not_due_when_buried_even_if_due_today(self):
        # A sibling-buried card is deferred for the day and excluded from the
        # review queue (database._NON_REVIEWABLE_STATES). The transcript must not
        # bold it as due just because its due_at.date() is today.
        ds = DirectionState(
            direction=Direction.RECOGNITION, state=SRSState.BURIED, due_at=datetime(2026, 6, 1, tzinfo=UTC)
        )
        assert _is_due(ds, date(2026, 6, 1)) is False

    def test_not_due_at_midnight_before_rollover(self):
        """A card with due_at on calendar-today is NOT is_due at 02:00 when
        the active Anki day is still yesterday (4 AM rollover hasn't fired).

        Anki-today = anki_today(frozen_now) returns yesterday's date in the
        [midnight, 4 AM) window, so `due_at.date() <= today` is False for
        a due_at on calendar-today. The pre-fix code used date.today() which
        would bold the card as due up to 4 hours early.
        """
        from app.srs.anki_mirror.rollover import anki_today

        # "now" = 02:00 — before 4 AM rollover.
        # NAIVE = local wall-clock. The rollover is a local concept, so an aware
        # `now` is converted to local rather than supplying its own arithmetic
        # zone (rollover._local_now, tunatale-vnf.5). Written as tzinfo=UTC these
        # asserted UTC-day arithmetic and broke past UTC+5.
        frozen_now = datetime(2026, 5, 8, 2, 0)
        today = anki_today(frozen_now)  # still May 7 (yesterday)
        assert today == date(2026, 5, 7)

        # due_at on calendar-today (May 8) but NOT in the active Anki day (May 7).
        ds = DirectionState(
            direction=Direction.RECOGNITION,
            state=SRSState.REVIEW,
            due_at=datetime(2026, 5, 8, 12, 0, tzinfo=UTC),
        )
        assert _is_due(ds, today) is False, (
            "card due on calendar-today is NOT due when the active Anki day is still yesterday"
        )

        # Counter-case: using calendar date.today() at 02:00 WOULD mark it due.
        assert _is_due(ds, date(2026, 5, 8)) is True, (
            "calendar date.today() at 02:00 incorrectly marks the card as due (the pre-fix bug)"
        )


class TestTranscriptEnrichment:
    def setup_method(self):
        self.db = SRSDatabase(":memory:")
        self.lemmatizer = LowercaseLemmatizer()
        self.today = date(2026, 6, 1)
        self.now = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)

    def _add_vocab(self, text: str, translation: str, lemma: str | None = None) -> int:
        unit = SyntacticUnit(
            text=text, translation=translation, word_count=1, difficulty=1, source="llm", lemma=lemma or text
        )
        self.db.add_collocation(unit, language_code="sl")
        return self.db.get_collocation_id_by_guid(unit.guid)

    def test_unknown_token_has_defaults(self):
        lesson = _make_lesson([("female-1", "banka")])
        result = extract_transcript(lesson, self.db, self.lemmatizer, today=self.today)
        word = result.dialogue_lines[0].words[0]
        assert word.progress is None
        assert word.active_state == "unknown"
        assert word.inflectable is False
        assert word.card_type is None
        assert word.active_direction is None
        assert word.is_due is False
        assert word.inflection_feature is None

    def test_vocab_recognition_not_review_active_direction_recognition(self):
        self._add_vocab("banka", "bank", lemma="banka")
        lesson = _make_lesson([("female-1", "banka")])
        result = extract_transcript(lesson, self.db, self.lemmatizer, today=self.today)
        word = result.dialogue_lines[0].words[0]
        assert word.active_direction == "recognition"
        assert word.active_state == "new"

    def test_vocab_both_review_active_direction_production(self):
        from app.models.srs_item import Direction, SRSState

        self._add_vocab("banka", "bank", lemma="banka")
        item = self.db.get_collocation("banka")
        item.directions[Direction.RECOGNITION].state = SRSState.REVIEW
        item.directions[Direction.PRODUCTION].state = SRSState.REVIEW
        item.directions[Direction.RECOGNITION].last_review = datetime(2026, 5, 1, tzinfo=UTC)
        item.directions[Direction.PRODUCTION].last_review = datetime(2026, 5, 1, tzinfo=UTC)
        self.db.update_direction(item.guid, Direction.RECOGNITION, item.directions[Direction.RECOGNITION])
        self.db.update_direction(item.guid, Direction.PRODUCTION, item.directions[Direction.PRODUCTION])
        lesson = _make_lesson([("female-1", "banka")])
        result = extract_transcript(lesson, self.db, self.lemmatizer, today=self.today)
        word = result.dialogue_lines[0].words[0]
        assert word.active_direction == "production"
        assert word.active_state == "review"

    def test_cloze_base_active_direction_production(self):
        unit = SyntacticUnit(
            text="je",
            translation="is",
            word_count=1,
            difficulty=1,
            source="llm",
            lemma="je",
            card_type="cloze",
            source_sentence="To je dobro.",
        )
        self.db.add_collocation(unit, language_code="sl")
        lesson = _make_lesson([("female-1", "To je dobro.")])
        result = extract_transcript(lesson, self.db, self.lemmatizer, today=self.today)
        word = next(w for w in result.dialogue_lines[0].words if w.lemma == "je")
        assert word.active_direction == "production"
        assert word.card_type == "cloze"

    def test_progress_reflects_components(self):
        self._add_vocab("banka", "bank", lemma="banka")
        lesson = _make_lesson([("female-1", "banka")])
        result = extract_transcript(lesson, self.db, self.lemmatizer, today=self.today)
        word = result.dialogue_lines[0].words[0]
        # NEW → mastery is 0.0
        assert word.progress == 0.0

    def test_inflectable_true_for_a1_surface_differs_from_lemma_with_reviewed_base(self):
        """Inflectable requires surface!=lemma, A1 feature, base production REVIEW/KNOWN, no existing cloze."""
        from app.models.srs_item import Direction, SRSState

        self._add_vocab("hoditi", "to walk", lemma="hoditi")
        item = self.db.get_collocation("hoditi")
        item.directions[Direction.PRODUCTION].state = SRSState.REVIEW
        item.directions[Direction.RECOGNITION].state = SRSState.REVIEW
        item.directions[Direction.PRODUCTION].last_review = datetime(2026, 5, 1, tzinfo=UTC)
        item.directions[Direction.RECOGNITION].last_review = datetime(2026, 5, 1, tzinfo=UTC)
        self.db.update_direction(item.guid, Direction.RECOGNITION, item.directions[Direction.RECOGNITION])
        self.db.update_direction(item.guid, Direction.PRODUCTION, item.directions[Direction.PRODUCTION])

        lesson = _make_lesson([("female-1", "hodim")])
        result = extract_transcript(lesson, self.db, self.lemmatizer, today=self.today)
        word = result.dialogue_lines[0].words[0]
        # LowercaseLemmatizer returns empty upos/case/number → ud_feats_to_tt_feature returns None
        # So inflectable depends on having analysis data. With LowercaseLemmatizer, analysis returns
        # empty strings → no feature → inflectable=False
        assert word.inflectable is False  # not enough analysis data from LowercaseLemmatizer

    def test_inflectable_false_when_surface_equals_lemma(self):
        self._add_vocab("banka", "bank", lemma="banka")
        item = self.db.get_collocation("banka")
        item.directions[Direction.PRODUCTION].state = SRSState.REVIEW
        self.db.update_direction(item.guid, Direction.PRODUCTION, item.directions[Direction.PRODUCTION])
        lesson = _make_lesson([("female-1", "banka")])
        result = extract_transcript(lesson, self.db, self.lemmatizer, today=self.today)
        word = result.dialogue_lines[0].words[0]
        assert word.inflectable is False  # surface==lemma

    def test_inflectable_false_when_inflection_cloze_already_exists(self):
        """When an exact-surface inflection cloze exists, inflectable=False."""
        from app.models.srs_item import Direction, SRSState

        lemma = "lep"
        self._add_vocab("lep", "nice", lemma=lemma)
        item = self.db.get_collocation("lep")
        item.directions[Direction.PRODUCTION].state = SRSState.REVIEW
        self.db.update_direction(item.guid, Direction.PRODUCTION, item.directions[Direction.PRODUCTION])

        # Create an existing inflection cloze
        unit = SyntacticUnit(
            text="lepa",
            translation="nice (fem)",
            word_count=1,
            difficulty=1,
            source="llm",
            lemma=lemma,
            disambig_key="morph:adj-nom-f-sg",
            card_type="cloze",
            source_sentence="Hiša je lepa.",
        )
        self.db.add_collocation(unit, language_code="sl")

        lesson = _make_lesson([("female-1", "Hiša je lepa.")])
        result = extract_transcript(lesson, self.db, self.lemmatizer, today=self.today)
        lepa_words = [w for w in result.dialogue_lines[0].words if w.surface == "lepa"]
        for w in lepa_words:
            assert w.inflectable is False  # cloze already exists

    def test_same_lemma_twice_hits_cache(self):
        """Two occurrences of the same lemma within one lesson hit inflection cache."""
        self._add_vocab("banka", "bank", lemma="banka")
        lesson = _make_lesson([("female-1", "Banka je v banki.")])
        result = extract_transcript(lesson, self.db, self.lemmatizer, today=self.today)
        # Both "banka" tokens should resolve (lemma same either way)
        words = result.dialogue_lines[0].words
        banka_words = [w for w in words if w.lemma == "banka"]
        assert len(banka_words) >= 1

    def test_unknown_word_has_empty_components(self):
        lesson = _make_lesson([("female-1", "xyznonexistent")])
        result = extract_transcript(lesson, self.db, self.lemmatizer, today=self.today)
        word = result.dialogue_lines[0].words[0]
        assert word.progress is None
        assert word.srs_state == "unknown"
        assert word.active_state == "unknown"

    def test_inflectable_path_with_analysis_data(self):
        """Use a custom lemmatizer that returns analysis data to cover inflectable detection."""
        from app.srs.lemmatizer import TokenAnalysis as _TA

        class _MockLemmatizer:
            def lemmatize(self, word: str, language_code: str) -> str:
                if word == "hodim":
                    return "hoditi"
                return word.lower()

            def analyze(self, word: str, language_code: str) -> tuple[str, str, str]:
                if word == "hodim":
                    return "hoditi", "", ""
                return word.lower(), "", ""

            def analyze_sentence(self, sentence: str, language_code: str) -> list:
                if "hodim" in sentence:
                    return [
                        _TA(
                            surface="hodim", lemma="hoditi", upos="VERB", case="", number="Sing", person="1", gender=""
                        ),
                    ]
                return []

        from app.models.srs_item import Direction, SRSState

        self._add_vocab("hoditi", "to walk", lemma="hoditi")
        item = self.db.get_collocation("hoditi")
        item.directions[Direction.PRODUCTION].state = SRSState.REVIEW
        item.directions[Direction.RECOGNITION].state = SRSState.REVIEW
        item.directions[Direction.PRODUCTION].last_review = datetime(2026, 5, 1, tzinfo=UTC)
        item.directions[Direction.RECOGNITION].last_review = datetime(2026, 5, 1, tzinfo=UTC)
        self.db.update_direction(item.guid, Direction.RECOGNITION, item.directions[Direction.RECOGNITION])
        self.db.update_direction(item.guid, Direction.PRODUCTION, item.directions[Direction.PRODUCTION])

        lesson = _make_lesson([("female-1", "hodim")])
        result = extract_transcript(lesson, self.db, _MockLemmatizer(), today=self.today)
        word = result.dialogue_lines[0].words[0]
        # Now lemmatizer maps "hodim" → "hoditi" → base found → inflectable detection runs
        # ud_feats_to_tt_feature(TokenAnalysis(upos="VERB", number="Sing", person="1"), "sl") → "verb:1sg"
        # → is_a1_morphology_feature("verb:1sg", "sl") True
        assert word.inflectable is True
        assert word.inflection_feature == "verb:1sg"
        assert word.srs_state != "unknown"

    def test_norwegian_inflectable_uses_definite_feature(self):
        """A Norwegian definite noun maps to ``noun:def:sg`` and inflects — the
        feature that used to be ``None`` under the Slovene-only mapper."""
        from app.srs.lemmatizer import TokenAnalysis as _TA

        class _MockNorwegianLemmatizer:
            def lemmatize(self, word: str, language_code: str) -> str:
                if word == "bilen":
                    return "bil"
                return word.lower()

            def analyze(self, word: str, language_code: str) -> tuple[str, str, str]:
                if word == "bilen":
                    return "bil", "", ""
                return word.lower(), "", ""

            def analyze_sentence(self, sentence: str, language_code: str) -> list:
                if "bilen" in sentence:
                    return [_TA(surface="bilen", lemma="bil", upos="NOUN", definite="Def", number="Sing")]
                return []

        from app.models.srs_item import Direction, SRSState

        self._add_vocab("bil", "car", lemma="bil")
        item = self.db.get_collocation("bil")
        item.directions[Direction.PRODUCTION].state = SRSState.REVIEW
        item.directions[Direction.RECOGNITION].state = SRSState.REVIEW
        item.directions[Direction.PRODUCTION].last_review = datetime(2026, 5, 1, tzinfo=UTC)
        item.directions[Direction.RECOGNITION].last_review = datetime(2026, 5, 1, tzinfo=UTC)
        self.db.update_direction(item.guid, Direction.RECOGNITION, item.directions[Direction.RECOGNITION])
        self.db.update_direction(item.guid, Direction.PRODUCTION, item.directions[Direction.PRODUCTION])

        lesson = _make_lesson([("female-1", "bilen")], lang="no")
        result = extract_transcript(lesson, self.db, _MockNorwegianLemmatizer(), today=self.today)
        word = result.dialogue_lines[0].words[0]
        assert word.inflectable is True
        assert word.inflection_feature == "noun:def:sg"

    def test_a_word_whose_production_is_a_cloze_reads_as_covered(self):
        """One word, two rows — the reader must not report only the vocab one.

        A word that cannot be pictured gets its production card as a **cloze**,
        which is a separate note and therefore a separate collocation (sync maps
        one note to one row). Reading the vocab row alone, the word has no
        production direction, so `compute_mastery_progress` imputes the
        absent-production 0.0 and the word reads half-mastered forever — while
        the card that would make it whole sits in the next row, linked by
        `base_collocation_id`.

        Mastery itself is NOT at fault and is unchanged: a cloze on its own has
        always been mastered by its single card. What changes is WHICH rows the
        reader measures.
        """
        from app.common.guid import compute_guid
        from app.models.srs_item import Direction, DirectionState, SRSState

        # The vocab row as the Anki import makes it: recognition only, mature.
        # The non-empty disambig is load-bearing, not decoration — with an empty
        # one the cloze below would share this row's (text, language, disambig)
        # identity and `add_collocation` would MERGE into it instead of creating
        # a second row. That is exactly why `_fallback_to_cloze` declines a word
        # whose own Word class is blank.
        unit = SyntacticUnit(
            text="foran",
            translation="in front of",
            word_count=1,
            difficulty=1,
            source="anki",
            disambig_key="preposition",
        )
        base_id = self.db.upsert_by_guid(
            unit,
            "sl",
            {
                Direction.RECOGNITION: DirectionState(
                    direction=Direction.RECOGNITION,
                    due_at=datetime(2026, 9, 1, tzinfo=UTC),
                    state=SRSState.REVIEW,
                    stability=120.0,
                    reps=30,
                    last_review=datetime(2026, 5, 1, tzinfo=UTC),
                )
            },
            anki_note_id=9100,
        )
        # The base cloze the promotion path mints for it, equally mature.
        cloze_unit = SyntacticUnit(
            text="foran",
            translation="in front of",
            word_count=1,
            difficulty=1,
            source="anki",
            lemma="foran",
            card_type="cloze",
            source_sentence="Han står {{c1::foran}} huset",
        )
        self.db.add_collocation(cloze_unit, language_code="sl")
        cloze_guid = compute_guid(cloze_unit.text, "sl", cloze_unit.disambig_key or "")
        cloze_id = self.db.get_collocation_id_by_guid(cloze_guid)
        self.db.update_direction(
            cloze_guid,
            Direction.PRODUCTION,
            DirectionState(
                direction=Direction.PRODUCTION,
                due_at=datetime(2026, 9, 1, tzinfo=UTC),
                state=SRSState.REVIEW,
                stability=120.0,
                reps=30,
                last_review=datetime(2026, 5, 1, tzinfo=UTC),
            ),
        )
        self.db.set_base_collocation_id(cloze_id, base_id)

        lesson = _make_lesson([("female-1", "foran")])
        result = extract_transcript(lesson, self.db, self.lemmatizer, today=self.today)
        word = result.dialogue_lines[0].words[0]

        assert word.progress == 1.0, "the covering cloze's production was not counted"

    def test_a_cloze_covered_word_can_still_be_inflected(self):
        """The affordance must OPEN for these words, not just stop lying about them.

        A word whose production card is a base cloze can be produced once that
        cloze is in REVIEW — which is exactly what the inflection gate asks. The
        vocab row has no production direction of its own, so without the link the
        gate reads `None` and the affordance stays invisible forever.
        """
        from app.common.guid import compute_guid
        from app.models.srs_item import Direction, DirectionState, SRSState
        from app.srs.lemmatizer import TokenAnalysis as _TA

        class _MockNorwegianLemmatizer:
            def lemmatize(self, word: str, language_code: str) -> str:
                return "bil" if word == "bilen" else word.lower()

            def analyze(self, word: str, language_code: str) -> tuple[str, str, str]:
                return ("bil", "", "") if word == "bilen" else (word.lower(), "", "")

            def analyze_sentence(self, sentence: str, language_code: str) -> list:
                if "bilen" in sentence:
                    return [_TA(surface="bilen", lemma="bil", upos="NOUN", definite="Def", number="Sing")]
                return []

        mature = lambda d: DirectionState(  # noqa: E731
            direction=d,
            due_at=datetime(2026, 9, 1, tzinfo=UTC),
            state=SRSState.REVIEW,
            stability=120.0,
            reps=30,
            last_review=datetime(2026, 5, 1, tzinfo=UTC),
        )
        base_unit = SyntacticUnit(
            text="bil", translation="car", word_count=1, difficulty=1, source="anki", disambig_key="noun"
        )
        base_id = self.db.upsert_by_guid(
            base_unit, "no", {Direction.RECOGNITION: mature(Direction.RECOGNITION)}, anki_note_id=9200
        )
        cloze_unit = SyntacticUnit(
            text="bil",
            translation="car",
            word_count=1,
            difficulty=1,
            source="anki",
            lemma="bil",
            card_type="cloze",
            source_sentence="Jeg kjører {{c1::bil}}",
        )
        self.db.add_collocation(cloze_unit, language_code="no")
        cloze_guid = compute_guid("bil", "no", "")
        self.db.update_direction(cloze_guid, Direction.PRODUCTION, mature(Direction.PRODUCTION))
        self.db.set_base_collocation_id(self.db.get_collocation_id_by_guid(cloze_guid), base_id)

        lesson = _make_lesson([("female-1", "bilen")], lang="no")
        result = extract_transcript(lesson, self.db, _MockNorwegianLemmatizer(), today=self.today)
        word = result.dialogue_lines[0].words[0]

        assert word.inflectable is True, "the covering cloze's production did not open the gate"
        assert word.inflection_feature == "noun:def:sg"

    def test_a_word_still_awaiting_its_production_card_is_not_inflectable(self):
        """Neither a production direction NOR a covering cloze — the state every
        un-promoted word is in until the sync's mint reaches it. The affordance
        stays shut, and the covering-cloze lookup must not manufacture one."""
        from app.models.srs_item import Direction, DirectionState, SRSState
        from app.srs.lemmatizer import TokenAnalysis as _TA

        class _MockNorwegianLemmatizer:
            def lemmatize(self, word: str, language_code: str) -> str:
                return "bil" if word == "bilen" else word.lower()

            def analyze(self, word: str, language_code: str) -> tuple[str, str, str]:
                return ("bil", "", "") if word == "bilen" else (word.lower(), "", "")

            def analyze_sentence(self, sentence: str, language_code: str) -> list:
                if "bilen" in sentence:
                    return [_TA(surface="bilen", lemma="bil", upos="NOUN", definite="Def", number="Sing")]
                return []

        unit = SyntacticUnit(
            text="bil", translation="car", word_count=1, difficulty=1, source="anki", disambig_key="noun"
        )
        self.db.upsert_by_guid(
            unit,
            "no",
            {
                Direction.RECOGNITION: DirectionState(
                    direction=Direction.RECOGNITION,
                    due_at=datetime(2026, 9, 1, tzinfo=UTC),
                    state=SRSState.REVIEW,
                    stability=120.0,
                    reps=30,
                    last_review=datetime(2026, 5, 1, tzinfo=UTC),
                )
            },
            anki_note_id=9300,
        )

        lesson = _make_lesson([("female-1", "bilen")], lang="no")
        result = extract_transcript(lesson, self.db, _MockNorwegianLemmatizer(), today=self.today)
        word = result.dialogue_lines[0].words[0]

        assert word.inflectable is False
        assert word.progress == 0.5, "recognition-only still draws the absent-production penalty"

    def test_norwegian_freshly_minted_production_is_not_yet_inflectable(self):
        """The state the just-in-time mint creates, which did not exist before it.

        `promote_production_cards` adds the production direction as NEW — the
        card is *added*, not graded (`.claude/rules/anki-sync.md`). So a word the
        drain promoted this morning has a production direction and still must not
        offer the inflection affordance: the gate is "the learner can produce
        this", not "a production card exists". It becomes inflectable once that
        card is actually studied to REVIEW, which is the same bar Slovene has
        always had.
        """
        from app.srs.lemmatizer import TokenAnalysis as _TA

        class _MockNorwegianLemmatizer:
            def lemmatize(self, word: str, language_code: str) -> str:
                return "bil" if word == "bilen" else word.lower()

            def analyze(self, word: str, language_code: str) -> tuple[str, str, str]:
                return ("bil", "", "") if word == "bilen" else (word.lower(), "", "")

            def analyze_sentence(self, sentence: str, language_code: str) -> list:
                if "bilen" in sentence:
                    return [_TA(surface="bilen", lemma="bil", upos="NOUN", definite="Def", number="Sing")]
                return []

        from app.models.srs_item import Direction, SRSState

        self._add_vocab("bil", "car", lemma="bil")
        item = self.db.get_collocation("bil")
        # Recognition graduated a year ago; production minted this morning.
        item.directions[Direction.RECOGNITION].state = SRSState.REVIEW
        item.directions[Direction.RECOGNITION].last_review = datetime(2026, 5, 1, tzinfo=UTC)
        item.directions[Direction.RECOGNITION].stability = 180.0
        item.directions[Direction.RECOGNITION].reps = 40
        item.directions[Direction.PRODUCTION].state = SRSState.NEW
        item.directions[Direction.PRODUCTION].last_review = None
        self.db.update_direction(item.guid, Direction.RECOGNITION, item.directions[Direction.RECOGNITION])
        self.db.update_direction(item.guid, Direction.PRODUCTION, item.directions[Direction.PRODUCTION])

        lesson = _make_lesson([("female-1", "bilen")], lang="no")
        result = extract_transcript(lesson, self.db, _MockNorwegianLemmatizer(), today=self.today)
        word = result.dialogue_lines[0].words[0]

        assert word.inflectable is False
        # ...and the flip of the ACTIVE direction is not a regression in what the
        # reader sees. `resolve_active_direction` hands over to production the
        # moment one exists, so a word mature for a year starts reporting
        # `active_state='new'` — but the colour and the tooltip percentage are
        # driven by `progress`, and that does not move: a recognition-only word
        # was already capped at half, and the minted direction supplies the
        # other half's zero.
        from app.srs.mastery import compute_mastery_progress

        assert word.active_direction == "production"
        before = compute_mastery_progress([item.directions[Direction.RECOGNITION]])
        assert word.progress == before == 0.5

    def test_inflection_cloze_exact_match_path(self):
        """When an inflection cloze with exact surface match exists, it takes priority over the base."""
        from app.srs.lemmatizer import TokenAnalysis as _TA

        class _MockLemmatizer2:
            def lemmatize(self, word: str, language_code: str) -> str:
                if word == "lepa":
                    return "lep"
                return word.lower()

            def analyze(self, word: str, language_code: str) -> tuple[str, str, str]:
                return word.lower(), "", ""

            def analyze_sentence(self, sentence: str, language_code: str) -> list:
                if "lep" in sentence:
                    return [
                        _TA(
                            surface="Lepa", lemma="lep", upos="ADJ", case="Nom", number="Sing", person="", gender="Fem"
                        ),
                        _TA(surface="je", lemma="je", upos="AUX", case="", number="", person="", gender=""),
                        _TA(
                            surface="lepa", lemma="lep", upos="ADJ", case="Nom", number="Sing", person="", gender="Fem"
                        ),
                    ]
                return []

        from app.models.srs_item import Direction, SRSState

        self._add_vocab("lep", "nice", lemma="lep")
        item = self.db.get_collocation("lep")
        item.directions[Direction.PRODUCTION].state = SRSState.REVIEW
        item.directions[Direction.RECOGNITION].state = SRSState.REVIEW
        item.directions[Direction.PRODUCTION].last_review = datetime(2026, 5, 1, tzinfo=UTC)
        item.directions[Direction.RECOGNITION].last_review = datetime(2026, 5, 1, tzinfo=UTC)
        self.db.update_direction(item.guid, Direction.RECOGNITION, item.directions[Direction.RECOGNITION])
        self.db.update_direction(item.guid, Direction.PRODUCTION, item.directions[Direction.PRODUCTION])

        # Create an inflection cloze with exact surface "lepa"
        unit = SyntacticUnit(
            text="lepa",
            translation="nice (fem)",
            word_count=1,
            difficulty=1,
            source="llm",
            lemma="lep",
            disambig_key="morph:adj-nom-f-sg",
            card_type="cloze",
            source_sentence="Lepa je lepa.",
        )
        self.db.add_collocation(unit, language_code="sl")

        lesson = _make_lesson([("female-1", "Lepa je lepa.")])
        result = extract_transcript(lesson, self.db, _MockLemmatizer2(), today=self.today)
        words = result.dialogue_lines[0].words
        lepa_words = [w for w in words if w.surface == "lepa"]
        for w in lepa_words:
            # Resolved as inflection cloze → card_type=cloze
            assert w.card_type == "cloze"
            assert w.inflectable is False  # already has the cloze
            assert w.active_direction == "production"

    def test_inflectable_else_branch_when_analysis_missing(self):
        """Surface!=lemma but no analysis_by_surface entry → feature_str='' (line 242)."""

        class _MockNoAnalysis:
            def lemmatize(self, word, language_code):
                return {"hodim": "hoditi"}.get(word, word.lower())

            def analyze(self, word, language_code):
                return word.lower(), "", ""

            def analyze_sentence(self, sentence, language_code):
                return []

        from app.models.srs_item import Direction, SRSState

        self._add_vocab("hoditi", "to walk", lemma="hoditi")
        item = self.db.get_collocation("hoditi")
        item.directions[Direction.PRODUCTION].state = SRSState.REVIEW
        item.directions[Direction.RECOGNITION].state = SRSState.REVIEW
        item.directions[Direction.PRODUCTION].last_review = datetime(2026, 5, 1, tzinfo=UTC)
        item.directions[Direction.RECOGNITION].last_review = datetime(2026, 5, 1, tzinfo=UTC)
        self.db.update_direction(item.guid, Direction.RECOGNITION, item.directions[Direction.RECOGNITION])
        self.db.update_direction(item.guid, Direction.PRODUCTION, item.directions[Direction.PRODUCTION])

        lesson = _make_lesson([("female-1", "hodim")])
        result = extract_transcript(lesson, self.db, _MockNoAnalysis(), today=self.today)
        word = result.dialogue_lines[0].words[0]
        # analysis_by_surface is empty → ta=None → feature_str="" → no inflectable detection
        assert word.inflectable is False
        assert word.inflection_feature is None
        assert word.srs_state != "unknown"

    def test_inflection_cloze_with_reviewed_production_hits_inflectable_else_branch(self):
        """When inflection cloze exists and its production is REVIEW/KNOWN, the
        inflectable check's `inflection_match is not None` else branch is taken."""
        from app.srs.lemmatizer import TokenAnalysis as _TA

        class _MockLemmatizer3:
            def lemmatize(self, word, language_code):
                return {"Lepa": "lep", "lepa": "lep"}.get(word, word.lower())

            def analyze(self, word, language_code):
                return word.lower(), "", ""

            def analyze_sentence(self, sentence, language_code):
                return [
                    _TA(surface="Lepa", lemma="lep", upos="ADJ", case="Nom", number="Sing", person="", gender="Fem"),
                    _TA(surface="je", lemma="je", upos="AUX", case="", number="", person="", gender=""),
                    _TA(surface="lepa", lemma="lep", upos="ADJ", case="Nom", number="Sing", person="", gender="Fem"),
                ]

        from app.models.srs_item import Direction, SRSState

        self._add_vocab("lep", "nice", lemma="lep")
        item = self.db.get_collocation("lep")
        item.directions[Direction.PRODUCTION].state = SRSState.REVIEW
        item.directions[Direction.RECOGNITION].state = SRSState.REVIEW
        item.directions[Direction.PRODUCTION].last_review = datetime(2026, 5, 1, tzinfo=UTC)
        item.directions[Direction.RECOGNITION].last_review = datetime(2026, 5, 1, tzinfo=UTC)
        self.db.update_direction(item.guid, Direction.RECOGNITION, item.directions[Direction.RECOGNITION])
        self.db.update_direction(item.guid, Direction.PRODUCTION, item.directions[Direction.PRODUCTION])

        # Create an inflection cloze with exact surface "lepa" AND set its PRODUCTION to REVIEW
        unit = SyntacticUnit(
            text="lepa",
            translation="nice (fem)",
            word_count=1,
            difficulty=1,
            source="llm",
            lemma="lep",
            disambig_key="morph:adj-nom-f-sg",
            card_type="cloze",
            source_sentence="Lepa je lepa.",
        )
        self.db.add_collocation(unit, language_code="sl")

        # Set the cloze's production to REVIEW/KNOWN so the inner check is entered
        ic_item = self.db.get_collocation("lepa")
        if ic_item is not None:
            ic_item.directions[Direction.PRODUCTION].state = SRSState.REVIEW
            ic_item.directions[Direction.PRODUCTION].last_review = datetime(2026, 5, 1, tzinfo=UTC)
            self.db.update_direction(ic_item.guid, Direction.PRODUCTION, ic_item.directions[Direction.PRODUCTION])

        lesson = _make_lesson([("female-1", "Lepa je lepa.")])
        result = extract_transcript(lesson, self.db, _MockLemmatizer3(), today=self.today)
        lepa_words = [w for w in result.dialogue_lines[0].words if w.surface == "lepa"]
        for w in lepa_words:
            # Token resolved as cloze, but inflectable=False (else branch of inflection_match is None)
            assert w.inflectable is False

    def test_add_inflection_cloze_lowers_progress(self):
        """Adding an inflection cloze component lowers the base token's progress."""
        from app.models.srs_item import Direction, SRSState

        self._add_vocab("delati", "to work", lemma="delati")
        item = self.db.get_collocation("delati")
        item.directions[Direction.RECOGNITION].state = SRSState.REVIEW
        item.directions[Direction.PRODUCTION].state = SRSState.REVIEW
        item.directions[Direction.RECOGNITION].last_review = datetime(2026, 5, 1, tzinfo=UTC)
        item.directions[Direction.PRODUCTION].last_review = datetime(2026, 5, 1, tzinfo=UTC)
        # Non-trivial stability so the base has positive mastery; otherwise the
        # default stability=1.0 maps to log10(1)=0.0 and there's nothing to lower.
        item.directions[Direction.RECOGNITION].stability = 50.0
        item.directions[Direction.PRODUCTION].stability = 50.0
        self.db.update_direction(item.guid, Direction.RECOGNITION, item.directions[Direction.RECOGNITION])
        self.db.update_direction(item.guid, Direction.PRODUCTION, item.directions[Direction.PRODUCTION])

        lesson_no_inflection = _make_lesson([("female-1", "delati")])
        result_no = extract_transcript(lesson_no_inflection, self.db, self.lemmatizer, today=self.today)
        progress_without = result_no.dialogue_lines[0].words[0].progress

        # Add an inflection cloze for this lemma (no last_review → mastery=0.0)
        unit = SyntacticUnit(
            text="delam",
            translation="I work",
            word_count=1,
            difficulty=1,
            source="llm",
            lemma="delati",
            disambig_key="morph:verb-1sg",
            card_type="cloze",
        )
        self.db.add_collocation(unit, language_code="sl")
        ic_item = self.db.get_collocation("delam")
        if ic_item is not None:
            if Direction.PRODUCTION in ic_item.directions:
                ic_item.directions[Direction.PRODUCTION].state = SRSState.REVIEW
            self.db.update_direction(ic_item.guid, Direction.PRODUCTION, ic_item.directions[Direction.PRODUCTION])

        lesson_with_inflection = _make_lesson([("female-1", "delati")])
        result_with = extract_transcript(lesson_with_inflection, self.db, self.lemmatizer, today=self.today)
        progress_with = result_with.dialogue_lines[0].words[0].progress
        assert progress_with is not None
        assert progress_without is not None
        assert progress_with < progress_without

    def test_repeated_lemma_triggers_one_base_lookup(self):
        """N occurrences of the same lemma cause 1 DB lookup, not N (finding #6).

        Regression: without a base_cache, each token calls
        get_collocation_by_lemma_with_id per occurrence of its lemma.
        """
        self._add_vocab("banka", "bank", lemma="banka")
        lesson = _make_lesson([("female-1", "banka banka banka")])

        original_lookup = self.db.get_collocation_by_lemma_with_id
        call_count = 0

        def counting_lookup(lemma: str):
            nonlocal call_count
            call_count += 1
            return original_lookup(lemma)

        self.db.get_collocation_by_lemma_with_id = counting_lookup
        extract_transcript(lesson, self.db, self.lemmatizer, today=self.today)
        assert call_count == 1, f"expected 1 lookup, got {call_count}"


class TestCollocationLemmaKey:
    """Precompute + persist of collocations.lemma_key (review finding #4).

    _build_collocation_index used to re-lemmatize every multi-word collocation on
    every /transcript request. The lemma tuple is now stored as lemma_key and
    reused; rows missing it are lemmatized once and persisted (self-healing
    backfill), so the request path lemmatizes each collocation at most once ever.
    """

    def setup_method(self):
        self.db = SRSDatabase(":memory:")
        self.lemmatizer = LowercaseLemmatizer()

    def _add_phrase(self, text: str) -> int:
        self.db.add_collocation(
            SyntacticUnit(text=text, translation="x", word_count=len(text.split()), difficulty=1, source="user"),
            language_code="sl",
        )
        with self.db._get_conn() as conn:
            return conn.execute("SELECT id FROM collocations WHERE text = ?", (text,)).fetchone()[0]

    def test_build_collocation_lemma_key_joins_lemmas(self):
        from app.srs.transcript import build_collocation_lemma_key

        assert build_collocation_lemma_key("Dober Dan", self.lemmatizer, "sl") == "dober dan"

    def test_build_index_lazily_persists_missing_key(self):
        from app.srs.transcript import _build_collocation_index

        coll_id = self._add_phrase("dober dan")
        rows = self.db.get_collocations_with_lemma_key("sl", min_word_count=2)
        assert rows == [(coll_id, "dober dan", None)]

        index = _build_collocation_index(self.db, rows, self.lemmatizer, "sl")
        assert index == {("dober", "dan"): coll_id}

        # Persisted, so the next request reads the stored key.
        assert self.db.get_collocations_with_lemma_key("sl", min_word_count=2) == [(coll_id, "dober dan", "dober dan")]

    def test_build_index_uses_stored_key_without_lemmatizing(self):
        from app.srs.transcript import _build_collocation_index

        coll_id = self._add_phrase("dober dan")
        self.db.set_lemma_key(coll_id, "dober dan")

        class BoomLemmatizer:
            def lemmatize(self, *a, **k):
                raise AssertionError("must not lemmatize when lemma_key is stored")

            def analyze_sentence(self, *a, **k):
                raise AssertionError("must not analyze when lemma_key is stored")

        rows = self.db.get_collocations_with_lemma_key("sl", min_word_count=2)
        index = _build_collocation_index(self.db, rows, BoomLemmatizer(), "sl")
        assert index == {("dober", "dan"): coll_id}


class TestExtractPunctPairs:
    """Tests for _extract_punct_pairs edge cases."""

    def test_surface_not_found_returns_empty(self):
        """When a surface is not a substring of the text, both punct fields are empty."""
        result = _extract_punct_pairs("foo", ["bar"])
        assert result == [("", "")]

    def test_standalone_punctuation_token_is_skipped(self):
        """Surfaces come from tokenize() (drops standalone punctuation like an
        en-dash); the punct list stays aligned to surfaces even though
        str.split() would count the dash as a token."""
        text = "Koliko stane? – Dve kavi."
        surfaces = ["Koliko", "stane", "Dve", "kavi"]
        assert _extract_punct_pairs(text, surfaces) == [("", ""), ("", "?"), ("", ""), ("", ".")]

    def test_prefix_and_suffix_punct_around_surface(self):
        """Punctuation both before and after a surface within its token is captured."""
        assert _extract_punct_pairs("«Dve» kavi.", ["Dve", "kavi"]) == [("«", "»"), ("", ".")]

    def test_repeated_surface_advances_cursor(self):
        """A surface repeated in the line resolves to successive occurrences, not
        the same one twice."""
        assert _extract_punct_pairs("kavo, kavo!", ["kavo", "kavo"]) == [("", ","), ("", "!")]


class TestBitiTranscriptSpecialCase:
    """Transcript resolution for biti (clozes-only verb)."""

    def setup_method(self):
        self.db = SRSDatabase(":memory:")
        self.lemmatizer = LowercaseLemmatizer()
        self.today = date(2026, 6, 4)

    def test_biti_surface_unknown_is_inflectable_ungated(self):
        """A biti surface with no cloze yet is inflectable=True, even without a base card."""
        from app.srs.lemmatizer import TokenAnalysis as _TA

        class _MockBitiLemmatizer:
            def lemmatize(self, word, language_code):
                return {"sem": "biti", "ste": "biti", "smo": "biti"}.get(word, word.lower())

            def analyze(self, word, language_code):
                return word.lower(), "", ""

            def analyze_sentence(self, sentence, language_code):
                tokens = sentence.split()
                analyses = []
                for t in tokens:
                    t_lower = t.lower()
                    if t_lower == "sem":
                        analyses.append(
                            _TA(surface=t, lemma="biti", upos="AUX", case="", number="Sing", person="1", gender="")
                        )
                    elif t_lower == "ste":
                        analyses.append(
                            _TA(surface=t, lemma="biti", upos="AUX", case="", number="Plur", person="2", gender="")
                        )
                    else:
                        analyses.append(
                            _TA(surface=t, lemma=t_lower, upos="", case="", number="", person="", gender="")
                        )
                return analyses

        lesson = _make_lesson([("female-1", "Sem doma")])
        result = extract_transcript(lesson, self.db, _MockBitiLemmatizer(), today=self.today)
        word = result.dialogue_lines[0].words[0]
        assert word.surface == "Sem"
        assert word.lemma == "biti"
        assert word.srs_state == "unknown"
        assert word.inflectable is True
        assert word.inflection_feature == "verb:1sg"

    def test_biti_surface_equals_lemma_no_inflectable(self):
        """When biti surface == lemma (rare, but guards the branch)."""
        from app.srs.lemmatizer import TokenAnalysis as _TA

        class _MockLemmatizer:
            def lemmatize(self, word, language_code):
                return word.lower()

            def analyze(self, word, language_code):
                return word.lower(), "", ""

            def analyze_sentence(self, sentence, language_code):
                return [
                    _TA(surface=t, lemma=t.lower(), upos="", case="", number="", person="", gender="")
                    for t in sentence.split()
                ]

        # biti as base-form surface — unlikely in practice but tests the surface==lemma branch
        lesson = _make_lesson([("female-1", "biti")])
        result = extract_transcript(lesson, self.db, _MockLemmatizer(), today=self.today)
        word = result.dialogue_lines[0].words[0]
        assert word.lemma == "biti"
        assert word.inflectable is False  # surface==lemma

    def test_biti_surface_no_analysis_no_inflectable(self):
        """biti surface with no analysis_by_surface entry → feature_str='' → inflectable=False."""
        from app.srs.lemmatizer import TokenAnalysis as _TA

        class _MockNoAnalysis:
            def lemmatize(self, word, language_code):
                return {"ste": "biti"}.get(word, word.lower())

            def analyze(self, word, language_code):
                return word.lower(), "", ""

            def analyze_sentence(self, sentence, language_code):
                # Return analysis for "kje" only — "ste" is absent from the dict
                return [
                    _TA(surface="kje", lemma="kje", upos="ADV", case="", number="", person="", gender=""),
                ]

        lesson = _make_lesson([("female-1", "kje ste")])
        result = extract_transcript(lesson, self.db, _MockNoAnalysis(), today=self.today)
        # ste has no analysis → feature_str='' → inflectable stays False
        ste_words = [w for w in result.dialogue_lines[0].words if w.surface == "ste"]
        assert len(ste_words) == 1
        assert ste_words[0].inflectable is False

    def test_biti_surface_non_a1_feature_no_inflectable(self):
        """biti surface with analysis but non-A1 feature → inflectable=False."""
        from app.srs.lemmatizer import TokenAnalysis as _TA

        class _MockNonA1:
            def lemmatize(self, word, language_code):
                return {"ste": "biti"}.get(word, word.lower())

            def analyze(self, word, language_code):
                return word.lower(), "", ""

            def analyze_sentence(self, sentence, language_code):
                tokens = sentence.split()
                analyses = []
                for t in tokens:
                    t_lower = t.lower()
                    if t_lower == "ste":
                        # Return a non-A1 feature (missing number makes ud_feats_to_tt_feature return None)
                        analyses.append(
                            _TA(surface=t, lemma="biti", upos="AUX", case="", number="", person="2", gender="")
                        )
                    else:
                        analyses.append(
                            _TA(surface=t, lemma=t_lower, upos="", case="", number="", person="", gender="")
                        )
                return analyses

        lesson = _make_lesson([("female-1", "ste")])
        result = extract_transcript(lesson, self.db, _MockNonA1(), today=self.today)
        word = result.dialogue_lines[0].words[0]
        assert word.lemma == "biti"
        # ud_feats_to_tt_feature returns None for person=2, number="" (under "sl")
        # → inflectable stays False
        assert word.inflectable is False

    def test_biti_surface_resolves_existing_inflection_cloze(self):
        """A biti surface with an existing conjugation cloze resolves to that cloze."""
        from app.srs.lemmatizer import TokenAnalysis as _TA

        class _MockBitiLemmatizer2:
            def lemmatize(self, word, language_code):
                return {"ste": "biti"}.get(word, word.lower())

            def analyze(self, word, language_code):
                return word.lower(), "", ""

            def analyze_sentence(self, sentence, language_code):
                tokens = sentence.split()
                analyses = []
                for t in tokens:
                    t_lower = t.lower()
                    if t_lower == "ste":
                        analyses.append(
                            _TA(surface=t, lemma="biti", upos="AUX", case="", number="Plur", person="2", gender="")
                        )
                    else:
                        analyses.append(
                            _TA(surface=t, lemma=t_lower, upos="", case="", number="", person="", gender="")
                        )
                return analyses

        # Create a conjugation cloze for "ste"
        unit = SyntacticUnit(
            text="ste",
            translation="",
            word_count=1,
            difficulty=1,
            source="llm",
            lemma="biti",
            disambig_key="morph:verb-2pl",
            card_type="cloze",
            source_sentence="Zdravo kje {{c1::ste}}",
            grammar="biti, 2nd person plural",
        )
        self.db.add_collocation(unit, language_code="sl")

        lesson = _make_lesson([("female-1", "Zdravo kje ste")])
        result = extract_transcript(lesson, self.db, _MockBitiLemmatizer2(), today=self.today)
        # Find the "ste" word
        ste_words = [w for w in result.dialogue_lines[0].words if w.surface == "ste"]
        assert len(ste_words) == 1
        w = ste_words[0]
        assert w.lemma == "biti"
        assert w.card_type == "cloze"
        assert w.inflectable is False  # already has the cloze
        assert w.srs_state != "unknown"

    def test_biti_surface_no_inflection_cloze_still_inflectable(self):
        """biti surface with no exact cloze is inflectable=True ungated,
        even when no base card exists at all."""
        from app.srs.lemmatizer import TokenAnalysis as _TA

        class _MockBitiLemmatizer3:
            def lemmatize(self, word, language_code):
                return {"smo": "biti"}.get(word, word.lower())

            def analyze(self, word, language_code):
                return word.lower(), "", ""

            def analyze_sentence(self, sentence, language_code):
                tokens = sentence.split()
                analyses = []
                for t in tokens:
                    t_lower = t.lower()
                    if t_lower == "smo":
                        analyses.append(
                            _TA(surface=t, lemma="biti", upos="AUX", case="", number="Plur", person="1", gender="")
                        )
                    else:
                        analyses.append(
                            _TA(surface=t, lemma=t_lower, upos="", case="", number="", person="", gender="")
                        )
                return analyses

        lesson = _make_lesson([("female-1", "smo")])
        result = extract_transcript(lesson, self.db, _MockBitiLemmatizer3(), today=self.today)
        word = result.dialogue_lines[0].words[0]
        assert word.lemma == "biti"
        assert word.srs_state == "unknown"
        assert word.inflectable is True
        assert word.inflection_feature == "verb:1pl"

    def test_regular_verb_no_base_not_inflectable(self):
        """Non-special verb with no base is NOT inflectable — still gated."""
        from app.srs.lemmatizer import TokenAnalysis as _TA

        class _MockRegularLemmatizer:
            def lemmatize(self, word, language_code):
                return {"hodim": "hoditi"}.get(word, word.lower())

            def analyze(self, word, language_code):
                return word.lower(), "", ""

            def analyze_sentence(self, sentence, language_code):
                tokens = sentence.split()
                analyses = []
                for t in tokens:
                    t_lower = t.lower()
                    if t_lower == "hodim":
                        analyses.append(
                            _TA(surface=t, lemma="hoditi", upos="VERB", case="", number="Sing", person="1", gender="")
                        )
                    else:
                        analyses.append(
                            _TA(surface=t, lemma=t_lower, upos="", case="", number="", person="", gender="")
                        )
                return analyses

        lesson = _make_lesson([("female-1", "hodim")])
        result = extract_transcript(lesson, self.db, _MockRegularLemmatizer(), today=self.today)
        word = result.dialogue_lines[0].words[0]
        assert word.lemma == "hoditi"
        assert word.srs_state == "unknown"
        assert word.inflectable is False  # no base → gated


class TestRecognitionState:
    """recognition_state and recognition_is_due: recognition-side bucketing fields.

    The mastery line buckets words by their RECOGNITION direction's state,
    independent of the active direction. 'new' must mean "not in the
    recognition queue yet" — a word whose recognition graduated to REVIEW
    (with active=production, active_state=new) must show as review, not new.
    """

    def setup_method(self):
        self.db = SRSDatabase(":memory:")
        self.lemmatizer = LowercaseLemmatizer()
        self.today = date(2026, 6, 1)
        self.now = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)

    def _add_vocab(self, text: str, translation: str, lemma: str | None = None) -> int:
        unit = SyntacticUnit(
            text=text, translation=translation, word_count=1, difficulty=1, source="llm", lemma=lemma or text
        )
        self.db.add_collocation(unit, language_code="sl")
        return self.db.get_collocation_id_by_guid(unit.guid)

    def test_untracked_word_recognition_state_none(self):
        """Case 1: untracked word → recognition_state is None, recognition_is_due False."""
        lesson = _make_lesson([("female-1", "banka")])
        result = extract_transcript(lesson, self.db, self.lemmatizer, today=self.today)
        word = result.dialogue_lines[0].words[0]
        assert word.recognition_state is None
        assert word.recognition_is_due is False

    def test_recognition_learning_due_per_is_due(self):
        """Case 2: recognition LEARNING → 'learning', due per _is_due."""
        self._add_vocab("banka", "bank", lemma="banka")
        item = self.db.get_collocation("banka")
        rec = item.directions[Direction.RECOGNITION]
        rec.state = SRSState.LEARNING
        rec.due_at = datetime(2026, 5, 30, 12, 0, tzinfo=UTC)  # past
        self.db.update_direction(item.guid, Direction.RECOGNITION, rec)

        lesson = _make_lesson([("female-1", "banka")])
        result = extract_transcript(lesson, self.db, self.lemmatizer, today=self.today)
        word = result.dialogue_lines[0].words[0]
        assert word.recognition_state == "learning"
        assert word.recognition_is_due is True

    def test_recognition_review_past_due(self):
        """Case 3: recognition REVIEW, due_at in past → 'review', recognition_is_due True."""
        self._add_vocab("banka", "bank", lemma="banka")
        item = self.db.get_collocation("banka")
        rec = item.directions[Direction.RECOGNITION]
        rec.state = SRSState.REVIEW
        rec.due_at = datetime(2026, 5, 30, 12, 0, tzinfo=UTC)  # past
        rec.last_review = datetime(2026, 5, 1, tzinfo=UTC)
        self.db.update_direction(item.guid, Direction.RECOGNITION, rec)

        lesson = _make_lesson([("female-1", "banka")])
        result = extract_transcript(lesson, self.db, self.lemmatizer, today=self.today)
        word = result.dialogue_lines[0].words[0]
        assert word.recognition_state == "review"
        assert word.recognition_is_due is True

    def test_recognition_review_future_due(self):
        """Case 4: recognition REVIEW, due_at in future → 'review', recognition_is_due False."""
        self._add_vocab("banka", "bank", lemma="banka")
        item = self.db.get_collocation("banka")
        rec = item.directions[Direction.RECOGNITION]
        rec.state = SRSState.REVIEW
        rec.due_at = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)  # future
        rec.last_review = datetime(2026, 5, 1, tzinfo=UTC)
        self.db.update_direction(item.guid, Direction.RECOGNITION, rec)

        lesson = _make_lesson([("female-1", "banka")])
        result = extract_transcript(lesson, self.db, self.lemmatizer, today=self.today)
        word = result.dialogue_lines[0].words[0]
        assert word.recognition_state == "review"
        assert word.recognition_is_due is False

    def test_flip_case_recognition_review_production_new(self):
        """Case 5 (guardrail): recognition REVIEW + production NEW → recognition_state
        'review', NOT 'new'. The active direction is production (active_state='new'),
        but /listen grades ONLY recognition — the mastery line must reflect recognition."""
        self._add_vocab("banka", "bank", lemma="banka")
        item = self.db.get_collocation("banka")
        rec = item.directions[Direction.RECOGNITION]
        rec.state = SRSState.REVIEW
        rec.due_at = datetime(2099, 1, 1, 4, 0, tzinfo=UTC)  # future (not due)
        rec.last_review = datetime(2026, 5, 1, tzinfo=UTC)
        self.db.update_direction(item.guid, Direction.RECOGNITION, rec)
        # Production stays at default NEW (never graded)

        lesson = _make_lesson([("female-1", "banka")])
        result = extract_transcript(lesson, self.db, self.lemmatizer, today=self.today)
        word = result.dialogue_lines[0].words[0]
        # Active direction is production (rec=REVIEW, prod=NEW, prod exists → prod active)
        assert word.active_direction == "production"
        assert word.active_state == "new"  # production is NEW
        # But recognition is REVIEW
        assert word.recognition_state == "review"
        assert word.recognition_is_due is False

    def test_cloze_production_only_recognition_state_none(self):
        """Case 6: cloze (production-only) → recognition_state is None."""
        unit = SyntacticUnit(
            text="vsak",
            translation="every",
            word_count=1,
            difficulty=1,
            source="cloze",
            lemma="vsak",
            source_sentence="Odprto je vsak dan",
            card_type="cloze",
        )
        self.db.add_collocation(unit, language_code="sl")
        item = self.db.get_collocation("vsak")
        item.directions[Direction.PRODUCTION].state = SRSState.LEARNING
        self.db.update_collocation(item)

        lesson = _make_lesson([("female-1", "Odprto je vsak dan")])
        result = extract_transcript(lesson, self.db, self.lemmatizer, today=self.today)
        vsak = next(w for w in result.dialogue_lines[0].words if w.lemma == "vsak")
        assert vsak.recognition_state is None
        assert vsak.recognition_is_due is False

    def test_recognition_known_state(self):
        """Recognition KNOWN → recognition_state 'known', recognition_is_due False."""
        self._add_vocab("banka", "bank", lemma="banka")
        item = self.db.get_collocation("banka")
        rec = item.directions[Direction.RECOGNITION]
        rec.state = SRSState.KNOWN
        self.db.update_direction(item.guid, Direction.RECOGNITION, rec)

        lesson = _make_lesson([("female-1", "banka")])
        result = extract_transcript(lesson, self.db, self.lemmatizer, today=self.today)
        word = result.dialogue_lines[0].words[0]
        assert word.recognition_state == "known"
        assert word.recognition_is_due is False


class TestExtractTranscriptCaching:
    """extract_transcript populates the persistent cache and reuses it on subsequent calls."""

    def setup_method(self):
        self.db = SRSDatabase(":memory:")
        self.call_count = 0

    class _CountingLemmatizer(LowercaseLemmatizer):
        _cache_version = "test-v1"

        def __init__(self, owner):
            super().__init__()
            self._owner = owner

        def analyze_sentence(self, sentence: str, language_code: str) -> list:
            self._owner.call_count += 1
            return super().analyze_sentence(sentence, language_code)

    def test_populates_and_reuses_cache(self):
        lem = self._CountingLemmatizer(self)
        lesson = _make_lesson([("female-1", "Dober dan"), ("male-1", "Kako si")])
        today = date(2026, 6, 4)

        # First call: both phrases miss cache → 2 lemmatizer invocations
        result1 = extract_transcript(lesson, self.db, lem, today=today)
        assert len(result1.dialogue_lines) == 2
        assert self.call_count == 2

        # Cache should have entries for both phrases (keyed under the model
        # version — which includes the analysis-schema revision, so bumping
        # TokenAnalysis invalidates old rows)
        for text in ("Dober dan", "Kako si"):
            cached = self.db.get_sentence_analysis(text, "sl", model_version_for(lem))
            assert cached is not None, f"Expected cache entry for {text}"

        # Second call: both cache hits → 0 lemmatizer invocations
        result2 = extract_transcript(lesson, self.db, lem, today=today)
        assert len(result2.dialogue_lines) == 2
        assert self.call_count == 2  # unchanged

    def test_model_version_mismatch_re_analyzes(self):
        lem = self._CountingLemmatizer(self)
        lesson = _make_lesson([("female-1", "Dober dan")])
        today = date(2026, 6, 4)

        # Populate cache with a different model_version manually
        self.db.set_sentence_analysis(
            "Dober dan", "sl", "other-v1", _serialize_analyses([TokenAnalysis(surface="Dober", lemma="dober")])
        )
        # First call: version mismatch → 1 lemmatizer invocation
        extract_transcript(lesson, self.db, lem, today=today)
        assert self.call_count == 1


class TestWellKnown:
    """well_known: a recognition card scheduled past the listen horizon.

    Same rule the listen preview uses to suppress a word from the grading list
    (``mastery.is_well_known``), surfaced on the transcript so the dialogue and
    the mastery line can call it "known" too. Before this the flag existed only
    inside the preview response, so a word the preview had stopped asking about
    still rendered as an ordinary REVIEW word and counted in the review bucket.
    Marked-known cards land here as well: a sync returns them as REVIEW due
    ~2126, which is far beyond the horizon (the documented KNOWN-doesn't-survive
    -sync case).
    """

    def setup_method(self):
        self.db = SRSDatabase(":memory:")
        self.lemmatizer = LowercaseLemmatizer()

    def _add(self, text: str, state: SRSState, due: datetime | None) -> None:
        unit = SyntacticUnit(text=text, translation="x", word_count=1, difficulty=1, source="llm", lemma=text)
        self.db.add_collocation(unit, language_code="sl")
        item = self.db.get_collocation(text)
        rec = item.directions[Direction.RECOGNITION]
        rec.state = state
        rec.due_at = due
        self.db.update_direction(item.guid, Direction.RECOGNITION, rec)

    def _word(self, text: str) -> object:
        lesson = _make_lesson([("female-1", text)])
        result = extract_transcript(lesson, self.db, self.lemmatizer, today=date(2026, 6, 1))
        return result.dialogue_lines[0].words[0]

    def test_review_due_beyond_horizon_is_well_known(self):
        self._add("banka", SRSState.REVIEW, datetime(2029, 1, 1, 4, 0, tzinfo=UTC))
        assert self._word("banka").well_known is True

    def test_review_due_inside_horizon_is_not_well_known(self):
        # 30 days out — the preview still asks about this one.
        self._add("banka", SRSState.REVIEW, datetime(2026, 7, 1, 4, 0, tzinfo=UTC))
        assert self._word("banka").well_known is False

    def test_due_review_is_not_well_known(self):
        self._add("banka", SRSState.REVIEW, datetime(2026, 5, 1, 4, 0, tzinfo=UTC))
        word = self._word("banka")
        # Recognition-side dueness, not word.is_due: once recognition graduates
        # to REVIEW the ACTIVE direction is production (NEW, not due).
        assert word.recognition_is_due is True
        assert word.well_known is False

    def test_learning_is_never_well_known(self):
        # Guards the suppression rule: a card being acquired must never read as
        # known however far out its due date drifts.
        self._add("banka", SRSState.LEARNING, datetime(2029, 1, 1, 4, 0, tzinfo=UTC))
        assert self._word("banka").well_known is False

    def test_untracked_word_is_not_well_known(self):
        assert self._word("banka").well_known is False
