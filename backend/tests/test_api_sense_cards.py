"""POST /api/srs/items/sense — a second card for a spelling's other meaning.

bd tunatale-ceuc, slice 4. Norwegian ``gang`` is one card, "hall", and every
lesson line that uses it means "time". Slice 1 shows the lesson's gloss over the
card's translation and slice 3 lets the gloss pick among a spelling's cards, so
what is left is making the second card: the reader's "different meaning" action.

The contract, which is the card-adding one (``.claude/rules/anki-sync.md``) plus
what a SENSE card needs on top:

- it is a vocab card with the first card's spelling and lemma, told apart by a
  key made from the meaning — the convention the Slovene deck already uses
  (``barva`` keyed "color" / "paint");
- after it exists, the gloss it was made from resolves to it
  (``transcript.py::choose_lemma_card``), which is the reason to make it;
- a meaning one of the spelling's cards already has makes nothing.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.models import CreateCardResponse
from app.common.guid import compute_guid
from app.main import app
from app.models.srs_item import Direction, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.sense_match import names_same_sense, sense_label
from app.srs.transcript import choose_lemma_card
from tests._helpers.lemmatizer import StubLemmatizer
from tests._helpers.srs_item_shape import SRS_ITEM_KEYS

LANG = "no"


def _seed(db, text: str, translation: str, key: str, *, lang: str = LANG, card_type: str = "vocab", **unit) -> int:
    db.add_collocation(
        SyntacticUnit(
            text=text,
            translation=translation,
            word_count=unit.pop("word_count", 1),
            difficulty=1,
            source="anki",
            lemma=unit.pop("lemma", text),
            disambig_key=key,
            card_type=card_type,
            source_sentence="En {{c1::gang}} til" if card_type == "cloze" else "",
            **unit,
        ),
        language_code=lang,
    )
    return db.get_collocation_id_by_guid(compute_guid(text, lang, key))


def _rows(db, text: str) -> list[tuple[str, str]]:
    with db._get_conn() as conn:
        return [
            (r["disambig_key"], r["translation"])
            for r in conn.execute(
                "SELECT disambig_key, translation FROM collocations WHERE text = ? ORDER BY id", (text,)
            )
        ]


async def _post(**body) -> object:
    payload = {"surface": "gang", "sentence": "Jeg kommer med en gang.", "language_code": LANG, **body}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post("/api/srs/items/sense", json=payload)


@pytest.fixture
def lemmatizer(monkeypatch: pytest.MonkeyPatch) -> StubLemmatizer:
    """No tagger: an unregistered word has no word class, so nothing is re-glossed."""
    import app.api.srs as srs_mod

    stub = StubLemmatizer()
    monkeypatch.setattr(srs_mod, "get_lemmatizer", lambda code: stub)
    return stub


class TestSenseLabel:
    @pytest.mark.parametrize(
        ("gloss", "label"),
        [
            ("time", "time"),
            ("  The Time ", "time"),
            ("a hall", "hall"),
            ("an hour", "hour"),
            ("to wonder", "wonder"),
            ("one  more   time", "one more time"),
            ("time, occasion", "time, occasion"),
            # A gloss's own aside is not part of the meaning's name.
            ("the time (den gangen = back then)", "time"),
            # ...unless it is all there is.
            ("(formal)", "(formal)"),
            ("Sunday", "sunday"),
        ],
    )
    def test_names_the_meaning(self, gloss, label):
        assert sense_label(gloss) == label

    def test_is_empty_for_a_gloss_that_names_nothing(self):
        assert sense_label("  ") == ""
        assert sense_label("the") == ""


class TestNamesSameSense:
    """ "Does a card already have this meaning?" — where a yes makes no card."""

    @pytest.mark.parametrize(
        ("gloss", "translation", "same"),
        [
            ("times", "time, occasion", True),
            ("the hall", "hall", True),
            ("time", "hall", False),
            # A shared grammar word is not a shared meaning...
            ("to wonder", "to trick", False),
            # ...unless grammar words are all the gloss has (`vår`, live deck).
            ("our", "our", True),
            ("our", "spring", False),
            ("wonders", "", False),
        ],
    )
    def test_a_shared_content_word_or_an_all_grammar_match(self, gloss, translation, same):
        assert names_same_sense(gloss, translation) is same


@pytest.mark.usefixtures("lemmatizer")
class TestCreateSenseCard:
    async def test_mints_a_second_card_for_the_other_meaning(self, api_app_state):
        hall = _seed(api_app_state, "gang", "hall", "noun", article="en")

        resp = await _post(item_id=hall, translation="time")

        assert resp.status_code == 200
        data = resp.json()
        assert data["was_created"] is True
        assert data["id"] != hall
        assert _rows(api_app_state, "gang") == [("noun", "hall"), ("time", "time")]

        new = api_app_state.get_collocation_by_guid(compute_guid("gang", LANG, "time"))
        unit = new.syntactic_unit
        assert (unit.text, unit.lemma, unit.card_type, unit.word_count) == ("gang", "gang", "vocab", 1)
        # The first card's own facts, copied: the same word has the same gender.
        assert unit.article == "en"
        assert unit.source == "user"
        # The line the meaning was met in, so the card can say where it came from.
        assert unit.source_sentence == "Jeg kommer med en gang."
        # The card-adding contract: NEW, both directions, nothing of Anki's yet.
        assert new.anki_note_id is None
        assert new.directions[Direction.RECOGNITION].state == SRSState.NEW
        assert new.directions[Direction.PRODUCTION].state == SRSState.NEW
        assert new.directions[Direction.RECOGNITION].anki_card_id is None

    async def test_the_gloss_it_was_made_from_now_resolves_to_it(self, api_app_state):
        """The reason the card is made: before, "time" could only land on "hall"."""
        hall = _seed(api_app_state, "gang", "hall", "noun")
        assert choose_lemma_card(api_app_state, "gang", "NOUN", "time").card[0] == hall

        time = (await _post(item_id=hall, translation="time")).json()["id"]

        picked = choose_lemma_card(api_app_state, "gang", "NOUN", "time")
        assert (picked.card[0], picked.undecided) == (time, False)
        picked = choose_lemma_card(api_app_state, "gang", "NOUN", "the hall")
        assert (picked.card[0], picked.undecided) == (hall, False)

    async def test_asking_twice_makes_one_card(self, api_app_state):
        hall = _seed(api_app_state, "gang", "hall", "noun")

        first = await _post(item_id=hall, translation="time")
        second = await _post(item_id=hall, translation="time")

        assert (first.json()["was_created"], second.json()["was_created"]) == (True, False)
        assert first.json()["id"] == second.json()["id"]
        assert _rows(api_app_state, "gang") == [("noun", "hall"), ("time", "time")]

    async def test_a_meaning_another_card_already_has_returns_that_card(self, api_app_state):
        """ "times" on a later line is the "time" card's meaning, not a third card."""
        hall = _seed(api_app_state, "gang", "hall", "noun")
        time = _seed(api_app_state, "gang", "time, occasion", "time")

        resp = await _post(item_id=hall, translation="times")

        assert resp.status_code == 200
        assert (resp.json()["id"], resp.json()["was_created"]) == (time, False)
        assert len(_rows(api_app_state, "gang")) == 2

    async def test_the_cards_own_meaning_makes_nothing(self, api_app_state):
        """ "the hall" differs from "hall" as a string, and names the same sense."""
        hall = _seed(api_app_state, "gang", "hall", "noun")

        resp = await _post(item_id=hall, translation="the hall")

        assert (resp.json()["id"], resp.json()["was_created"]) == (hall, False)
        assert _rows(api_app_state, "gang") == [("noun", "hall")]

    async def test_a_meaning_named_like_a_word_class_cannot_take_the_first_cards_key(self, api_app_state):
        """UNIQUE(text, disambig_key): a bare "noun" key IS the first card's, so
        the add would hand back the "hall" card and report nothing wrong — and a
        key that reads as a word class would be resolved as one."""
        hall = _seed(api_app_state, "gang", "hall", "noun")

        resp = await _post(item_id=hall, translation="noun")

        assert resp.json()["was_created"] is True
        assert resp.json()["id"] != hall
        assert _rows(api_app_state, "gang") == [("noun", "hall"), ("sense:noun", "noun")]

    async def test_an_unknown_card_is_not_found(self, api_app_state):
        assert (await _post(item_id=999, translation="time")).status_code == 404

    async def test_a_cloze_has_no_second_sense_card(self, api_app_state):
        """Only vocab cards compete for a spelling (``choose_lemma_card``); a
        sense card beside a cloze could never be resolved to."""
        cloze = _seed(api_app_state, "gang", "time", "", card_type="cloze")

        resp = await _post(item_id=cloze, translation="hall")

        assert resp.status_code == 409
        assert len(_rows(api_app_state, "gang")) == 1

    async def test_a_phrase_has_no_second_sense_card(self, api_app_state):
        """A phrase card is found by its span and carries no lemma, so nothing
        could ever choose between it and a second one."""
        phrase = _seed(api_app_state, "med en gang", "right away", "", lemma=None, word_count=3)

        resp = await _post(item_id=phrase, surface="gang", translation="at once")

        assert resp.status_code == 409
        assert len(_rows(api_app_state, "med en gang")) == 1

    async def test_a_cloze_of_the_same_word_is_not_a_meaning_it_has(self, api_app_state):
        """A cloze shares the lemma and is not a sense candidate: its English
        must not stand in for a vocab card's."""
        hall = _seed(api_app_state, "gang", "hall", "noun")
        _seed(api_app_state, "gang", "time", "sense:x", card_type="cloze")

        resp = await _post(item_id=hall, translation="time")

        assert resp.json()["was_created"] is True
        assert ("time", "time") in _rows(api_app_state, "gang")

    @pytest.mark.parametrize("gloss", ["", "   ", "the"])
    async def test_a_meaning_with_no_name_is_refused(self, api_app_state, gloss):
        hall = _seed(api_app_state, "gang", "hall", "noun")

        resp = await _post(item_id=hall, translation=gloss)

        assert resp.status_code == 422
        assert _rows(api_app_state, "gang") == [("noun", "hall")]

    async def test_response_keys_match_model(self, api_app_state):
        hall = _seed(api_app_state, "gang", "hall", "noun")

        data = (await _post(item_id=hall, translation="time")).json()

        assert set(data.keys()) == {"id", "was_created", "item"} == set(CreateCardResponse.model_fields)
        assert set(data["item"].keys()) == SRS_ITEM_KEYS
        assert data["item"]["translation"] == "time"

    async def test_surfaces_same_day_in_review_queue(self, api_app_state):
        hall = _seed(api_app_state, "gang", "hall", "noun")
        await _post(item_id=hall, translation="time")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            queue = (await client.get("/api/srs/review-queue")).json()["queue"]

        assert [q["state"] for q in queue if q["text"] == "gang" and q["translation"] == "time"][:1] == ["new"]


class TestAVerbsMeaningIsItsDictionaryForm:
    """The lesson glosses the form it met ("wonders"); a card says "wonder" —
    the same re-gloss ``/items/base`` gives a verb, and for the same reason."""

    SENTENCE = "Hun lurer på det."

    @pytest.fixture(autouse=True)
    def _verb(self, lemmatizer: StubLemmatizer) -> None:
        lemmatizer.set_analysis("lurer", "lure", upos="VERB")

    async def _mint(self, db, gloss: str = "wonders"):
        trick = _seed(db, "lure", "lurk, trick", "verb")
        resp = await _post(item_id=trick, surface="lurer", sentence=self.SENTENCE, translation=gloss)
        assert resp.status_code == 200
        return resp.json()

    async def test_the_card_takes_the_dictionary_form(self, api_app_state):
        llm = AsyncMock()
        llm.complete.return_value = "wonder"
        app.state.llm = llm

        data = await self._mint(api_app_state)

        assert data["was_created"] is True
        assert _rows(api_app_state, "lure") == [("verb", "lurk, trick"), ("wonder", "wonder")]
        # Asked about the card's own front, in the sentence it was met in.
        prompt = " ".join(str(a) for a in llm.complete.call_args.args) + str(llm.complete.call_args.kwargs)
        assert "lure" in prompt and self.SENTENCE in prompt

    async def test_a_second_tap_finds_the_card_without_asking_again(self, api_app_state):
        """The re-gloss is a model's answer and may differ next time, so the
        second request must be settled by the card that exists, not by a key
        derived from a fresh answer."""
        llm = AsyncMock()
        llm.complete.return_value = "wonder"
        app.state.llm = llm
        first = await self._mint(api_app_state)
        trick = api_app_state.get_collocation_id_by_guid(compute_guid("lure", LANG, "verb"))
        llm.complete.reset_mock()
        llm.complete.return_value = "ask oneself"

        again = await _post(item_id=trick, surface="lurer", sentence=self.SENTENCE, translation="wonders")

        assert (again.json()["id"], again.json()["was_created"]) == (first["id"], False)
        llm.complete.assert_not_called()
        assert len(_rows(api_app_state, "lure")) == 2

    async def test_a_dictionary_form_that_lost_the_meaning_is_not_used(self, api_app_state):
        """A card whose translation shares no word with the gloss it was made
        from would not be the card that gloss resolves to — the mint would have
        changed nothing the learner can see."""
        llm = AsyncMock()
        llm.complete.return_value = "ponder"
        app.state.llm = llm

        await self._mint(api_app_state)

        assert _rows(api_app_state, "lure") == [("verb", "lurk, trick"), ("wonders", "wonders")]

    async def test_an_empty_answer_keeps_the_gloss(self, api_app_state):
        llm = AsyncMock()
        llm.complete.return_value = ""
        app.state.llm = llm

        await self._mint(api_app_state)

        assert _rows(api_app_state, "lure") == [("verb", "lurk, trick"), ("wonders", "wonders")]

    async def test_without_a_model_the_gloss_is_the_meaning(self, api_app_state):
        await self._mint(api_app_state)

        assert _rows(api_app_state, "lure") == [("verb", "lurk, trick"), ("wonders", "wonders")]


@pytest.mark.usefixtures("lemmatizer")
class TestASenseCardReachesAnki:
    """Through ``sync_create_new``, which is where a TT-made card becomes a note.

    Slovene, because the mint notetype in the shared fake collection is
    Slovene's — and because this is the Slovene deck's own shape: ``ura`` keyed
    "hour" beside ``ura`` keyed "clock".
    """

    async def _sync(self, db, anki_conn) -> None:
        from app.plugins.anki_sync.sync import AnkiSync, OfflineWriter
        from tests._helpers.anki_sync_create_new import FakeReader

        await AnkiSync(db=db, _reader=FakeReader(), _writer=OfflineWriter(anki_conn)).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary"
        )

    async def test_two_meanings_become_two_notes_told_apart_by_their_key(self, api_app_state):
        from tests._helpers.anki_sync_create_new import _make_dual_collection_conn

        hour = _seed(api_app_state, "ura", "hour", "hour", lang="sl")
        resp = await _post(
            item_id=hour, surface="uro", sentence="Poglej na uro.", language_code="sl", translation="clock"
        )
        clock = resp.json()["id"]
        anki_conn = _make_dual_collection_conn()

        await self._sync(api_app_state, anki_conn)

        notes = anki_conn.execute("SELECT id, guid, flds FROM notes ORDER BY id").fetchall()
        assert len(notes) == 2
        assert len({n["guid"] for n in notes}) == 2
        fields = {n["id"]: n["flds"].split("\x1f") for n in notes}
        # Field 0 is the word, 1 its English, 6 the DisambigKey the reader
        # recovers the key from on the way back (sqlite_reader.py).
        assert sorted((f[0], f[1], f[6]) for f in fields.values()) == [
            ("ura", "clock", "clock"),
            ("ura", "hour", "hour"),
        ]
        linked = {cid: api_app_state.get_collocation_by_id(cid)[1].anki_note_id for cid in (hour, clock)}
        assert None not in linked.values()
        assert fields[linked[clock]][1] == "clock"
        assert fields[linked[hour]][1] == "hour"
        for nid in fields:
            assert anki_conn.execute("SELECT COUNT(*) FROM cards WHERE nid = ?", (nid,)).fetchone()[0] == 2

        # Idempotent: a second sync, and a second ask, add nothing.
        await _post(item_id=hour, surface="uro", sentence="Poglej na uro.", language_code="sl", translation="clock")
        await self._sync(api_app_state, anki_conn)
        assert anki_conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 2
        assert len(_rows(api_app_state, "ura")) == 2
