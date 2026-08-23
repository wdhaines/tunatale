"""An unglossed card must not reach Anki, and must keep trying to get a gloss.

Second half of the `ferskt` incident (bd `tunatale-1wiw`; the dedup half shipped
in fbaa725). cid 3054 had `translation = ''` AND `extras = ''` — nothing on its
back — yet it got an `anki_note_id`, reached the collection, and was failed 12
times.

Two mechanisms, and the split matters:

  * The **regloss** (``llm/translate.py::generate_word_gloss``, wired into
    ``_complete_listen_media``) is a REPAIR. It runs after the response, so the row already exists when it
    fires, and Groq's free tier can exhaust — TPD binds before RPM.
  * The **gate** here is what makes a failed repair survivable. A card with
    nothing on its back is held out of ``sync_create_new`` until it has
    something, so the worst case is "TT-only until glossed" rather than "empty
    card in Anki forever".

⚠️ The oracle is the SKIP, not the absence of such rows. There are zero
unglossed rows in the live Norwegian deck today (3054 was graved 2026-08-22), so
an existence assertion passes vacuously — assert that the push path declines to
mint, with the row sitting right there.
"""

from __future__ import annotations

from app.models.srs_item import Direction
from app.models.syntactic_unit import BackField, SyntacticUnit
from app.plugins.anki_sync.sync import AnkiSync, OfflineWriter
from app.srs.database import SRSDatabase
from tests._helpers.anki_sync_create_new import FakeReader, _make_dual_collection_conn  # noqa: F401


def _add(db, text: str, *, translation: str = "", extras: tuple[BackField, ...] = ()) -> None:
    db.add_collocation(
        SyntacticUnit(
            text=text,
            translation=translation,
            word_count=1,
            difficulty=1,
            source="llm",
            lemma=text,
            source_sentence=f"Ja, og skisporet var helt {text}.",
            extras=extras,
        )
    )


async def _run_create_new(db) -> None:
    writer = OfflineWriter(_make_dual_collection_conn())
    await AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_create_new(
        deck_name="0. Slovene", model_name="Slovene Vocabulary"
    )


class TestTheGate:
    async def test_a_card_with_nothing_on_its_back_is_not_minted(self):
        """The ferskt shape: empty translation AND empty extras."""
        db = SRSDatabase(":memory:")
        _add(db, "ferskt")

        await _run_create_new(db)

        assert db.get_collocation("ferskt").anki_note_id is None, "an unglossed card was pushed to Anki"

    async def test_a_glossed_card_beside_it_is_still_minted(self):
        """The gate must hold back one row, not stall the batch — a skipped item
        that aborted the loop would silently stop every later card reaching Anki."""
        db = SRSDatabase(":memory:")
        _add(db, "ferskt")
        _add(db, "skispor", translation="ski track")

        await _run_create_new(db)

        assert db.get_collocation("ferskt").anki_note_id is None
        assert db.get_collocation("skispor").anki_note_id is not None, "the gate stalled the rest of the batch"

    async def test_extras_alone_are_enough_to_be_worth_pushing(self):
        """An imported card can carry its meaning in extras (IPA / Meaning /
        Inflections) with the translation column empty — 'fersk' itself has 1549
        bytes of extras. That card teaches something; it must still be pushed."""
        db = SRSDatabase(":memory:")
        _add(db, "fersk", extras=(BackField(label="Meaning", html="Recently made or obtained.", tier="summary"),))

        await _run_create_new(db)

        assert db.get_collocation("fersk").anki_note_id is not None, "a card with extras was wrongly held back"

    async def test_a_cloze_card_is_never_held_back_for_having_no_gloss(self):
        """A cloze has no gloss BY DESIGN — its front is the sentence with a
        blank, its back the answer plus Back Extra, so `translation` is empty on
        every one. The first cut of this gate keyed on "has a back" and held the
        entire cloze pipeline; six pre-existing tests caught it."""
        db = SRSDatabase(":memory:")
        db.add_collocation(
            SyntacticUnit(
                text="ki",
                translation="",
                word_count=1,
                difficulty=1,
                source="cloze",
                lemma="ki",
                source_sentence="knjiga, ki je tam",
                card_type="cloze",
            )
        )

        await _run_create_new(db)

        assert db.get_collocation("ki").anki_note_id is not None, "the gate held back a cloze card"

    async def test_the_row_is_not_deleted_or_suspended_merely_for_being_unglossed(self):
        """Held back, not thrown away — the retry needs the row to still be there,
        and the learner still meets the word in TT."""
        db = SRSDatabase(":memory:")
        _add(db, "ferskt")

        await _run_create_new(db)

        item = db.get_collocation("ferskt")
        assert item is not None
        assert item.directions[Direction.RECOGNITION].state.value == "new"

    async def test_a_gloss_arriving_later_releases_the_card(self):
        """The whole point of holding rather than dropping: once the retry lands
        a gloss, the ordinary push path mints it with no special handling."""
        db = SRSDatabase(":memory:")
        _add(db, "ferskt")
        await _run_create_new(db)
        assert db.get_collocation("ferskt").anki_note_id is None

        db.set_translation_dirty(db.get_collocation("ferskt").guid, "fresh")
        await _run_create_new(db)

        assert db.get_collocation("ferskt").anki_note_id is not None, "a reglossed card never reached Anki"


class TestTheRetryIsMarkedForPush:
    async def test_a_backfilled_gloss_is_marked_dirty_so_sync_push_rewrites_the_note(self):
        """A card already in Anki that gets reglossed must push the new text —
        otherwise the empty back survives in the collection.

        `translation` is an existing dirty field (`sync_engine.py:888`), so this
        is the same mechanism the cloze sentence_translation backfill uses."""
        db = SRSDatabase(":memory:")
        _add(db, "ferskt")
        guid = db.get_collocation("ferskt").guid

        db.set_translation_dirty(guid, "fresh")

        assert db.get_collocation("ferskt").syntactic_unit.translation == "fresh"
        assert "translation" in db.get_dirty_fields(guid)

    async def test_backfilling_preserves_dirty_fields_already_queued(self):
        db = SRSDatabase(":memory:")
        _add(db, "ferskt")
        guid = db.get_collocation("ferskt").guid
        db.set_sentence_translation_dirty(guid, "Yes, and the ski track was completely fresh.")

        db.set_translation_dirty(guid, "fresh")

        assert set(db.get_dirty_fields(guid).split(",")) >= {"translation", "sentence_translation"}

    async def test_backfilling_an_unknown_guid_is_a_no_op(self):
        db = SRSDatabase(":memory:")
        db.set_translation_dirty("no-such-guid", "fresh")
