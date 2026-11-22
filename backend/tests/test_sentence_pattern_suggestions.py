"""Grade suggests the day's pattern forms as sentence blanks (tunatale-ve4p.37).

Design: ``.beads-tasks/briefs/design-sentence-mining-2026-11.md`` § 8. A word pattern's pace-gate
evidence is the sentence cards whose blank says one of its forms (#445); nothing made them. In a
lesson whose ``affix_drill`` names a word pattern, the read model marks each word that says one of
its forms as ``suggested``, read by the gate's own reader (``pattern_forms.FormReader``), so a
suggestion the gate would not count cannot exist. One per form per lesson; never a form the lesson
already has a card or a not-wanted mark for.

The owner, 2026-11-22: on by default up to the day's new-card budget, as word suggestions are. The
Grade plan says how many fit (``sentence_budget``), after the word creations and the contrast cards.

Cebuano: an affix pattern is a facet only its plugin registers. The gate's test lemmatizer knows
molakaw, milakaw, moinom and miinom (``tests/_helpers/pattern_gate.py``).
"""

from __future__ import annotations

import pytest

from app.api import srs as srs_mod
from app.generation.pattern_gate import _Deck
from app.languages import get_a1_morphology
from app.main import app
from app.models.lesson import Lesson, Phrase, Section, SectionType
from app.srs.sentence_card import sentence_card
from app.srs.sentence_lines import sentence_lines
from app.storage.store import ContentStore
from tests._helpers.pattern_gate import install_gate_lemmatizer

LINES = [
    ("female-1", "Molakaw ko.", "I will go."),  # 1: molakaw, suggested
    ("male-1", "Moinom siya ug kape.", "He will drink coffee."),  # 2: moinom, suggested
    ("female-1", "Molakaw sila.", "They will go."),  # 3: molakaw again: not suggested
    ("male-1", "Maayo.", "Good."),  # 4: no form
]
GLOSSES = {"molakaw": "will go", "moinom": "will drink", "ko": "I", "siya": "he", "kape": "coffee"}


@pytest.fixture(autouse=True)
def lemmatizer(monkeypatch):
    stub = install_gate_lemmatizer(monkeypatch)
    monkeypatch.setattr(srs_mod, "get_lemmatizer", lambda code: stub)
    return stub


def _lesson(pattern: str | None = "mo-mi", lines=LINES) -> Lesson:
    story = {
        "title": "Paglakaw",
        "key_phrases": [],
        "scenes": [{"label": "Sa balay", "lines": [{"speaker": s, "text": t, "translation": e} for s, t, e in lines]}],
    }
    metadata: dict = {"story": story, "token_glosses": GLOSSES}
    if pattern is not None:
        # As a generated lesson records its drill (live supper-after-the-beach: these four keys).
        metadata["affix_drill"] = {"pattern": pattern, "roots": ["lakaw", "inom"], "new_roots": [], "note": False}
    return Lesson(
        title="Paglakaw",
        language_code="ceb",
        sections=[Section(SectionType.NATURAL_SPEED, [Phrase(t, "voice", "ceb", role=s) for s, t, _ in lines])],
        generation_metadata=metadata,
    )


def _install(db, lesson: Lesson) -> ContentStore:
    store = ContentStore(":memory:")
    store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
    app.state.srs_db = db
    app.state.content_store = store
    return store


def _suggested(lines) -> list[tuple[str, str]]:
    return [(ln.line_key, ln.text[t.start : t.end]) for ln in lines for t in ln.tokens if t.suggested]


def _edits(lines, *, off=()) -> list[dict]:
    """The Grade page's Save for *lines*: every suggestion on, but those named in *off* declined."""
    edits: dict[str, dict] = {}
    for ln in lines:
        for t in ln.tokens:
            if t.suggested:
                word = ln.text[t.start : t.end]
                side = "off" if word in off else "on"
                edits.setdefault(ln.line_key, {"line_key": ln.line_key, "on": [], "off": []})[side].append(
                    [t.start, t.end]
                )
    return list(edits.values())


async def _post(url: str, body: dict) -> dict:
    from tests.test_api_grade_page import _post as post

    resp = await post(url, body)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _plan() -> dict:
    from tests.test_api_grade_page import _get

    resp = await _get("/api/srs/content/lesson-1/grade-plan")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _mo_mi():
    return next(p for p in get_a1_morphology("ceb").patterns if p.key == "mo-mi")


class TestReadModel:
    def test_each_form_of_the_lessons_pattern_is_suggested_once(self, srs_db):
        lines = sentence_lines(srs_db, _lesson(), "lesson-1")
        assert _suggested(lines) == [("sent:lesson-1:1", "Molakaw"), ("sent:lesson-1:2", "Moinom")]
        token = lines[0].tokens[0]
        assert (token.state, token.origin, token.in_deck) == ("off", "mine", False)  # a hint, not a card

    def test_a_lesson_with_no_affix_drill_suggests_nothing(self, srs_db):
        assert _suggested(sentence_lines(srs_db, _lesson(None), "lesson-1")) == []

    def test_another_patterns_forms_are_not_suggested(self, srs_db):
        lines = [("female-1", "Naglakaw ko.", "I walked."), ("male-1", "Molakaw ko.", "I will go.")]
        got = _suggested(sentence_lines(srs_db, _lesson("mag-nag", lines), "lesson-1"))
        assert got == [("sent:lesson-1:1", "Naglakaw")]

    def test_a_sentence_pattern_suggests_nothing(self, srs_db):
        """on-gi counts its focus cards, not sentence blanks: a blank would be no evidence."""
        sentence_pattern = next(p for p in get_a1_morphology("ceb").patterns if p.sentences)
        assert _suggested(sentence_lines(srs_db, _lesson(sentence_pattern.key), "lesson-1")) == []

    def test_an_unknown_pattern_key_suggests_nothing(self, srs_db):
        assert _suggested(sentence_lines(srs_db, _lesson("no-such-pattern"), "lesson-1")) == []

    def test_a_form_the_lesson_already_has_a_card_for_is_not_suggested_again(self, srs_db):
        """Line 1's Molakaw is mined: line 3's Molakaw is the same form, so nothing new is offered for it."""
        from app.srs.sentence_lines import line_card

        lesson = _lesson()
        srs_db.add_collocation(line_card(lesson, 1, "lesson-1").with_blank((0, 7), "will go").unit(), "ceb")
        assert _suggested(sentence_lines(srs_db, lesson, "lesson-1")) == [("sent:lesson-1:2", "Moinom")]

    def test_the_suggestions_are_the_gates_forms_exactly(self, srs_db, lemmatizer):
        """Control on the reader: a word the gate's lemmatizer does not read as mo-/mi- is not suggested."""
        lemmatizer.set_analysis("moinom", "moinom", upos="NOUN")
        assert _suggested(sentence_lines(srs_db, _lesson(), "lesson-1")) == [("sent:lesson-1:1", "Molakaw")]


class TestS1ClosingTheLoop:
    async def test_s1_a_save_keeping_the_suggestions_makes_the_gates_evidence(self, srs_db):
        store = _install(srs_db, _lesson())
        await _post("/api/srs/content/lesson-1/grade", {})  # studied first: its affix drill card is evidence too
        before = set(_Deck("ceb", srs_db, store).evidence(_mo_mi()))
        lines = sentence_lines(srs_db, store.get_lesson("lesson-1"), "lesson-1")
        await _post("/api/srs/content/lesson-1/grade", {"sentence_edits": _edits(lines)})
        rows = {
            row_id
            for key in ("sent:lesson-1:1", "sent:lesson-1:2")
            for _, item, row_id in srs_db.list_sibling_rows(key)
            if sentence_card(item.syntactic_unit).blank is not None
        }
        assert len(rows) == 2
        assert set(_Deck("ceb", srs_db, store).evidence(_mo_mi())) - before == rows
        assert {srs_db.get_collocation_by_id(r)[1].syntactic_unit.source for r in rows} == {"user"}

    async def test_s1_after_the_save_the_forms_are_cards_and_nothing_is_suggested(self, srs_db):
        store = _install(srs_db, _lesson())
        lines = sentence_lines(srs_db, store.get_lesson("lesson-1"), "lesson-1")
        await _post("/api/srs/content/lesson-1/grade", {"sentence_edits": _edits(lines)})
        assert _suggested(sentence_lines(srs_db, store.get_lesson("lesson-1"), "lesson-1")) == []


class TestS2Declined:
    async def test_s2_a_declined_suggestion_is_marked_and_not_offered_again(self, srs_db):
        store = _install(srs_db, _lesson())
        lines = sentence_lines(srs_db, store.get_lesson("lesson-1"), "lesson-1")
        await _post("/api/srs/content/lesson-1/grade", {"sentence_edits": _edits(lines, off={"Moinom"})})
        assert "sent:lesson-1:2:0-6" in srs_db.sentence_card_marks()
        assert not srs_db.list_sibling_rows("sent:lesson-1:2")
        after = sentence_lines(srs_db, store.get_lesson("lesson-1"), "lesson-1")
        assert _suggested(after) == []  # Molakaw is a card now, Moinom declined
        await _post("/api/srs/content/lesson-1/grade", {"studied": True})
        assert not srs_db.list_sibling_rows("sent:lesson-1:2")  # the next Save does not mint it

    async def test_a_declined_suggestion_turned_on_by_hand_is_made_and_unmarked(self, srs_db):
        _install(srs_db, _lesson())
        srs_db.add_sentence_card_mark("sent:lesson-1:2:0-6")
        await _post(
            "/api/srs/content/lesson-1/grade",
            {"sentence_edits": [{"line_key": "sent:lesson-1:2", "on": [[0, 6]], "off": []}]},
        )
        assert "sent:lesson-1:2:0-6" not in srs_db.sentence_card_marks()
        assert len(srs_db.list_sibling_rows("sent:lesson-1:2")) == 1

    async def test_a_mined_form_turned_off_is_marked_so_it_is_not_suggested_again(self, srs_db):
        store = _install(srs_db, _lesson())
        lines = sentence_lines(srs_db, store.get_lesson("lesson-1"), "lesson-1")
        await _post("/api/srs/content/lesson-1/grade", {"sentence_edits": _edits(lines)})
        await _post(
            "/api/srs/content/lesson-1/grade",
            {"sentence_edits": [{"line_key": "sent:lesson-1:1", "on": [], "off": [[0, 7]]}]},
        )
        assert "sent:lesson-1:1:0-7" in srs_db.sentence_card_marks()
        # Line 3 says the same form: the learner turned it down, so it is not offered there either.
        assert _suggested(sentence_lines(srs_db, store.get_lesson("lesson-1"), "lesson-1")) == []

    async def test_control_a_word_that_says_no_pattern_turned_off_writes_no_mark(self, srs_db):
        _install(srs_db, _lesson())
        await _post(
            "/api/srs/content/lesson-1/grade",
            {"sentence_edits": [{"line_key": "sent:lesson-1:2", "on": [], "off": [[15, 19]]}]},  # kape
        )
        assert srs_db.sentence_card_marks() == set()


class TestBudget:
    async def test_the_plan_lists_the_suggestions_and_how_many_fit_the_day(self, srs_db):
        _install(srs_db, _lesson())
        plan = await _plan()
        sentences = plan["suggestions"]["sentences"]
        assert [(s["line_key"], [t["suggested"] for t in s["tokens"]]) for s in sentences][:2] == [
            ("sent:lesson-1:1", [True, False]),
            ("sent:lesson-1:2", [True, False, False, False]),
        ]
        words = sum(w["will_create"] for w in plan["suggestions"]["words"])
        contrast = sum(c["will_create"] for c in plan["suggestions"]["contrast"])
        assert plan["suggestions"]["sentence_budget"] == 20 - words - contrast  # the default cap

    async def test_the_budget_is_what_the_words_leave(self, srs_db):
        _install(srs_db, _lesson())
        srs_db.set_anki_state_cache("daily_new_cap", "2")
        plan = await _plan()
        words = sum(w["will_create"] for w in plan["suggestions"]["words"])
        assert words == 2
        assert plan["suggestions"]["sentence_budget"] == 0

    async def test_the_budget_is_never_negative(self, srs_db):
        _install(srs_db, _lesson())
        srs_db.set_anki_state_cache("daily_new_cap", "0")
        assert (await _plan())["suggestions"]["sentence_budget"] == 0
