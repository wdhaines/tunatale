"""Tests for S3.9: sync_create_new (addNote + media)."""

from __future__ import annotations

import json

import httpx

from app.anki.anki_connect import AnkiConnectClient
from app.anki.media.pipeline import MediaResult
from app.anki.sync import (
    AnkiSync,
    _safe_stem,
)
from app.models.srs_item import Direction
from app.models.syntactic_unit import SyntacticUnit
from app.srs.database import SRSDatabase

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_db() -> SRSDatabase:
    return SRSDatabase(":memory:")


def _add_item(db: SRSDatabase, text: str, translation: str) -> str:
    """Add a collocation with no Anki IDs. Returns guid."""
    unit = SyntacticUnit(text=text, translation=translation, word_count=1, difficulty=1, source="corpus")
    db.add_collocation(unit)
    return db.get_collocation(text).guid


def _add_item_with_anki_ids(db: SRSDatabase, text: str, translation: str, note_id: int = 9001) -> str:
    """Add a collocation WITH an Anki note_id already set."""
    guid = _add_item(db, text, translation)
    db.set_anki_ids(guid, note_id, {Direction.RECOGNITION: note_id * 10})
    return guid


class FakeReader:
    def get_note_records(self):
        return []


class FakeCreateWriter:
    """Tracks calls for sync_create_new assertions."""

    def __init__(
        self,
        new_note_id: int = 5001,
        cards_by_ord: dict[int, int] | None = None,
    ) -> None:
        self.calls: list[tuple] = []
        self._new_note_id = new_note_id
        self._cards_by_ord = cards_by_ord if cards_by_ord is not None else {0: 50010, 1: 50011}

    def create_note(self, deck_name: str, model_name: str, fields: dict, tags: list) -> int:
        self.calls.append(("create_note", deck_name, model_name, dict(fields), list(tags)))
        return self._new_note_id

    def store_media_file(self, filename: str, data: bytes) -> None:
        self.calls.append(("store_media_file", filename, len(data)))

    def get_cards_for_note(self, note_id: int) -> dict[int, int]:
        self.calls.append(("get_cards_for_note", note_id))
        return self._cards_by_ord

    # Stubs for the push path (not used in create_new tests)
    def update_note_fields(self, note_id, fields):
        pass

    def suspend(self, card_ids):
        pass

    def unsuspend(self, card_ids):
        pass

    def set_due_date(self, card_ids, days):
        pass

    def write_revlog(self, **kw):
        pass

    def set_specific_value_of_card(self, card_id, keys, new_values):
        pass

    def action_names(self) -> list[str]:
        return [c[0] for c in self.calls]


async def _no_media(word: str, english: str, *, used_image_urls: set[str]) -> MediaResult | None:
    return None


async def _forvo_media(word: str, english: str, *, used_image_urls: set[str]) -> MediaResult | None:
    return MediaResult(audio_bytes=b"mp3_data", audio_source="forvo")


async def _tts_media(word: str, english: str, *, used_image_urls: set[str]) -> MediaResult | None:
    return MediaResult(
        audio_bytes=b"tts_data",
        audio_source="tts",
        image_bytes=b"img_data",
        image_ext="jpg",
    )


async def _full_media(word: str, english: str, *, used_image_urls: set[str]) -> MediaResult | None:
    url = f"https://cdn.pixabay.com/{english}.jpg"
    used_image_urls.add(url)
    return MediaResult(
        audio_bytes=b"mp3_data",
        audio_source="forvo",
        image_bytes=b"img_data",
        image_ext="jpg",
        image_url=url,
    )


class FlexTransport(httpx.BaseTransport):
    """Returns per-action results."""

    def __init__(self, results: dict) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._results = results

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        action = body["action"]
        params = body.get("params", {})
        self.calls.append((action, params))
        result = self._results.get(action, None)
        return httpx.Response(200, json={"result": result, "error": None})


def _flex_client(results: dict) -> tuple[AnkiConnectClient, FlexTransport]:
    transport = FlexTransport(results)
    client = AnkiConnectClient(http_client=httpx.Client(transport=transport))
    return client, transport


# ── TestListItemsWithoutAnkiNote ──────────────────────────────────────────────


class TestListItemsWithoutAnkiNote:
    def test_returns_item_without_anki_note(self):
        db = _make_db()
        guid = _add_item(db, "voda", "water")
        rows = db.list_items_without_anki_note()
        assert len(rows) == 1
        assert rows[0][0] == guid
        assert rows[0][1].syntactic_unit.text == "voda"

    def test_excludes_item_with_anki_note(self):
        db = _make_db()
        _add_item_with_anki_ids(db, "voda", "water")
        assert db.list_items_without_anki_note() == []

    def test_returns_empty_when_db_empty(self):
        db = _make_db()
        assert db.list_items_without_anki_note() == []

    def test_returns_only_items_without_note(self):
        db = _make_db()
        _add_item_with_anki_ids(db, "voda", "water")
        guid2 = _add_item(db, "miza", "table")
        rows = db.list_items_without_anki_note()
        assert len(rows) == 1
        assert rows[0][0] == guid2


# ── TestSafeStem ──────────────────────────────────────────────────────────────


class TestSafeStem:
    def test_basic_ascii(self):
        assert _safe_stem("voda", "sl") == "sl_voda"

    def test_spaces_become_underscores(self):
        assert _safe_stem("letni čas", "sl") == "sl_letni_čas"

    def test_strips_special_chars(self):
        assert _safe_stem("hello!", "tts") == "tts_hello"

    def test_prefix_applied(self):
        assert _safe_stem("table", "img").startswith("img_")


# ── TestSyncCreateNew ─────────────────────────────────────────────────────────


class TestSyncCreateNew:
    async def test_creates_note_for_item_without_anki_id(self):
        db = _make_db()
        _add_item(db, "voda", "water")
        writer = FakeCreateWriter()
        report = await AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary"
        )
        assert report.count == 1
        assert "create_note" in writer.action_names()

    async def test_skips_item_with_existing_anki_id(self):
        db = _make_db()
        _add_item_with_anki_ids(db, "voda", "water")
        writer = FakeCreateWriter()
        report = await AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary"
        )
        assert report.count == 0
        assert "create_note" not in writer.action_names()

    async def test_returns_zero_when_no_new_items(self):
        db = _make_db()
        writer = FakeCreateWriter()
        report = await AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary"
        )
        assert report.count == 0

    async def test_dry_run_counts_but_does_not_write(self):
        db = _make_db()
        _add_item(db, "voda", "water")
        writer = FakeCreateWriter()
        report = await AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary", dry_run=True
        )
        assert report.count == 1
        assert "create_note" not in writer.action_names()
        # DB not updated
        assert db.list_items_without_anki_note()[0][0] is not None

    async def test_no_media_fn_creates_note_with_empty_media_fields(self):
        db = _make_db()
        _add_item(db, "voda", "water")
        writer = FakeCreateWriter()
        await AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary", _media_fn=None
        )
        call = next(c for c in writer.calls if c[0] == "create_note")
        fields = call[3]
        assert fields["Audio"] == ""
        assert fields["Image"] == ""

    async def test_forvo_audio_uses_sl_prefix(self):
        db = _make_db()
        _add_item(db, "voda", "water")
        writer = FakeCreateWriter()
        await AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary", _media_fn=_forvo_media
        )
        stored = [c for c in writer.calls if c[0] == "store_media_file"]
        assert len(stored) == 1
        assert stored[0][1].startswith("sl_")

    async def test_tts_audio_uses_tts_prefix(self):
        db = _make_db()
        _add_item(db, "voda", "water")
        writer = FakeCreateWriter()
        await AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary", _media_fn=_tts_media
        )
        stored = [c for c in writer.calls if c[0] == "store_media_file"]
        assert len(stored) == 2  # audio + image
        audio_files = [s for s in stored if s[1].startswith("tts_")]
        assert len(audio_files) == 1
        assert audio_files[0][1] == "tts_voda.mp3"

    async def test_audio_field_contains_sound_tag(self):
        db = _make_db()
        _add_item(db, "voda", "water")
        writer = FakeCreateWriter()
        await AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary", _media_fn=_forvo_media
        )
        call = next(c for c in writer.calls if c[0] == "create_note")
        assert "[sound:" in call[3]["Audio"]

    async def test_image_field_contains_img_tag(self):
        db = _make_db()
        _add_item(db, "voda", "water")
        writer = FakeCreateWriter()
        await AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary", _media_fn=_tts_media
        )
        call = next(c for c in writer.calls if c[0] == "create_note")
        assert '<img src="' in call[3]["Image"]

    async def test_source_sentence_written_to_note_field(self):
        """Item with source_sentence should have it in the Note field."""
        db = _make_db()
        # Add item with source context
        unit = SyntacticUnit(
            text="kako si",
            translation="how are you",
            word_count=2,
            difficulty=1,
            source="user",
            source_sentence="Kako si? Jaz sem dobro.",
        )
        db.add_collocation(unit)
        _ = db.get_collocation("kako si").guid  # Ensure item is created

        writer = FakeCreateWriter()
        await AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary"
        )
        call = next(c for c in writer.calls if c[0] == "create_note")
        fields = call[3]
        assert fields["Note"] == "Kako si? Jaz sem dobro."

    async def test_empty_source_sentence_gives_empty_note_field(self):
        """Item without source_sentence should have empty Note field."""
        db = _make_db()
        _add_item(db, "voda", "water")  # No source_sentence
        writer = FakeCreateWriter()
        await AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary"
        )
        call = next(c for c in writer.calls if c[0] == "create_note")
        fields = call[3]
        assert fields["Note"] == ""

    async def test_image_stored_with_img_prefix(self):
        db = _make_db()
        _add_item(db, "voda", "water")
        writer = FakeCreateWriter()
        await AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary", _media_fn=_full_media
        )
        stored = [c for c in writer.calls if c[0] == "store_media_file"]
        filenames = [c[1] for c in stored]
        assert any(f.startswith("img_") for f in filenames)

    async def test_media_fn_returning_none_stores_no_media(self):
        db = _make_db()
        _add_item(db, "voda", "water")
        writer = FakeCreateWriter()
        await AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary", _media_fn=_no_media
        )
        assert "store_media_file" not in writer.action_names()

    async def test_updates_db_with_note_id(self):
        db = _make_db()
        _add_item(db, "voda", "water")
        writer = FakeCreateWriter(new_note_id=5001)
        await AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary"
        )
        item = db.get_collocation("voda")
        assert item.anki_note_id == 5001

    async def test_updates_db_with_card_ids(self):
        db = _make_db()
        _add_item(db, "voda", "water")
        writer = FakeCreateWriter(new_note_id=5001, cards_by_ord={0: 50010, 1: 50011})
        await AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary"
        )
        item = db.get_collocation("voda")
        rec = item.directions.get(Direction.RECOGNITION)
        prod = item.directions.get(Direction.PRODUCTION)
        assert rec is not None and rec.anki_card_id == 50010
        assert prod is not None and prod.anki_card_id == 50011

    async def test_handles_note_with_only_one_card(self):
        db = _make_db()
        _add_item(db, "voda", "water")
        writer = FakeCreateWriter(new_note_id=5001, cards_by_ord={0: 50010})
        await AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary"
        )
        item = db.get_collocation("voda")
        assert item.anki_note_id == 5001
        assert item.directions[Direction.RECOGNITION].anki_card_id == 50010

    async def test_handles_note_with_no_cards(self):
        db = _make_db()
        _add_item(db, "voda", "water")
        writer = FakeCreateWriter(new_note_id=5001, cards_by_ord={})
        await AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary"
        )
        # note_id still stored even if no cards
        item = db.get_collocation("voda")
        assert item.anki_note_id == 5001

    async def test_creates_multiple_notes(self):
        db = _make_db()
        _add_item(db, "voda", "water")
        _add_item(db, "miza", "table")
        writer = FakeCreateWriter()
        report = await AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary"
        )
        assert report.count == 2
        assert len([c for c in writer.calls if c[0] == "create_note"]) == 2

    async def test_deduplicates_images_via_used_image_urls(self):
        """used_image_urls accumulates across items so second item sees first URL."""
        db = _make_db()
        _add_item(db, "voda", "water")
        _add_item(db, "miza", "table")

        received_used_urls: list[frozenset] = []

        async def tracking_media(word, english, *, used_image_urls):
            received_used_urls.append(frozenset(used_image_urls))
            url = f"https://cdn.pixabay.com/{english}.jpg"
            used_image_urls.add(url)
            return MediaResult(audio_bytes=b"x", audio_source="forvo")

        writer = FakeCreateWriter()
        await AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene",
            model_name="Slovene Vocabulary",
            _media_fn=tracking_media,
        )
        # First item saw empty set; second item saw first URL
        assert received_used_urls[0] == frozenset()
        assert len(received_used_urls[1]) == 1

    async def test_note_fields_include_slovene_and_english(self):
        db = _make_db()
        _add_item(db, "voda", "water")
        writer = FakeCreateWriter()
        await AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary"
        )
        call = next(c for c in writer.calls if c[0] == "create_note")
        fields = call[3]
        assert fields["Slovene"] == "voda"
        assert fields["English"] == "water"

    async def test_note_has_tunatale_tag(self):
        db = _make_db()
        _add_item(db, "voda", "water")
        writer = FakeCreateWriter()
        await AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary"
        )
        call = next(c for c in writer.calls if c[0] == "create_note")
        assert "tunatale" in call[4]  # tags

    async def test_dry_run_does_not_update_db(self):
        db = _make_db()
        _add_item(db, "voda", "water")
        writer = FakeCreateWriter()
        await AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary", dry_run=True
        )
        item = db.get_collocation("voda")
        assert item.anki_note_id is None

    async def test_duplicate_note_error_links_offline(self):
        """DuplicateNoteError from OfflineWriter links without calling find_notes."""
        from app.anki.sync import DuplicateNoteError

        db = _make_db()
        _add_item(db, "voda", "water")

        class OfflineDupWriter(FakeCreateWriter):
            def create_note(self, deck, model, fields, tags):
                raise DuplicateNoteError(note_id=8888)

        writer = OfflineDupWriter()
        report = await AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary"
        )
        assert report.linked == 1
        assert report.created == 0
        assert db.get_collocation("voda").anki_note_id == 8888
        # find_notes must NOT be called (offline path knows the ID from the exception)
        assert not any(c[0] == "find_notes" for c in writer.calls)
