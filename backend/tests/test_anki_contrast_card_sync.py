"""A contrast card reaches Anki as one Cloze note whose front is the grid (tunatale-ve4p.6).

TT stores the card as an ordinary cloze: the form, the root, and a sentence
with the form blanked. The grid exists only in field 0 of the Anki note, and it
is written by ``contrast_card.cloze_note_text`` at both places the sync writes
that field: the mint (``sync_create_new``) and a sentence edit (``sync_push``).
These tests are the two call sites; ``test_contrast_card.py`` is the function.

Cebuano, because an affix pattern is a facet only its plugin registers. The
collection is the shared synthetic one, whose deck happens to be named for
Slovene; a cloze mint needs only a deck and the built-in Cloze notetype.

The TTS network boundary is faked (``generate_tts_audio``, the seam
``TestSyncCreateNewClozeAudioMint`` uses); everything else is the real path.
"""

from __future__ import annotations

import pytest

from app.common.guid import compute_guid
from app.languages import get_a1_morphology
from app.models.srs_item import Direction
from app.models.syntactic_unit import SyntacticUnit
from app.plugins.anki_sync.sync import AnkiSync, OfflineWriter
from app.srs.contrast_card import contrast_card
from app.srs.database import SRSDatabase
from tests._helpers.anki_sync_create_new import FakeReader, _make_dual_collection_conn
from tests._helpers.anki_sync_push import FakeReader as PushReader
from tests._helpers.anki_sync_push import FakeWriter as PushWriter

_MO_MI = next(p for p in get_a1_morphology("ceb").patterns if p.key == "mo-mi")
_DECK = "0. Slovene"
_GRID = "lakaw · walk<br>will walk: molakaw<br>walked: {{c1::milakaw}}"
_UBAN_GRID = "uban · accompany<br>will go along: {{c1::mouban}}<br>went along: miuban"


@pytest.fixture
def spoken(monkeypatch) -> list[str]:
    """Every text handed to the TTS vendor during the test."""
    import app.audio.cloze_tts as cloze_tts_mod
    import app.plugins.anki_sync.sync as sync_mod

    said: list[str] = []

    async def _tts(text, voice=None):
        said.append(text)
        return b"fake-mp3"

    monkeypatch.setattr(cloze_tts_mod, "generate_tts_audio", _tts)
    monkeypatch.setattr(cloze_tts_mod, "_MEDIA_DIR", sync_mod._MEDIA_DIR)
    return said


def _add(db: SRSDatabase, root: str, blank: int, line: tuple[str, str] | None = None) -> str:
    unit = contrast_card("ceb", _MO_MI, root, blank, line=line).unit()
    db.add_collocation(unit, language_code="ceb")
    return compute_guid(unit.text, "ceb", unit.disambig_key)


async def _mint(db: SRSDatabase, tmp_path):
    anki_conn = _make_dual_collection_conn()
    writer = OfflineWriter(anki_conn, media_dir=tmp_path)
    report = await AnkiSync(db=db, _reader=FakeReader(), _writer=writer, language_code="ceb").sync_create_new(
        deck_name=_DECK, model_name="Slovene Vocabulary"
    )
    return anki_conn, report


class TestMint:
    async def test_the_note_front_is_the_grid(self, tmp_path, spoken):
        db = SRSDatabase(":memory:")
        guid = _add(db, "lakaw", 1)

        anki_conn, report = await _mint(db, tmp_path)

        assert report.created == 1
        note = anki_conn.execute("SELECT id, mid, guid, flds, sfld FROM notes").fetchone()
        assert note["mid"] == 1000002  # the built-in Cloze notetype
        front, back = note["flds"].split("\x1f")
        assert front == _GRID == note["sfld"]
        # Anki's note guid is derived from field 0, as for every cloze.
        assert note["guid"] == compute_guid(_GRID, "ceb", "")
        assert "<i>walked</i>" in back
        assert '<span class="grammar">lakaw · walk</span>' in back

        # One note, one card, linked to the TT row as its PRODUCTION direction.
        cards = anki_conn.execute("SELECT id FROM cards WHERE nid = ?", (note["id"],)).fetchall()
        assert len(cards) == 1
        item = db.get_collocation_by_guid(guid)
        assert set(item.directions) == {Direction.PRODUCTION}
        assert db.get_collocation_by_anki_note_id(note["id"]).guid == guid

    async def test_a_lesson_line_follows_the_grid(self, tmp_path, spoken):
        db = SRSDatabase(":memory:")
        _add(db, "uban", 0, line=("Mouban ko ugma.", "I will go along tomorrow."))

        anki_conn, _ = await _mint(db, tmp_path)

        front, back = anki_conn.execute("SELECT flds FROM notes").fetchone()["flds"].split("\x1f")
        assert front == f"{_UBAN_GRID}<br><br>{{{{c1::Mouban}}}} ko ugma."
        assert '<span class="st">I will go along tomorrow.</span>' in back

    async def test_the_audio_is_the_form_or_the_line_never_the_grid(self, tmp_path, spoken):
        """The mint voices a soundless cloze. Handed field 0 it would read a table aloud."""
        db = SRSDatabase(":memory:")
        _add(db, "lakaw", 1)
        _add(db, "uban", 0, line=("Mouban ko ugma.", "I will go along tomorrow."))

        anki_conn, _ = await _mint(db, tmp_path)

        assert sorted(set(spoken)) == ["Mouban ko ugma.", "milakaw", "mouban"]
        assert all("[sound:tts_sentence_" in row["flds"] for row in anki_conn.execute("SELECT flds FROM notes"))

    async def test_a_second_sync_mints_nothing_more(self, tmp_path, spoken):
        db = SRSDatabase(":memory:")
        _add(db, "lakaw", 1)
        anki_conn = _make_dual_collection_conn()
        sync = AnkiSync(
            db=db, _reader=FakeReader(), _writer=OfflineWriter(anki_conn, media_dir=tmp_path), language_code="ceb"
        )

        await sync.sync_create_new(deck_name=_DECK, model_name="Slovene Vocabulary")
        again = await sync.sync_create_new(deck_name=_DECK, model_name="Slovene Vocabulary")

        assert again.created == 0
        assert anki_conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 1

    async def test_both_cards_of_a_root_are_two_notes(self, tmp_path, spoken):
        db = SRSDatabase(":memory:")
        _add(db, "lakaw", 0)
        _add(db, "lakaw", 1)

        anki_conn, report = await _mint(db, tmp_path)

        assert report.created == 2
        fronts = {row["flds"].split("\x1f")[0] for row in anki_conn.execute("SELECT flds FROM notes")}
        assert fronts == {_GRID, "lakaw · walk<br>will walk: {{c1::molakaw}}<br>walked: milakaw"}


class TestSentenceEdit:
    def test_a_rewritten_line_is_pushed_under_the_grid(self):
        """``sync_push`` used to send ``source_sentence`` as field 0, which would drop the grid."""
        db = SRSDatabase(":memory:")
        guid = _add(db, "uban", 0, line=("Mouban ko ugma.", "I will go along tomorrow."))
        db.set_anki_ids(guid, 111, {Direction.PRODUCTION: 222})
        db.set_cloze_sentence(
            db.get_collocation_id_by_guid(guid), "{{c1::Mouban}} sila karon.", "They will go along now."
        )

        writer = PushWriter()
        AnkiSync(db=db, _reader=PushReader(), _writer=writer, language_code="ceb").sync_push()

        texts = [call[2] for call in writer.calls if call[0] == "update_cloze_text"]
        assert texts == [f"{_UBAN_GRID}<br><br>{{{{c1::Mouban}}}} sila karon."]

    def test_any_other_cloze_is_pushed_exactly_as_stored(self):
        """The guard on the shared call site: nothing about an ordinary cloze's push moved.

        The stored sentence goes out verbatim, unmarked here on purpose. Marking
        the blank is the job of whoever stores the sentence; a push that started
        wrapping it would be a second writer of the same rule.
        """
        db = SRSDatabase(":memory:")
        unit = SyntacticUnit(
            text="sa",
            translation="to",
            word_count=1,
            difficulty=1,
            source="test",
            lemma="sa",
            card_type="cloze",
            source_sentence="Moadto ko {{c1::sa}} merkado.",
        )
        db.add_collocation(unit, language_code="ceb")
        guid = compute_guid("sa", "ceb", "")
        db.set_anki_ids(guid, 111, {Direction.PRODUCTION: 222})
        db.set_cloze_sentence(db.get_collocation_id_by_guid(guid), "Mouli ko sa balay.", "I will go home.")

        writer = PushWriter()
        AnkiSync(db=db, _reader=PushReader(), _writer=writer, language_code="ceb").sync_push()

        assert [call[2] for call in writer.calls if call[0] == "update_cloze_text"] == ["Mouli ko sa balay."]
