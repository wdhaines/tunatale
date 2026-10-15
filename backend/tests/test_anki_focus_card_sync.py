"""A focus card reaches Anki as one Cloze note: the English in bold, then the sentence as the blank (tunatale-ve4p.9).

TT stores the card as an ordinary cloze on the whole sentence. The bold exists
only in field 0 of the Anki note, written by ``contrast_card.cloze_note_text``
at both places the sync writes that field: the mint (``sync_create_new``) and a
sentence edit (``sync_push``). Bold reads in both Anki themes and on AnkiDroid,
so no template changes (design § 3 D3/D4).

The TTS network boundary is faked (``generate_tts_audio``); everything else is
the real path. Cebuano, because an affix pattern is a facet only its plugin
registers.
"""

from __future__ import annotations

import pytest

from app.common.guid import compute_guid
from app.languages import get_a1_morphology
from app.models.srs_item import Direction
from app.plugins.anki_sync.sync import AnkiSync, OfflineWriter
from app.srs.database import SRSDatabase
from app.srs.focus_card import focus_cards
from tests._helpers.anki_sync_create_new import FakeReader, _make_dual_collection_conn
from tests._helpers.anki_sync_push import FakeReader as PushReader
from tests._helpers.anki_sync_push import FakeWriter as PushWriter

_ON_GI = next(p for p in get_a1_morphology("ceb").patterns if p.key == "on-gi")
_DECK = "0. Slovene"
_GIINOM = "I drank <b>the water</b>.<br><br>{{c1::<b>Giinom</b> ko <b>ang tubig</b>.}}"
_MIINOM = "<b>He</b> drank coffee.<br><br>{{c1::<b>Miinom</b> <b>siya</b> og kape.}}"


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


def _add(db: SRSDatabase, root: str, k: int) -> str:
    unit = focus_cards("ceb", _ON_GI, root)[k].unit()
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
    async def test_the_note_front_is_the_english_in_bold_over_the_blank(self, tmp_path, spoken):
        db = SRSDatabase(":memory:")
        guid = _add(db, "inom", 0)

        anki_conn, report = await _mint(db, tmp_path)

        assert report.created == 1
        note = anki_conn.execute("SELECT id, mid, guid, flds FROM notes").fetchone()
        assert note["mid"] == 1000002  # the built-in Cloze notetype
        front, back = note["flds"].split("\x1f")
        assert front == _GIINOM
        assert note["guid"] == compute_guid(_GIINOM, "ceb", "")
        assert "<i>I drank the water.</i>" in back
        assert '<span class="grammar">inom · drink</span>' in back
        assert '<span class="st">' not in back  # the English is the front; not again on the back

        cards = anki_conn.execute("SELECT id FROM cards WHERE nid = ?", (note["id"],)).fetchall()
        assert len(cards) == 1
        assert set(db.get_collocation_by_guid(guid).directions) == {Direction.PRODUCTION}
        assert db.get_collocation_by_anki_note_id(note["id"]).guid == guid

    async def test_the_audio_is_the_cebuano_sentence_never_the_front(self, tmp_path, spoken):
        db = SRSDatabase(":memory:")
        _add(db, "inom", 0)
        _add(db, "inom", 1)

        anki_conn, _ = await _mint(db, tmp_path)

        assert sorted(set(spoken)) == ["Giinom ko ang tubig.", "Miinom siya og kape."]
        fronts = {row["flds"].split("\x1f")[0] for row in anki_conn.execute("SELECT flds FROM notes")}
        assert fronts == {_GIINOM, _MIINOM}

    async def test_a_second_sync_mints_nothing_more(self, tmp_path, spoken):
        db = SRSDatabase(":memory:")
        _add(db, "kaon", 0)
        anki_conn = _make_dual_collection_conn()
        sync = AnkiSync(
            db=db, _reader=FakeReader(), _writer=OfflineWriter(anki_conn, media_dir=tmp_path), language_code="ceb"
        )

        await sync.sync_create_new(deck_name=_DECK, model_name="Slovene Vocabulary")
        again = await sync.sync_create_new(deck_name=_DECK, model_name="Slovene Vocabulary")

        assert again.created == 0
        assert anki_conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 1


class TestSentenceEdit:
    def test_a_pushed_sentence_keeps_the_bold_front(self):
        """``sync_push`` would otherwise send ``source_sentence`` alone and drop the English."""
        db = SRSDatabase(":memory:")
        guid = _add(db, "inom", 0)
        db.set_anki_ids(guid, 111, {Direction.PRODUCTION: 222})
        db.set_cloze_sentence(db.get_collocation_id_by_guid(guid), "{{c1::Giinom ko ang tubig.}}", "")

        writer = PushWriter()
        AnkiSync(db=db, _reader=PushReader(), _writer=writer, language_code="ceb").sync_push()

        assert [call[2] for call in writer.calls if call[0] == "update_cloze_text"] == [_GIINOM]
