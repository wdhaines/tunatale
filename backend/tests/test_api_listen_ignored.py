"""Item 3: cards must not be created for explicitly-ignored words.

Two independent mechanisms exist for ignoring words (see brief §Item 3):
card-less ignore (ignored_lemmas table, TT-only) and carded ignore
(SUSPENDED state, synced). This file tests only the card-less path.

The carded-ignore path (SUSPENDED) is a characterization test ensuring
it still works — not a new behavior.
"""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.lesson import Lesson, Phrase, Section, SectionType
from app.models.srs_item import Direction, SRSState
from app.models.syntactic_unit import SyntacticUnit
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401

PREVIEW_URL = "/api/srs/lesson/lesson-1/listen-preview"
LISTEN_URL = "/api/srs/listen"


def _setup(phrases: list[str], language_code: str = "sl"):
    from app.srs.database import SRSDatabase
    from app.storage.store import ContentStore

    lesson = Lesson(
        title="Day 1",
        language_code=language_code,
        sections=[
            Section(
                section_type=SectionType.NATURAL_SPEED,
                phrases=[
                    Phrase(text=t, voice_id="female-1", language_code=language_code, role="female-1") for t in phrases
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


async def _get_preview() -> dict:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(PREVIEW_URL)
    assert resp.status_code == 200
    return resp.json()


async def _post_listen(payload: dict) -> dict:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(LISTEN_URL, json=payload)
    assert resp.status_code == 200
    return resp.json()


class TestIgnoredLemmaPreviewAndCommit:
    """One test driving BOTH endpoints against one seeded DB (the
    TestPreviewMatchesListenCreation pattern): with an ignored lemma
    present in the lesson, the preview emits no create row for it and
    the commit creates no collocation for it."""

    async def test_ignored_lemma_absent_from_preview_and_commit(self):
        db = _setup(["anna boris"])
        db.add_ignored_lemma("sl", "anna")

        preview = await _get_preview()
        preview_texts = {c["text"] for c in preview["candidates"]}
        assert "anna" not in preview_texts
        assert "boris" in preview_texts

        listen = await _post_listen({"lesson_id": "lesson-1"})
        assert listen["created"] == 1
        assert db.get_collocation_by_lemma("boris") is not None
        assert db.get_collocation_by_lemma("anna") is None


class TestIgnoredLemmaMixedCase:
    """Mixed-case seed: write 'Anna' (capitalized) to the table and assert
    the lemma 'anna' is still suppressed. Pins both-sides normalization."""

    async def test_capitalized_ignore_still_suppresses_lowercase_lemma(self):
        db = _setup(["anna boris"])
        # add_ignored_lemma lowercases on write; but we test the READ side
        # casefold — the brief says normalize on read.
        # Store "Anna" directly to test the read-side casefold:
        with db._get_conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO ignored_lemmas (language_code, lemma) VALUES (?, ?)",
                ("sl", "Anna"),
            )
            db._commit(conn)

        preview = await _get_preview()
        preview_texts = {c["text"] for c in preview["candidates"]}
        assert "anna" not in preview_texts
        assert "boris" in preview_texts


class TestCardedIgnoredLemmaPreviewCommitParity:
    """The card-less ignore list suppresses CREATION only — a lemma that is
    both in ``ignored_lemmas`` AND has a live card is graded as before, and
    the preview and the commit must agree about that.

    Pins the asymmetry that shipped in the first cut: the preview applied the
    ignore check before ``_resolve_card_for_lemma`` while ``mark_lesson_listened``
    applied it only in its untracked branch, so this lemma was hidden from the
    modal and staged by the commit anyway — a grade the user never saw offered
    (the 6a5c718 preview↔commit bug class).
    """

    async def test_carded_ignored_lemma_is_shown_and_staged(self):
        from datetime import UTC, datetime, timedelta

        from app.srs.anki_mirror.rollover import anki_today, due_at_rollover_utc

        db = _setup(["anna boris"])
        unit = SyntacticUnit(text="anna", translation="t-anna", word_count=1, difficulty=1, source="test")
        db.add_collocation(unit, language_code="sl")
        item = db.get_collocation("anna")
        rec = item.directions[Direction.RECOGNITION]
        rec.state = SRSState.REVIEW
        rec.due_at = due_at_rollover_utc(anki_today() - timedelta(days=1))
        rec.last_review = datetime.now(UTC) - timedelta(days=5)
        rec.reps = 5
        db.update_collocation(item)
        anna_id = db.get_collocation_id_by_guid(item.guid)
        db.add_ignored_lemma("sl", "anna")

        preview = await _get_preview()
        by_text = {c["text"]: c for c in preview["candidates"]}
        # Shown as an ordinary due row — the ignore list governs creation only.
        assert "anna" in by_text
        assert by_text["anna"]["grade_class"] == "due"
        assert by_text["anna"]["kind"] == "word"

        await _post_listen({"lesson_id": "lesson-1"})
        # ...and the commit stages exactly the row the preview offered.
        assert db.get_pending_grade(anna_id, Direction.RECOGNITION.value) is not None
        # Control: the non-ignored card-less sibling still gets created.
        assert db.get_collocation_by_lemma("boris") is not None


class TestSuspendedCardStillExcluded:
    """A lemma with a SUSPENDED card (the carded-ignore path) is still
    absent from both the create rows and the graded rows — a
    characterization test for the behavior that already works."""

    async def test_suspended_card_excluded_from_preview(self):
        db = _setup(["anna boris"])
        # Create a card for "anna" then suspend it
        unit = SyntacticUnit(text="anna", translation="t-anna", word_count=1, difficulty=1, source="test")
        db.add_collocation(unit, language_code="sl")
        item = db.get_collocation("anna")
        rec = item.directions[Direction.RECOGNITION]
        rec.state = SRSState.REVIEW
        from datetime import UTC, datetime, timedelta

        from app.srs.anki_mirror.rollover import anki_today, due_at_rollover_utc

        rec.due_at = due_at_rollover_utc(anki_today() - timedelta(days=1))
        rec.last_review = datetime.now(UTC) - timedelta(days=5)
        db.update_collocation(item)
        colloc_id = db.get_collocation_id_by_guid(item.guid)
        db.set_suspended(colloc_id, True)

        preview = await _get_preview()
        preview_texts = {c["text"] for c in preview["candidates"]}
        # SUSPENDED is not LEARNING/RELEARNING/REVIEW → _listen_grade_class returns None
        assert "anna" not in preview_texts
        assert "boris" in preview_texts


class TestNorwegianProperNounIgnore:
    """Reproduction attempt for the 2026-07-29 field incident: three lemmas on
    the card-less ignore list ('hansen', 'lund', 'alibi') acquired vocab cards
    with ``source='llm'`` — the signature of ``mark_lesson_listened``'s staged
    creation loop — four days AFTER the ignore guard shipped (4df8cab).

    Difference from the tests above, and the only shape difference the field
    data shows: the language is Norwegian, and the lemmas are proper nouns that
    appear CAPITALIZED in the source text ('Hansen'), so the guard's
    ``lemma.lower() in ignored`` depends on what the lemmatizer emits for a
    capitalized token.
    """

    async def test_capitalized_proper_noun_is_not_created_in_norwegian(self):
        db = _setup(
            ["Hansen kom til Lund.", "Hansen fant et alibi."],
            language_code="no",
        )
        for lem in ("hansen", "lund", "alibi"):
            db.add_ignored_lemma("no", lem)

        preview = await _get_preview()
        preview_texts = {c["text"].lower() for c in preview["candidates"]}
        assert "hansen" not in preview_texts, f"ignored lemma offered for creation: {preview_texts}"
        assert "lund" not in preview_texts, f"ignored lemma offered for creation: {preview_texts}"
        assert "alibi" not in preview_texts, f"ignored lemma offered for creation: {preview_texts}"

        await _post_listen({"lesson_id": "lesson-1"})
        for lem in ("hansen", "lund", "alibi"):
            assert db.get_collocation_by_lemma(lem) is None, f"card created for ignored lemma {lem!r}"
