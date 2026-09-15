"""Two surface forms of one lemma are ONE card, not two.

Contract file for bd `tunatale-og4d`. The sibling of
``test_api_listen_kp_word_collision.py``: that one reconciled the WORD pass
against the KEY-PHRASE pass, and its docstring already states the invariant
this file extends — *"Identity is the resolved collocation id, never the
text."* Nothing reconciled the word pass against **itself**.

Live repro 2026-09-13 (Norwegian deck, review session
``the-file-that-was-moved-b90b7e14``): the preview returned 150 candidates, of
which ``mappe`` and ``mappen`` were two rows sharing ``item_id`` 1302 — the only
duplicated id in the whole response. The lesson page then asked the user to
"Check your work — review 1 word: mappe" for a card they had already graded.

The two keys arise from a lemmatizer defect plus a fallback that is working as
designed. The cached analysis is the evidence::

    {"surface": "mappe",  "lemma": "mappe"}   x6
    {"surface": "mappen", "lemma": "mapp"}    x9
    {"surface": "Mappen", "lemma": "mapp"}    x3

Stanza over-strips ``mappen`` to ``mapp`` (the ``snømenn`` -> ``snøm`` class), so
``_card_key_for_lemma`` correctly rejects the fragment against NST and keys the
card on the surface instead — ``mappen``. That fallback is load-bearing (bd
``tunatale-q5pl``) and is not the bug. The bug is that the resulting second key
resolves to a card that ALREADY has a row, and nothing dedupes by the resolved
id.

The half that loses data is the commit. ``word_ratings`` is keyed by lemma
STRING, so confirming ``mappe`` leaves ``mappen`` unconfirmed; the unconfirmed
twin falls into the auto-rated remainder that ``mark_lesson_listened`` stages
into ``pending_listen_grades``, and the user is re-asked a card they just
reviewed. Exactly one pending row existed in the live database and it was
``(1302, recognition, good, 'ahead')``.

These tests do not depend on Stanza — CI installs no language groups. The suite's
deterministic ``lowercase`` lemmatizer pin reproduces the production SHAPE via
the deck's own Inflections table instead: ``mappe`` resolves directly and
``mappen`` resolves through ``resolve_via_inflection_index``. Two keys, one card,
which is the only property under test.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.lesson import Lesson, Phrase, Section, SectionType
from app.models.srs_item import Direction, SRSState
from app.models.syntactic_unit import BackField, SyntacticUnit
from app.srs.anki_mirror.rollover import anki_today, due_at_rollover_utc
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401

PREVIEW_URL = "/api/srs/content/lesson-1/listen-preview"
LISTEN_URL = "/api/srs/listen"


def _noun_inflections(*forms: str) -> str:
    """A noun table whose <tbody> carries a gender LABEL cell beside the forms."""
    cells = "".join(f"<td>{f}</td>" for f in forms)
    return (
        '<table class="tg"><thead><tr><th>entall</th><th>flertall</th></tr></thead>'
        f"<tbody><tr><td>hankjønn</td>{cells}</tr></tbody></table>"
    )


def _setup_no_lesson(phrase_text: str):
    from app.srs.database import SRSDatabase
    from app.storage.store import ContentStore

    lesson = Lesson(
        title="Day 1",
        language_code="no",
        sections=[
            Section(
                section_type=SectionType.NATURAL_SPEED,
                phrases=[
                    Phrase(text=phrase_text, voice_id="female-1", language_code="no", role="female-1"),
                ],
            )
        ],
        key_phrases=[],
    )
    db = SRSDatabase(":memory:")
    store = ContentStore(":memory:")
    store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
    app.state.srs_db = db
    app.state.content_store = store
    return db


def _seed_review_card(db, text: str, translation: str, inflections_html: str) -> None:
    """An imported-deck vocab card in REVIEW state, due yesterday."""
    unit = SyntacticUnit(
        text=text,
        translation=translation,
        word_count=1,
        difficulty=1,
        source="test",
        lemma=text,
        extras=(BackField(label="Inflections", html=inflections_html, tier="details"),),
    )
    db.add_collocation(unit, language_code="no")
    item = db.get_collocation(text)
    rec = item.directions[Direction.RECOGNITION]
    rec.state = SRSState.REVIEW
    rec.last_review = datetime.now(UTC) - timedelta(days=5)
    rec.due_at = due_at_rollover_utc(anki_today() - timedelta(days=1))
    rec.reps = 5
    db.update_collocation(item)


def _id_of(db, text: str) -> int:
    with db._get_conn() as conn:
        return conn.execute("SELECT id FROM collocations WHERE text = ?", (text,)).fetchone()[0]


def _pending_rows(db, content_id: str = "lesson-1") -> list[tuple]:
    with db._get_conn() as conn:
        return conn.execute(
            "SELECT collocation_id, direction, rating FROM pending_listen_grades WHERE lesson_id = ?",
            (content_id,),
        ).fetchall()


def _caps(db, *, new_cap: int = 0, review_cap: int = 10) -> None:
    db.set_anki_state_cache("daily_new_cap", str(new_cap))
    db.set_anki_state_cache("daily_review_cap", str(review_cap))


#: One sentence carrying BOTH surfaces of the same card — the production shape.
BOTH_SURFACES = "Jeg fant en mappe og mappen var tom"


class TestTwoSurfacesOfOneCardProduceOneRow:
    """The `mappe`/`mappen` incident, pinned end to end."""

    async def test_preview_returns_one_row_per_card_not_one_per_surface(self):
        """The visible half: the duplicated row on screen."""
        db = _setup_no_lesson(BOTH_SURFACES)
        _seed_review_card(db, "mappe", "folder", _noun_inflections("mappe", "mappen"))
        _caps(db)
        card_id = _id_of(db, "mappe")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(PREVIEW_URL)
        assert resp.status_code == 200

        rows = [c for c in resp.json()["candidates"] if c["item_id"] == card_id]
        assert len(rows) == 1, f"one card produced {len(rows)} preview rows: {[r['text'] for r in rows]}"

    async def test_no_candidate_id_is_duplicated(self):
        """The property, stated over the whole response rather than one card.

        This is the assertion that survives a change of example: no tracked row
        may share an ``item_id`` with any other tracked row, whatever the
        lemmatizer did to the surfaces.
        """
        db = _setup_no_lesson(BOTH_SURFACES)
        _seed_review_card(db, "mappe", "folder", _noun_inflections("mappe", "mappen"))
        _caps(db)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(PREVIEW_URL)
        ids = [c["item_id"] for c in resp.json()["candidates"] if c["item_id"] is not None]
        assert len(ids) == len(set(ids)), f"duplicated item_ids in preview: {sorted(ids)}"

    async def test_confirming_the_card_stages_no_pending_grade_for_its_twin(self):
        """The half that loses data: a card graded for real, then re-asked.

        The user confirms the row they were shown. If a second key for the same
        card survives into the commit, it is auto-rated and staged — and "Check
        your work" asks for a card whose ``last_review`` is already today.
        """
        db = _setup_no_lesson(BOTH_SURFACES)
        _seed_review_card(db, "mappe", "folder", _noun_inflections("mappe", "mappen"))
        _caps(db)
        card_id = _id_of(db, "mappe")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            preview = await client.get(PREVIEW_URL)
            row = next(c for c in preview.json()["candidates"] if c["item_id"] == card_id)
            resp = await client.post(
                LISTEN_URL,
                json={
                    "content_id": "lesson-1",
                    # Keyed by the card's id, not its text — the whole point of
                    # the protocol half of this fix. `row["text"]` is whichever
                    # surface won the tie-break and is NOT an identity.
                    "word_ratings": {str(row["item_id"]): "good"},
                    "confirmed_words": [row["item_id"]],
                },
            )
        assert resp.status_code == 200

        staged = [r for r in _pending_rows(db) if r[0] == card_id]
        assert staged == [], f"a confirmed card was also staged into the pending bucket: {staged}"


class TestTheSurvivingRowIsNamedWithARealWord:
    """WHICH of the two keys wins — the user's explicit call, 2026-09-15.

    ⚠️ The tests above do NOT discriminate this. In ``BOTH_SURFACES`` the bare
    ``mappe`` appears before ``mappen``, so "first in candidate order" and "the
    key matching the card's stored lemma" pick the same winner and a tie-break
    implemented either way passes. This class inverts the sentence so the two
    rules disagree, which is the only arrangement that can tell them apart.

    The stored lemma must win even when it appears SECOND, because the surviving
    row is the one the learner reads: ``mappe`` is the headword the deck itself
    uses, and ``mappen`` is an inflected surface that only looks like a headword
    because Stanza truncated the real lemma to ``mapp``.
    """

    #: `mappen` FIRST — so first-seen would name the row with the inflected form.
    INVERTED = "Jeg fant mappen og en mappe var tom"

    async def test_the_stored_lemma_wins_even_when_it_appears_second(self):
        db = _setup_no_lesson(self.INVERTED)
        _seed_review_card(db, "mappe", "folder", _noun_inflections("mappe", "mappen"))
        _caps(db)
        card_id = _id_of(db, "mappe")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(PREVIEW_URL)

        rows = [c for c in resp.json()["candidates"] if c["item_id"] == card_id]
        assert len(rows) == 1, f"one card produced {len(rows)} rows: {[r['text'] for r in rows]}"
        assert rows[0]["text"] == "mappe", (
            f"the surviving row is named {rows[0]['text']!r}, the inflected surface — "
            "first-seen won instead of the card's stored lemma"
        )
