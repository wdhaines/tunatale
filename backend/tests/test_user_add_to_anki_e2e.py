"""
E2E test: Create SRS item via API → sync to Anki → assert note has Note=source_sentence.

Uses an in-memory SQLite DB for SRS and a FakeWriter to capture the
note fields that would be sent to Anki, so no real Anki instance is required.
"""

from __future__ import annotations

import pytest

from app.anki.sync import AnkiSync
from app.models.syntactic_unit import SyntacticUnit
from app.srs.database import SRSDatabase


def _make_db() -> SRSDatabase:
    return SRSDatabase(":memory:")


def _add_item_with_source(
    db: SRSDatabase, text: str, translation: str, source_sentence: str, lesson_id: str = "lesson-1", line_index: int = 0
) -> str:
    unit = SyntacticUnit(
        text=text,
        translation=translation,
        word_count=1,
        difficulty=1,
        source="user",
        source_sentence=source_sentence,
        source_lesson_id=lesson_id,
        source_line_index=line_index,
    )
    db.add_collocation(unit)
    return db.get_collocation(text).guid


class FakeWriter:
    """Tracks calls for sync_create_new assertions."""

    def __init__(
        self,
        new_note_id: int = 5001,
        cards_by_ord: dict[int, int] | None = None,
    ) -> None:
        self.calls: list[tuple] = []
        self.created_notes: list[tuple[str, str, dict, list[str]]] = []
        self._new_note_id = new_note_id
        self._cards_by_ord = cards_by_ord if cards_by_ord is not None else {0: 50010, 1: 50011}

    def get_sort_field_name(self, model_name: str) -> str:
        return "Slovene"

    def create_note(self, deck_name: str, model_name: str, fields: dict, tags: list, language_code: str = "sl") -> int:
        self.calls.append(("create_note", deck_name, model_name, dict(fields), list(tags)))
        self.created_notes.append((deck_name, model_name, dict(fields), list(tags)))
        return self._new_note_id

    def store_media_file(self, filename: str, data: bytes) -> None:
        self.calls.append(("store_media_file", filename, len(data)))

    def get_cards_for_note(self, note_id: int) -> dict[int, int]:
        self.calls.append(("get_cards_for_note", note_id))
        return self._cards_by_ord


class FakeReader:
    def get_note_records(self):
        return []

    def get_revlog_for_card(self, card_id: int, after_ms: int = 0) -> list:
        return []


@pytest.mark.asyncio
async def test_user_add_to_anki_e2e():
    """
    Simulate the full flow:
    1. User creates an SRS item with source_sentence via API (simulated)
    2. sync_create_new() is called
    3. Assert the created Anki note has Note=source_sentence
    """
    db = _make_db()
    _add_item_with_source(
        db, "kavo", "coffee", source_sentence="Kavo prosim. Jaz bi rad kavo.", lesson_id="lesson-42", line_index=3
    )

    writer = FakeWriter()
    result = await AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_create_new(
        deck_name="0. Slovene", model_name="Slovene Vocabulary"
    )

    # Assert sync stats
    assert result.count == 1
    assert result.created == 1

    # Assert the note was created with correct fields
    assert len(writer.created_notes) == 1
    deck, model, fields, tags = writer.created_notes[0]

    assert deck == "0. Slovene"
    assert model == "Slovene Vocabulary"
    assert fields["Slovene"] == "kavo"
    assert fields["English"] == "coffee"
    assert fields["Note"] == "Kavo prosim. Jaz bi rad kavo."
    assert "tunatale" in tags

    # Lesson link is preserved on the SRS row (used for future deep-linking).
    stored = db.get_collocation("kavo")
    assert stored.syntactic_unit.source_lesson_id == "lesson-42"
    assert stored.syntactic_unit.source_line_index == 3


@pytest.mark.asyncio
async def test_user_add_to_anki_e2e_without_source_sentence():
    """Item without source_sentence should have empty Note field."""
    db = _make_db()
    unit = SyntacticUnit(
        text="voda",
        translation="water",
        word_count=1,
        difficulty=1,
        source="corpus",
        # No source_sentence
    )
    db.add_collocation(unit)

    writer = FakeWriter()
    result = await AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_create_new(
        deck_name="0. Slovene", model_name="Slovene Vocabulary"
    )

    assert result.created == 1
    assert len(writer.created_notes) == 1
    _, _, fields, _ = writer.created_notes[0]
    assert fields["Note"] == ""


@pytest.mark.asyncio
async def test_user_add_to_anki_e2e_with_anki_media():
    """Item with source_sentence + media should include it in Note field."""
    db = _make_db()
    _add_item_with_source(
        db, "dober dan", "good day", source_sentence="Dober dan! Kako si?", lesson_id="lesson-7", line_index=0
    )

    # Simulate media generation
    async def mock_media(word, english, *, used_image_urls=None, **_kwargs):
        from app.anki.media.pipeline import MediaResult

        return MediaResult(
            audio_bytes=b"fake_audio",
            audio_source="tts",
            image_bytes=b"fake_image",
            image_ext="jpg",
        )

    writer = FakeWriter()
    result = await AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_create_new(
        deck_name="0. Slovene", model_name="Slovene Vocabulary", _media_fn=mock_media
    )

    assert result.created == 1
    assert len(writer.created_notes) == 1
    _, _, fields, _ = writer.created_notes[0]

    # Note field should contain source_sentence
    assert fields["Note"] == "Dober dan! Kako si?"
    # Audio and Image should be present
    assert "[sound:" in fields["Audio"]
    assert '<img src="' in fields["Image"]
