"""/listen retries a missing gloss, at creation and on every later listen.

bd `tunatale-1wiw`. The generation-time LLM drops words from `dialogue_glosses`
when glossing a whole dialogue, so a word can reach card creation with no gloss —
the `ferskt` shape. `/listen` already *detected* this and shipped the card anyway
(`_resolve_gloss_translation` logs *"card created with empty translation"*).

Two dispatch points, and both are needed:

  * **at creation** — the word is new, so the background media task already has
    it in hand;
  * **on a later listen** — the row now EXISTS, so `_resolve_card_for_lemma`
    resolves it and the creation branch never runs again. Without this second
    point a single failed retry stranded the card permanently. Chosen over
    parking the card (2026-08-22): an unglossable word stays in the rotation and
    keeps trying rather than silently consuming a daily-new slot forever.

The regloss runs BEFORE the image fetch, because the Pixabay query is built from
the translation — an empty gloss is what produced the `img_.jpg` filename on the
real card.

The LLM double is passed via `app.state.llm`, never patched.
"""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.lesson import Lesson, Phrase, Section, SectionType
from app.models.syntactic_unit import SyntacticUnit
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401


class _FakeLLM:
    """Async LLM double keyed BY WORD, not by call order.

    A one-line lesson still yields several unglossed words under the suite's
    `lowercase` lemmatizer (`sporet`, `er`, `helt`, `ferskt`), and they are
    reglossed in an order the test does not control — an ordered double hands
    `ferskt`'s gloss to `er` and the assertion fails for a reason that has
    nothing to do with the code under test.

    ``fail_first`` maps a word to how many calls raise before it starts
    succeeding — a 429 / TPD exhaustion that later clears, which is the whole
    premise of retrying on a later listen.
    """

    def __init__(self, glosses: dict[str, str] | None = None, *, fail_first: dict[str, int] | None = None) -> None:
        self._glosses = glosses or {}
        self._fail_first = dict(fail_first or {})
        self.prompts: list[str] = []

    @staticmethod
    def _head(prompt: str) -> str:
        """The word being glossed, without the appended sentence.

        ``generate_word_gloss`` builds ``"<lemma> — in: <sentence>"``, and the
        sentence contains the word — so matching the whole prompt makes every
        word in the line look like every other. Split first, then match.
        """
        return prompt.split(" — in:")[0].strip()

    def _word_of(self, prompt: str) -> str | None:
        head = self._head(prompt)
        return next((w for w in {*self._glosses, *self._fail_first} if w in head), None)

    def calls_for(self, word: str) -> int:
        return sum(1 for p in self.prompts if word in self._head(p))

    async def complete(self, prompt, system_prompt=None, temperature=0.7, max_tokens=256):
        self.prompts.append(prompt)
        word = self._word_of(prompt)
        if self._fail_first.get(word, 0) > 0:
            self._fail_first[word] -= 1
            raise RuntimeError("429 rate limited")
        return self._glosses.get(word, "")


def _setup(phrase_text: str, llm) -> object:
    from app.srs.database import SRSDatabase
    from app.storage.store import ContentStore

    lesson = Lesson(
        title="Day 1",
        language_code="no",
        sections=[
            Section(
                section_type=SectionType.NATURAL_SPEED,
                phrases=[Phrase(text=phrase_text, voice_id="female-1", language_code="no", role="female-1")],
            )
        ],
        key_phrases=[],
    )
    db = SRSDatabase(":memory:")
    store = ContentStore(":memory:")
    store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
    app.state.srs_db = db
    app.state.content_store = store
    app.state.llm = llm
    db.set_anki_state_cache("daily_new_cap", "10")
    db.set_anki_state_cache("daily_review_cap", "10")
    return db


async def _listen(db) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
    assert resp.status_code == 200


def _translation(db, text: str) -> str:
    item = db.get_collocation(text)
    return "" if item is None else item.syntactic_unit.translation


class TestReglossAtCreation:
    async def test_a_word_the_generator_failed_to_gloss_gets_one_from_a_targeted_call(self):
        db = _setup("Sporet er helt ferskt", _FakeLLM({"ferskt": "fresh"}))
        await _listen(db)
        assert _translation(db, "ferskt") == "fresh", "the created card kept its empty gloss"

    async def test_the_sentence_the_word_appeared_in_is_sent_with_it(self):
        """Sense disambiguation is the whole reason a targeted retry beats the
        whole-dialogue gloss that dropped the word."""
        llm = _FakeLLM({"ferskt": "fresh"})
        db = _setup("Sporet er helt ferskt", llm)
        await _listen(db)
        assert any("Sporet er helt ferskt" in p for p in llm.prompts)

    async def test_a_word_the_generator_did_gloss_costs_no_llm_call(self):
        """Only the missing ones are retried — the free tier's TPD is the binding
        limit, so a call per created card would be paid on every lesson."""
        llm = _FakeLLM({"ferskt": "fresh"})
        db = _setup("Sporet er helt ferskt", llm)
        unit = SyntacticUnit(
            text="ferskt", translation="fresh", word_count=1, difficulty=1, source="llm", lemma="ferskt"
        )
        db.add_collocation(unit, language_code="no")
        await _listen(db)
        assert not any("ferskt" in p and "meaning" in p.lower() for p in llm.prompts)


class TestAFailedRetryIsSurvivable:
    async def test_an_llm_failure_leaves_the_card_unglossed_rather_than_failing_the_listen(self):
        db = _setup("Sporet er helt ferskt", _FakeLLM(fail_first={"ferskt": 99}))
        await _listen(db)
        assert _translation(db, "ferskt") == ""
        assert db.get_collocation("ferskt") is not None, "the listen dropped the card entirely"

    async def test_the_unglossed_card_is_not_linked_to_anki(self):
        db = _setup("Sporet er helt ferskt", _FakeLLM(fail_first={"ferskt": 99}))
        await _listen(db)
        assert db.get_collocation("ferskt").anki_note_id is None


class TestRetryOnALaterListen:
    """The row exists now, so the creation branch never fires again. This is the
    dispatch point that makes 'retry' rather than 'park' a real choice."""

    async def test_a_second_listen_retries_a_card_left_unglossed_by_the_first(self):
        db = _setup("Sporet er helt ferskt", _FakeLLM({"ferskt": "fresh"}, fail_first={"ferskt": 1}))
        await _listen(db)
        assert _translation(db, "ferskt") == "", "premise invalid — the first listen glossed it"

        await _listen(db)

        assert _translation(db, "ferskt") == "fresh", "the retry never fired on the second listen"

    async def test_a_successful_retry_marks_the_card_for_push(self):
        """The card may already be in Anki with an empty back; the new gloss has
        to get there."""
        db = _setup("Sporet er helt ferskt", _FakeLLM({"ferskt": "fresh"}, fail_first={"ferskt": 1}))
        await _listen(db)
        await _listen(db)

        guid = db.get_collocation("ferskt").guid
        assert "translation" in db.get_dirty_fields(guid)

    async def test_an_already_glossed_card_is_not_retried_on_every_listen(self):
        """Otherwise every listen of a lesson costs one LLM call per word in it."""
        llm = _FakeLLM({"ferskt": "fresh"})
        db = _setup("Sporet er helt ferskt", llm)
        await _listen(db)
        assert llm.calls_for("ferskt") == 1

        await _listen(db)

        # Per-word, not total: the other words in this line stay unglossed (the
        # double only answers for 'ferskt') and are legitimately retried, so a
        # total-call assertion would fail for the wrong reason.
        assert llm.calls_for("ferskt") == 1, "a glossed card was reglossed on a later listen"
