"""A listen that mints a Norwegian noun gives it its gender article (tunatale-vvb6).

The bead's oracle. `mark_lesson_listened` built its SyntacticUnit with no
`article=` at all, so every noun a listen minted had a blank article; and on
prod the table lemmatizer tags no gender, so even click-to-add was blank. The
stub below returns gender="" exactly as prod's TableLemmatizer does, which is
what makes this test about PROD rather than about Stanza.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.lesson import Lesson, Phrase, Section, SectionType
from app.srs.lemmatizer import TokenAnalysis
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401
from tests._helpers.lemmatizer import StubLemmatizer

pytestmark = pytest.mark.anyio

SENTENCE = "Hun kjøper kake og et hus."


def _tok(surface, lemma, upos):
    # gender="" for every token: what the production table lemmatizer returns.
    return TokenAnalysis(surface=surface, lemma=lemma, upos=upos, case="", number="", person="", gender="")


def _setup(monkeypatch, language_no):
    import app.api.srs as srs_mod
    from app.srs.database import SRSDatabase
    from app.storage.store import ContentStore

    stub = StubLemmatizer()
    stub.set_sentence(
        SENTENCE,
        [
            _tok("Hun", "hun", "PRON"),
            _tok("kjøper", "kjøpe", "VERB"),
            _tok("kake", "kake", "NOUN"),
            _tok("og", "og", "CCONJ"),
            _tok("et", "en", "DET"),
            _tok("hus", "hus", "NOUN"),
            _tok(".", ".", "PUNCT"),
        ],
    )
    monkeypatch.setattr(srs_mod, "get_lemmatizer", lambda code: stub)
    lesson = Lesson(
        title="Day 1",
        language_code="no",
        sections=[
            Section(
                section_type=SectionType.NATURAL_SPEED,
                phrases=[Phrase(text=SENTENCE, voice_id="female-1", language_code="no", role="female-1")],
            )
        ],
        key_phrases=[],
    )
    db = SRSDatabase(":memory:")
    db.set_anki_state_cache("daily_new_cap", "20")
    store = ContentStore(":memory:")
    store.save_lesson("lesson-no", "curriculum-no", 1, lesson)
    app.state.srs_db = db
    app.state.content_store = store
    app.state.language = language_no
    return db


async def _listen() -> dict:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/srs/listen", json={"content_id": "lesson-no"})
    assert resp.status_code == 200
    return resp.json()


async def test_minted_nouns_get_their_article_even_with_no_tagged_gender(monkeypatch, language_no):
    db = _setup(monkeypatch, language_no)

    await _listen()

    kake = db.get_collocation_by_lemma("kake")
    hus = db.get_collocation_by_lemma("hus")
    assert kake is not None and hus is not None, "the listen should have minted both nouns"
    assert kake.syntactic_unit.article == "ei/en"
    assert hus.syntactic_unit.article == "et"


async def test_a_minted_verb_gets_no_article(monkeypatch, language_no):
    db = _setup(monkeypatch, language_no)

    await _listen()

    verb = db.get_collocation_by_lemma("kjøpe")
    assert verb is not None, "the listen should have minted the verb"
    assert verb.syntactic_unit.article == ""
