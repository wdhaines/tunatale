"""When a spelling has two or more cards, the lesson's gloss picks among them.

bd tunatale-ceuc, slice 3. ``resolve_lemma_card`` told homographs apart by word
class only (tunatale-u8nz.22) and otherwise took the oldest card. That leaves
every SAME-class pair unresolved, and the decks already hold them: Slovene has
14 (``ura`` hour / clock, ``barva`` color / paint), Tagalog 5 (``linggo`` week /
Sunday), all keyed by a sense label rather than a word class — so each resolved
to whichever card was made first.

The rule, decided with the user:
- Among the cards the word class leaves standing, the lesson's own gloss picks
  the one whose translation it shares a content word with.
- A gloss that singles out none of them is "cannot tell": the reader keeps the
  old answer (it must still show the word as tracked), and a listen grades NONE
  of them rather than the wrong one.
- One card is never judged against the gloss. String overlap can choose among
  candidates; it cannot say whether a lone card is right (a probe over 11 live
  Norwegian lessons flagged 149 headwords that way, about 20 of them real).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.lesson import Lesson, Phrase, Section, SectionType
from app.models.srs_item import Direction, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.anki_mirror.rollover import anki_today, due_at_rollover_utc
from app.srs.database import SRSDatabase
from app.srs.lemmatizer import LowercaseLemmatizer
from app.srs.sense_match import sense_overlap
from app.srs.transcript import choose_lemma_card, extract_transcript, resolve_lemma_card
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401

LANG = "no"


def _add(db: SRSDatabase, text: str, translation: str, key: str, card_type: str = "vocab") -> int:
    db.add_collocation(
        SyntacticUnit(
            text=text,
            translation=translation,
            word_count=1,
            difficulty=1,
            source="anki",
            lemma=text,
            disambig_key=key,
            card_type=card_type,
            source_sentence="En {{c1::gang}} til" if card_type == "cloze" else "",
        ),
        language_code=LANG,
    )
    with db._get_conn() as conn:
        return conn.execute("SELECT id FROM collocations WHERE text = ? AND disambig_key = ?", (text, key)).fetchone()[
            0
        ]


class TestSenseOverlap:
    @pytest.mark.parametrize(
        ("gloss", "translation", "shares"),
        [
            ("time", "hall", False),
            ("time", "time, occasion", True),
            ("the time (den gangen = back then)", "time, occasion", True),
            ("hours", "hour", True),
            ("wondered", "wonder", True),
            # A plural in -es whose singular keeps the e: stripping "es" alone
            # made "times" the stem "tim", which is not "time" (2026-10-06;
            # Norwegian `tre ganger` is glossed "three times").
            ("times", "time, occasion", True),
            ("three times", "time", True),
            ("houses", "house", True),
            ("boxes", "box", True),
            ("timed", "time", True),
            ("timer", "time", False),
            ("Sunday", "sunday", True),
            # An article is never evidence of a shared sense.
            ("to the", "the hall", False),
            # A grammar word is — for a function-word card it IS the sense
            # (live: `vår` = spring / our, glossed "our").
            ("our", "our", True),
            ("of it", "it, that (neuter)", True),
            ("our", "spring", False),
            ("", "hall", False),
            ("time", "", False),
        ],
    )
    def test_shares_a_word(self, gloss, translation, shares):
        assert (sense_overlap(gloss, translation) > (0, 0)) is shares

    def test_more_shared_words_score_higher(self):
        assert sense_overlap("old, not young", "old (≠young)") > sense_overlap("old, not young", "old (≠new)")

    def test_a_content_word_outranks_any_grammar_word(self):
        """ "to go" names the card "go", not the card "to"."""
        assert sense_overlap("to go", "go") > sense_overlap("to go", "to")
        assert sense_overlap("to", "to") > sense_overlap("to", "go")


class TestChooseLemmaCard:
    def test_the_gloss_picks_among_sense_keyed_cards(self):
        """The Slovene / Tagalog shape: no key is a word class."""
        db = SRSDatabase(":memory:")
        hall = _add(db, "gang", "hall", "hall")
        time = _add(db, "gang", "time, occasion", "time")

        picked = choose_lemma_card(db, "gang", "NOUN", "the time")
        assert (picked.card[0], picked.undecided) == (time, False)
        picked = choose_lemma_card(db, "gang", "NOUN", "a hall")
        assert (picked.card[0], picked.undecided) == (hall, False)

    def test_a_sense_keyed_card_competes_with_a_class_keyed_one(self):
        """The Norwegian shape after a second sense is minted: the deck's card
        is keyed "noun", the new one by its sense. The class match must not
        hand the word to the "noun" card before the gloss is consulted."""
        db = SRSDatabase(":memory:")
        hall = _add(db, "gang", "hall", "noun")
        time = _add(db, "gang", "time, occasion", "time")

        picked = choose_lemma_card(db, "gang", "NOUN", "time")
        assert (picked.card[0], picked.undecided) == (time, False)
        picked = choose_lemma_card(db, "gang", "NOUN", "hall")
        assert (picked.card[0], picked.undecided) == (hall, False)

    def test_without_a_gloss_the_class_match_still_wins(self):
        """Regression guard: no evidence, no change."""
        db = SRSDatabase(":memory:")
        _add(db, "gang", "time, occasion", "time")  # older id
        hall = _add(db, "gang", "hall", "noun")

        picked = choose_lemma_card(db, "gang", "NOUN", None)
        assert (picked.card[0], picked.undecided) == (hall, False)

    def test_a_gloss_matching_no_card_is_undecided_and_keeps_the_old_answer(self):
        db = SRSDatabase(":memory:")
        hall = _add(db, "gang", "hall", "hall")
        _add(db, "gang", "time, occasion", "time")

        picked = choose_lemma_card(db, "gang", "NOUN", "walkway")
        assert (picked.card[0], picked.undecided) == (hall, True)

    def test_a_gloss_matching_two_cards_equally_is_undecided(self):
        """Slovene ``star``: old (≠new) / old (≠young), glossed "old"."""
        db = SRSDatabase(":memory:")
        first = _add(db, "star", "old (≠new)", "old (≠new)")
        _add(db, "star", "old (≠young)", "old (≠young)")

        picked = choose_lemma_card(db, "star", "ADJ", "old")
        assert (picked.card[0], picked.undecided) == (first, True)

    def test_a_unique_word_class_match_is_not_overridden_by_the_gloss(self):
        """THE CONTROL for tunatale-u8nz.22: the tagger reads the sentence, the
        gloss is one per lesson. Where the class leaves exactly one card, it
        decides."""
        db = SRSDatabase(":memory:")
        _add(db, "om", "if", "conjunction")
        prep = _add(db, "om", "about, around, by", "preposition")

        picked = choose_lemma_card(db, "om", "ADP", "if")
        assert (picked.card[0], picked.undecided) == (prep, False)

    def test_a_function_word_gloss_picks_the_function_word_card(self):
        """Live, 2026-10-06: the tagger called `vår` PRON, which is neither
        card's class, and every `vår` resolved to "spring"."""
        db = SRSDatabase(":memory:")
        _add(db, "vår", "spring", "noun")
        our = _add(db, "vår", "our", "determinative")

        picked = choose_lemma_card(db, "vår", "PRON", "our")
        assert (picked.card[0], picked.undecided) == (our, False)

    def test_a_lone_card_is_never_judged_against_the_gloss(self):
        db = SRSDatabase(":memory:")
        hall = _add(db, "gang", "hall", "noun")

        picked = choose_lemma_card(db, "gang", "NOUN", "time")
        assert (picked.card[0], picked.undecided) == (hall, False)

    def test_a_cloze_is_not_a_sense_candidate(self):
        db = SRSDatabase(":memory:")
        hall = _add(db, "gang", "hall", "noun")
        _add(db, "gang", "time", "", card_type="cloze")

        picked = choose_lemma_card(db, "gang", "NOUN", "time")
        assert (picked.card[0], picked.undecided) == (hall, False)

    def test_an_unknown_lemma_has_no_card(self):
        picked = choose_lemma_card(SRSDatabase(":memory:"), "gang", "NOUN", "time")
        assert (picked.card, picked.undecided) == (None, False)

    def test_resolve_lemma_card_is_the_same_choice(self):
        db = SRSDatabase(":memory:")
        _add(db, "gang", "hall", "noun")
        time = _add(db, "gang", "time, occasion", "time")

        assert resolve_lemma_card(db, "gang", "NOUN", "time")[0] == time


def _lesson(lines: list[str], glosses: dict[str, str]) -> Lesson:
    lesson = Lesson(
        title="Day 1",
        language_code=LANG,
        sections=[
            Section(
                section_type=SectionType.NATURAL_SPEED,
                phrases=[Phrase(text=t, voice_id="female-1", language_code=LANG, role="female-1") for t in lines],
            )
        ],
        key_phrases=[],
    )
    lesson.generation_metadata = {"token_glosses": glosses}
    return lesson


class TestReaderResolvesTheSense:
    def test_the_word_resolves_to_the_card_its_gloss_names(self):
        db = SRSDatabase(":memory:")
        _add(db, "gang", "hall", "noun")
        time = _add(db, "gang", "time, occasion", "time")

        lesson = _lesson(["en gang til"], {"gang": "time"})
        word = extract_transcript(lesson, db, LowercaseLemmatizer()).dialogue_lines[0].words[1]

        assert word.srs_item_id == time
        assert word.translation == "time, occasion"

    def test_two_lessons_two_senses(self):
        """The cache is per request, but it is keyed by lemma AND gloss inside
        one — a second gloss for the same lemma must not read the first's card."""
        db = SRSDatabase(":memory:")
        hall = _add(db, "gang", "hall", "noun")
        time = _add(db, "gang", "time, occasion", "time")

        lesson = _lesson(["en gang", "gangen"], {"gang": "time", "gangen": "the hall"})
        from app.srs.lemmatizer import TokenAnalysis
        from tests._helpers.lemmatizer import StubLemmatizer

        stub = StubLemmatizer()
        stub.set_sentence("en gang", [TokenAnalysis(surface="en", lemma="en"), TokenAnalysis("gang", "gang")])
        stub.set_sentence("gangen", [TokenAnalysis(surface="gangen", lemma="gang")])
        lines = extract_transcript(lesson, db, stub).dialogue_lines

        assert lines[0].words[1].srs_item_id == time
        assert lines[1].words[0].srs_item_id == hall

    def test_an_undecided_word_still_shows_as_tracked(self):
        db = SRSDatabase(":memory:")
        hall = _add(db, "gang", "hall", "hall")
        _add(db, "gang", "time, occasion", "time")

        lesson = _lesson(["en gang til"], {"gang": "walkway"})
        word = extract_transcript(lesson, db, LowercaseLemmatizer()).dialogue_lines[0].words[1]

        assert word.srs_item_id == hall


def _make_review_due(db: SRSDatabase, coll_id: int) -> None:
    _, item, _ = db.get_collocation_by_id(coll_id)
    rec = item.directions[Direction.RECOGNITION]
    rec.state = SRSState.REVIEW
    rec.last_review = datetime.now(UTC) - timedelta(days=5)
    rec.due_at = due_at_rollover_utc(anki_today() - timedelta(days=1))
    rec.reps = 5
    db.update_collocation(item)


def _setup_listen(glosses: dict[str, str], keys: tuple[str, str] = ("noun", "time")) -> tuple[SRSDatabase, int, int]:
    from app.storage.store import ContentStore

    db = SRSDatabase(":memory:")
    store = ContentStore(":memory:")
    store.save_lesson("lesson-1", "curriculum-1", 1, _lesson(["En gang til"], glosses))
    app.state.srs_db = db
    app.state.content_store = store
    db.set_anki_state_cache("daily_new_cap", "0")
    db.set_anki_state_cache("daily_review_cap", "50")
    hall = _add(db, "gang", "hall", keys[0])
    time = _add(db, "gang", "time, occasion", keys[1])
    _make_review_due(db, hall)
    _make_review_due(db, time)
    return db, hall, time


async def _preview_and_listen(db: SRSDatabase) -> tuple[set[int], set[int], list[dict]]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        preview = await client.get("/api/srs/content/lesson-1/listen-preview")
        listen = await client.post("/api/srs/listen", json={"content_id": "lesson-1"})
    assert preview.status_code == 200
    assert listen.status_code == 200
    candidates = preview.json()["candidates"]
    shown = {c["item_id"] for c in candidates if c["item_id"] is not None}
    staged = {p["collocation_id"] for p in db.get_pending_grades("lesson-1")}
    return shown, staged, candidates


class TestListenGradesTheSense:
    async def test_the_gloss_names_the_card_a_listen_grades(self):
        db, hall, time = _setup_listen({"gang": "time"})

        shown, staged, _ = await _preview_and_listen(db)

        assert time in shown and hall not in shown
        assert time in staged and hall not in staged

    async def test_a_surface_gloss_counts_as_evidence_for_its_lemma(self):
        """A listen decides per lemma, from every gloss the lesson gave any of
        its surfaces. (``En`` here is a surface whose own gloss is unrelated.)"""
        db, hall, time = _setup_listen({"en": "one", "gang": "time (en gang til = once more)"})

        _, staged, _ = await _preview_and_listen(db)

        assert time in staged and hall not in staged

    async def test_a_gloss_matching_neither_grades_neither_and_creates_nothing(self):
        db, hall, time = _setup_listen({"gang": "walkway"}, keys=("hall", "time"))
        db.set_anki_state_cache("daily_new_cap", "10")

        shown, staged, candidates = await _preview_and_listen(db)

        assert not ({hall, time} & shown), "the preview offered a card the lesson's gloss does not name"
        assert not ({hall, time} & staged), "the listen graded a card it could not tell was the right sense"
        assert "gang" not in {c["text"] for c in candidates if c["kind"] == "create"}
        with db._get_conn() as conn:
            assert conn.execute("SELECT COUNT(*) FROM collocations WHERE text = 'gang'").fetchone()[0] == 2

    async def test_without_glosses_the_old_card_is_graded(self):
        """Regression guard: a lesson with no glosses resolves as before."""
        db, hall, time = _setup_listen({})

        shown, staged, _ = await _preview_and_listen(db)

        assert hall in shown and time not in shown
        assert hall in staged and time not in staged
