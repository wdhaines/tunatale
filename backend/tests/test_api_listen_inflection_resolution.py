"""Resolution drift #2: an inflected surface must find the card that teaches it.

Contract file for bd `tunatale-qi4b`. The deck's own ``Inflections`` table is
deck-authored ground truth — ``fersk``'s table lists ``fersk / ferskt / ferske``
and its example sentence is *"Dette brødet er ferskt."* — but nothing consulted
it when deciding whether a lemma was already carded.

Live repro 2026-08-20 (Norwegian deck, cid 3054): Stanza returned
``lemma='ferskt'`` for the neuter surface ``ferskt``, so
``get_collocation_by_lemma_with_id('ferskt')`` missed cid 196 (``lemma='fersk'``)
and ``/listen`` minted a second card. It shipped with an empty gloss and an empty
image query, reached Anki, and was failed 12 times before anyone noticed.

The cached analysis is the evidence that this is a lemmatizer defect and not a
missing feature — the same sentence lemmatizes the neuter ``helt`` correctly::

    {"surface": "helt",   "lemma": "hel",    "upos": "ADJ", "gender": "Neut"}
    {"surface": "ferskt", "lemma": "ferskt", "upos": "ADJ", "gender": "Neut"}

so no lemmatizer fix can be relied on here. These tests pin the deck-data
fallback instead. The suite's ``lowercase`` lemmatizer pin reproduces the
production shape exactly: lemma == surface == ``ferskt``.

Mirrors `test_api_listen_variant_resolution.py`'s conservation shape — what the
transcript resolves, ``/listen`` must grade and must not duplicate.
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

#: The real ``fersk`` note's Inflections field, trimmed of its <style> block.
#: Grammar labels live in <thead>, forms in <tbody> — the split
#: ``cloze_source.parse_inflection_forms`` already relies on.
FERSK_INFLECTIONS = """
<table class="tg">
<thead>
  <tr><th colspan="3">entall</th><th rowspan="2">flertall</th></tr>
  <tr><th>hankjønn&nbsp;/<br>hunkjønn</th><th>intetkjønn</th><th>bestemt form</th></tr>
</thead>
<tbody>
  <tr><td>fersk</td><td>ferskt</td><td>ferske</td><td>ferske</td></tr>
</tbody>
</table>
"""


#: A noun table, whose <tbody> also carries a gender LABEL cell ("hankjønn")
#: alongside the real forms — the shape that makes a second card listing the
#: same label ambiguous rather than resolvable.
def _noun_inflections(*forms: str) -> str:
    cells = "".join(f"<td>{f}</td>" for f in forms)
    return (
        '<table class="tg"><thead><tr><th>entall</th><th>flertall</th></tr></thead>'
        f"<tbody><tr><td>hankjønn</td>{cells}</tr></tbody></table>"
    )


def _setup_no_lesson(phrase_text: str):
    """Norwegian lesson; the deck with Inflections tables."""
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


def _seed_card(db, text: str, translation: str, inflections_html: str, *, review_due: bool = False) -> None:
    """An imported-deck vocab card carrying its own Inflections table."""
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
    if not review_due:
        return
    item = db.get_collocation(text)
    rec = item.directions[Direction.RECOGNITION]
    rec.state = SRSState.REVIEW
    rec.last_review = datetime.now(UTC) - timedelta(days=5)
    rec.due_at = due_at_rollover_utc(anki_today() - timedelta(days=1))
    rec.reps = 5
    db.update_collocation(item)


def _count_rows(db, text: str) -> int:
    with db._get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM collocations WHERE text = ? OR lemma = ?", (text, text)).fetchone()[0]


def _id_of(db, text: str) -> int:
    with db._get_conn() as conn:
        return conn.execute("SELECT id FROM collocations WHERE text = ?", (text,)).fetchone()[0]


class TestInflectedFormResolvesToTheCardThatTeachesIt:
    """The `ferskt` incident, pinned end to end."""

    async def test_listen_does_not_duplicate_a_word_its_own_deck_already_inflects(self):
        """The regression itself: creation budget available, card must NOT be minted."""
        db = _setup_no_lesson("Sporet er helt ferskt")
        _seed_card(db, "fersk", "fresh", FERSK_INFLECTIONS)
        db.set_anki_state_cache("daily_new_cap", "10")
        db.set_anki_state_cache("daily_review_cap", "10")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert resp.status_code == 200

        assert _count_rows(db, "ferskt") == 0, "listen minted a duplicate of a word the deck already inflects"

    async def test_listen_grades_the_base_card_for_an_inflected_surface(self):
        """Resolution is not merely suppression — the base card gets the credit."""
        db = _setup_no_lesson("Sporet er helt ferskt")
        _seed_card(db, "fersk", "fresh", FERSK_INFLECTIONS, review_due=True)
        db.set_anki_state_cache("daily_new_cap", "0")
        db.set_anki_state_cache("daily_review_cap", "10")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert resp.status_code == 200
        assert resp.json()["staged"] == 1, "the inflected surface did not stage its base card"

    async def test_transcript_links_the_inflected_surface_to_the_base_card(self):
        """Preview↔commit parity: the reader must show what /listen resolved."""
        db = _setup_no_lesson("Sporet er helt ferskt")
        _seed_card(db, "fersk", "fresh", FERSK_INFLECTIONS, review_due=True)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/lesson/lesson-1/transcript")
        assert resp.status_code == 200
        words = [w for line in resp.json()["dialogue_lines"] for w in line["words"]]
        ferskt = next(w for w in words if w["surface"].lower() == "ferskt")
        assert ferskt["srs_item_id"] == _id_of(db, "fersk"), "transcript left the inflected surface untracked"


class TestResolutionRefusesWhenTheEvidenceIsAmbiguous:
    """A wrong resolution grades a card the learner never met. Decline instead."""

    async def test_a_form_two_cards_both_list_resolves_to_neither(self):
        """`løfte`'s noun and verb notes list the same forms — 199 such forms in
        the real Norwegian deck, including the grammar labels that leak out of
        noun <tbody> cells. Ambiguity must fall through to normal creation, not
        pick a winner by row order."""
        db = _setup_no_lesson("Han fikk et lofte")
        _seed_card(db, "lofte-noun", "promise", _noun_inflections("lofte", "lofter"))
        _seed_card(db, "lofte-verb", "to lift", _noun_inflections("lofte", "loftet"))
        db.set_anki_state_cache("daily_new_cap", "10")
        db.set_anki_state_cache("daily_review_cap", "10")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert resp.status_code == 200
        assert _count_rows(db, "lofte") == 1, "an ambiguous inflected form must not resolve to one of its claimants"

    async def test_a_words_own_card_wins_over_another_cards_inflection_table(self):
        """16 forms in the real deck are one card's inflection AND another's
        headword (`heller`, `noe`, `rundt`, `snart`…). The headword must win —
        the inflection table is the LAST fallback, never a shortcut past a real
        card."""
        db = _setup_no_lesson("Jeg vil heller ga")
        _seed_card(db, "helle", "to pour", _noun_inflections("heller", "helte"))
        _seed_card(db, "heller", "rather", "", review_due=True)
        db.set_anki_state_cache("daily_new_cap", "0")
        db.set_anki_state_cache("daily_review_cap", "10")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert resp.status_code == 200

        graded_ids = {row["collocation_id"] for row in db.get_pending_grades("lesson-1")}
        assert _id_of(db, "heller") in graded_ids, "the surface's own card lost to another card's inflection table"


class TestTheIndexIsASnapshot:
    """``resolve_via_inflection_index`` is handed a map built earlier in the
    request. Nothing guarantees the row is still there when it is read."""

    def test_a_stale_entry_leaves_the_word_untracked_rather_than_raising(self):
        from app.srs.database import SRSDatabase
        from app.srs.transcript import resolve_via_inflection_index

        db = SRSDatabase(":memory:")
        assert resolve_via_inflection_index(db, {"ferskt": 999999}, "ferskt") is None

    def test_a_key_absent_from_the_index_resolves_to_none(self):
        from app.srs.database import SRSDatabase
        from app.srs.transcript import resolve_via_inflection_index

        db = SRSDatabase(":memory:")
        assert resolve_via_inflection_index(db, {}, "ferskt", "fersk") is None

    def test_the_first_matching_key_wins(self):
        """Surface before lemma at the reader, lemma before surfaces at /listen —
        both rely on the order they pass keys in, not on this helper choosing."""
        from app.srs.database import SRSDatabase
        from app.srs.transcript import resolve_via_inflection_index

        db = SRSDatabase(":memory:")
        _seed_card(db, "fersk", "fresh", FERSK_INFLECTIONS)
        _seed_card(db, "helle", "to pour", _noun_inflections("heller"))
        fersk_id, helle_id = _id_of(db, "fersk"), _id_of(db, "helle")

        index = {"ferskt": fersk_id, "heller": helle_id}
        assert resolve_via_inflection_index(db, index, "heller", "ferskt")[0] == helle_id
        assert resolve_via_inflection_index(db, index, "ferskt", "heller")[0] == fersk_id
