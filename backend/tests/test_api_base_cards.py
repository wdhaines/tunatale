"""Tests for POST /api/srs/items/base (Phase 5, Part C — unknown→create base).

A click on an unknown transcript word mints its base card as NEW, branching by
word type (decision 8, C-a): function word → production-only cloze (surface
blanked in the sentence); content word → vocab (recognition + production). Both
honor the add_collocation card-adding contract (no Anki ids; sync mints + links).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.models import CreateCardResponse
from app.common.guid import compute_guid
from app.main import app
from app.models.srs_item import Direction, SRSState
from tests._helpers.lemmatizer import StubLemmatizer
from tests._helpers.srs_item_shape import SRS_ITEM_KEYS


def _stub_verb(monkeypatch, surface: str, lemma: str) -> None:
    """Force the create-base-card lemmatizer to classify *surface* as a VERB."""
    import app.api.srs as srs_mod

    stub = StubLemmatizer()
    stub.set_analysis(surface, lemma, upos="VERB")
    monkeypatch.setattr(srs_mod, "get_lemmatizer", lambda code: stub)


class TestCreateBaseCard:
    @pytest.fixture(autouse=True)
    def _mock_audio(self, monkeypatch: pytest.MonkeyPatch):
        import app.api.srs as srs_mod

        monkeypatch.setattr(srs_mod, "synthesize_cloze_audios", AsyncMock())

    async def test_content_word_creates_vocab_base(self, api_app_state):
        """A non-function word → vocab base, NEW, both directions, no Anki ids."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/items/base",
                json={
                    "surface": "kava",
                    "lemma": "kava",
                    "sentence": "Pijem kavo.",
                    "language_code": "sl",
                    "translation": "coffee",
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["was_created"] is True
        item = data["item"]
        assert item["card_type"] == "vocab"
        assert item["state"] == "new"
        assert item["directions"]["recognition"] is not None
        assert item["directions"]["production"] is not None

        coll = api_app_state.get_collocation_by_guid(compute_guid("kava", "sl", ""))
        assert coll is not None
        assert coll.syntactic_unit.card_type == "vocab"
        assert coll.syntactic_unit.translation == "coffee"
        assert coll.anki_note_id is None
        assert coll.directions[Direction.RECOGNITION].state == SRSState.NEW
        assert coll.directions[Direction.PRODUCTION].state == SRSState.NEW

    async def test_verb_base_card_reglossed_to_bare_form(self, api_app_state, monkeypatch):
        """A verb → LLM re-glosses to the bare dictionary form (not the conjugation)."""
        _stub_verb(monkeypatch, "pokazem", "pokazati")
        mock_llm = AsyncMock()
        mock_llm.complete.return_value = "show"
        app.state.llm = mock_llm

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/items/base",
                json={
                    "surface": "pokazem",
                    "lemma": "pokazati",
                    "sentence": "vam pokazem mesto",
                    "language_code": "sl",
                    "translation": "I will show",  # conjugated transcript gloss — must be overridden
                },
            )

        assert resp.status_code == 200
        coll = api_app_state.get_collocation_by_guid(compute_guid("pokazati", "sl", ""))
        assert coll.syntactic_unit.translation == "show"
        assert coll.syntactic_unit.card_type == "vocab"

    async def test_verb_base_card_keeps_gloss_when_llm_returns_empty(self, api_app_state, monkeypatch):
        """LLM failure/empty → keep the caller-provided gloss (fail-soft)."""
        _stub_verb(monkeypatch, "pokazem", "pokazati")
        mock_llm = AsyncMock()
        mock_llm.complete.return_value = ""
        app.state.llm = mock_llm

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/items/base",
                json={
                    "surface": "pokazem",
                    "lemma": "pokazati",
                    "sentence": "vam pokazem mesto",
                    "language_code": "sl",
                    "translation": "I will show",
                },
            )

        assert resp.status_code == 200
        coll = api_app_state.get_collocation_by_guid(compute_guid("pokazati", "sl", ""))
        assert coll.syntactic_unit.translation == "I will show"

    async def test_verb_base_card_no_llm_keeps_gloss(self, api_app_state, monkeypatch):
        """No LLM configured → keep the caller-provided gloss unchanged."""
        _stub_verb(monkeypatch, "pokazem", "pokazati")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/items/base",
                json={
                    "surface": "pokazem",
                    "lemma": "pokazati",
                    "sentence": "vam pokazem mesto",
                    "language_code": "sl",
                    "translation": "I will show",
                },
            )

        assert resp.status_code == 200
        coll = api_app_state.get_collocation_by_guid(compute_guid("pokazati", "sl", ""))
        assert coll.syntactic_unit.translation == "I will show"

    async def test_norwegian_verb_base_card_prepends_infinitive_marker(self, api_app_state, monkeypatch):
        """A Norwegian VERB base card fronts as "å " + lemma (deck convention)."""
        import app.api.srs as srs_mod
        from tests._helpers.lemmatizer import StubLemmatizer

        stub = StubLemmatizer()
        stub.set_analysis("lyver", "lyve", upos="VERB")
        monkeypatch.setattr(srs_mod, "get_lemmatizer", lambda code: stub)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/items/base",
                json={
                    "surface": "lyver",
                    "lemma": "lyve",
                    "sentence": "Jeg lyver",
                    "language_code": "no",
                    "translation": "I lie",
                },
            )

        assert resp.status_code == 200
        assert resp.json()["item"]["text"] == "å lyve"
        coll = api_app_state.get_collocation_by_guid(compute_guid("å lyve", "no", ""))
        assert coll is not None
        assert coll.syntactic_unit.text == "å lyve"
        assert coll.syntactic_unit.card_type == "vocab"

    async def test_norwegian_verb_base_card_media_fetch_uses_bare_lemma(self, api_app_state, monkeypatch):
        """The Forvo/Pixabay fetch stays keyed on the bare lemma, not "å " + lemma."""
        import app.api.srs as srs_mod
        from app.cards.media import vocab_media
        from app.cards.media.pipeline import MediaResult
        from app.config import settings
        from tests._helpers.lemmatizer import StubLemmatizer

        stub = StubLemmatizer()
        stub.set_analysis("lyver", "lyve", upos="VERB")
        monkeypatch.setattr(srs_mod, "get_lemmatizer", lambda code: stub)
        monkeypatch.setattr(settings, "pixabay_api_key", "test-key")
        fetched: list[str] = []

        async def _query(_word, _english, **_kw):
            return "a clear depiction"

        async def _fetch(word, _english, **_kw):
            fetched.append(word)
            return MediaResult(audio_bytes=b"A", audio_source="forvo", image_bytes=b"I", image_ext="jpg")

        monkeypatch.setattr(vocab_media, "generate_image_query", _query)
        monkeypatch.setattr(vocab_media, "fetch_card_media", _fetch)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/items/base",
                json={
                    "surface": "lyver",
                    "lemma": "lyve",
                    "sentence": "Jeg lyver",
                    "language_code": "no",
                    "translation": "I lie",
                },
            )

        assert resp.status_code == 200
        assert resp.json()["item"]["text"] == "å lyve"
        assert fetched == ["lyve"]
        coll = api_app_state.get_collocation_by_guid(compute_guid("å lyve", "no", ""))
        assert coll.syntactic_unit.text == "å lyve"

    async def test_norwegian_non_verb_base_card_stays_unprefixed(self, api_app_state, monkeypatch):
        """A Norwegian NOUN base card keeps its bare lemma front."""
        import app.api.srs as srs_mod
        from tests._helpers.lemmatizer import StubLemmatizer

        stub = StubLemmatizer()
        stub.set_analysis("Huset", "hus", upos="NOUN")
        monkeypatch.setattr(srs_mod, "get_lemmatizer", lambda code: stub)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/items/base",
                json={
                    "surface": "huset",
                    "lemma": "hus",
                    "sentence": "Huset er stort",
                    "language_code": "no",
                    "translation": "the house",
                },
            )

        assert resp.status_code == 200
        assert resp.json()["item"]["text"] == "hus"
        coll = api_app_state.get_collocation_by_guid(compute_guid("hus", "no", ""))
        assert coll is not None
        assert coll.syntactic_unit.text == "hus"

    async def test_base_card_response_keys_match_model(self, api_app_state):
        """Oracle for the response_model flip (openapi ledger batch 7)."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/items/base",
                json={
                    "surface": "kava",
                    "lemma": "kava",
                    "sentence": "Pijem kavo.",
                    "language_code": "sl",
                    "translation": "coffee",
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert set(data.keys()) == {"id", "was_created", "item"}
        assert set(data["item"].keys()) == SRS_ITEM_KEYS
        assert set(CreateCardResponse.model_fields) == {"id", "was_created", "item"}

        # Element-level key set for the NESTED direction — the assertion the
        # top-level ones cannot make. `_direction_to_dict` OMITS `left` when it
        # is None (a fresh base card is NEW, so it always is), and a plain
        # `response_model=` would put `"left": null` back in. That is a payload
        # rewrite in the ADD direction, which is what
        # `response_model_exclude_unset=True` on this route exists to prevent.
        # Without this line the flag is unpinned: stripping it leaves the whole
        # file green, so the guard reads as protection it does not provide.
        rec = data["item"]["directions"]["recognition"]
        assert set(rec.keys()) == {
            "state",
            "due_at",
            "stability",
            "difficulty",
            "reps",
            "lapses",
            "last_review",
            "last_review_time_ms",
            "anki_card_id",
        }, "a NEW card's direction must not carry a `left` key"

    async def test_clozes_only_verb_rejected(self, api_app_state):
        """A clozes-only verb (biti) has no base card — 409, no row created."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/items/base",
                json={
                    "surface": "boste",
                    "lemma": "biti",
                    "sentence": "Kje boste ostali?",
                    "language_code": "sl",
                    "translation": "you will be",
                },
            )

        assert resp.status_code == 409
        detail = resp.json()["detail"].lower()
        assert "clozes-only" in detail or "no base card" in detail
        assert api_app_state.get_collocation_by_guid(compute_guid("biti", "sl", "")) is None

    @pytest.mark.parametrize(
        ("lemma", "surface", "gender", "expected_article"),
        [
            ("morder", "morderen", "Masc", "en"),
            ("alibi", "alibi", "Neut", "et"),
            ("jente", "jenta", "Fem", "ei/en"),
            # Stanza leaves gender blank on some nouns (23 of the cached ones,
            # mostly proper nouns). No gender ⇒ no guess.
            ("bergen", "Bergen", "", ""),
        ],
    )
    async def test_norwegian_noun_base_card_gets_its_gender_article(
        self, api_app_state, monkeypatch, lemma, surface, gender, expected_article
    ):
        """A TT-minted noun fronts with its article, like the imported deck's do.

        The gender is already computed and already cached — ``TokenAnalysis``
        carries it and ``create_base_card`` already reads ``ta.upos`` off the
        same object. This is the reported ``morder`` bug: 0 of 24 TT-minted rows
        carried an article against 2039 of 2990 imported ones.

        Fem maps to ``ei/en`` rather than ``ei`` because that is what the source
        deck writes (313 rows), so minted and imported cards render alike.
        """
        import app.api.srs as srs_mod

        stub = StubLemmatizer()
        stub.set_analysis(surface, lemma, upos="NOUN", gender=gender)
        monkeypatch.setattr(srs_mod, "get_lemmatizer", lambda code: stub)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/items/base",
                json={
                    "surface": surface,
                    "lemma": lemma,
                    "sentence": f"{surface} her",
                    "language_code": "no",
                    "translation": "x",
                },
            )

        assert resp.status_code == 200
        assert resp.json()["item"]["article"] == expected_article
        coll = api_app_state.get_collocation_by_guid(compute_guid(lemma, "no", ""))
        assert coll is not None
        assert coll.syntactic_unit.article == expected_article
        # The headword itself must stay bare: text feeds compute_guid, so baking
        # the article in would change the card's identity and orphan its Anki
        # note. The article is display-only.
        assert coll.syntactic_unit.text == lemma

    async def test_norwegian_verb_base_card_gets_no_article(self, api_app_state, monkeypatch):
        """Verbs keep their marker in ``text`` via format_vocab_headword.

        The imported deck puts the verb marker in the same Article field it uses
        for noun gender (615 rows of ``å``), which invites merging the two
        mechanisms. Don't: ``å fryse``'s GUID already includes the marker, so
        moving it would re-identify every existing verb card.
        """
        _stub_verb(monkeypatch, "fryser", "fryse")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/items/base",
                json={
                    "surface": "fryser",
                    "lemma": "fryse",
                    "sentence": "Han fryser",
                    "language_code": "no",
                    "translation": "freeze",
                },
            )

        assert resp.status_code == 200
        coll = api_app_state.get_collocation_by_guid(compute_guid("å fryse", "no", ""))
        assert coll is not None
        assert coll.syntactic_unit.text == "å fryse"
        assert coll.syntactic_unit.article == ""

    async def test_language_without_a_gender_article_map_is_a_no_op(self, api_app_state, monkeypatch):
        """Slovene registers no map, so a NOUN with a gender still gets ``""``.

        Slovene has no articles at all. The registry lookup must degrade to
        empty rather than inventing one, per the no-hardcoded-language-logic
        rule — core never branches on the language itself.
        """
        import app.api.srs as srs_mod

        stub = StubLemmatizer()
        stub.set_analysis("vodo", "voda", upos="NOUN", gender="Fem")
        monkeypatch.setattr(srs_mod, "get_lemmatizer", lambda code: stub)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/items/base",
                json={
                    "surface": "vodo",
                    "lemma": "voda",
                    "sentence": "Pijem vodo",
                    "language_code": "sl",
                    "translation": "water",
                },
            )

        assert resp.status_code == 200
        coll = api_app_state.get_collocation_by_guid(compute_guid("voda", "sl", ""))
        assert coll is not None
        assert coll.syntactic_unit.article == ""

    async def test_function_word_creates_cloze_base(self, api_app_state):
        """A function word (surface==lemma) → production-only cloze base."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/items/base",
                json={
                    "surface": "na",
                    "lemma": "na",
                    "sentence": "Kava na mizi.",
                    "language_code": "sl",
                    "translation": "on",
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["was_created"] is True
        item = data["item"]
        assert item["card_type"] == "cloze"
        assert item["directions"]["recognition"] is None
        assert item["directions"]["production"] is not None

        coll = api_app_state.get_collocation_by_guid(compute_guid("na", "sl", ""))
        assert coll.syntactic_unit.card_type == "cloze"
        assert coll.syntactic_unit.source_sentence == "Kava {{c1::na}} mizi."
        assert coll.anki_note_id is None
        assert coll.directions[Direction.PRODUCTION].state == SRSState.NEW

    async def test_function_word_via_pos_blanks_surface_not_lemma(self, api_app_state, monkeypatch):
        """A function word classified via its classla UPOS (PRON) → cloze base,
        keyed by the lemma and blanking the surface as it appeared.

        Uses a pronoun (surface 'ga' / lemma 'on') so the surface≠lemma blanking
        branch is covered with a *non*-clozes-only word — biti is rejected outright
        (see test_clozes_only_verb_rejected). Also exercises the upos-present path.
        """
        import app.api.srs as srs_mod
        from tests._helpers.lemmatizer import StubLemmatizer

        stub = StubLemmatizer()
        stub.set_analysis("ga", "on", upos="PRON")
        monkeypatch.setattr(srs_mod, "get_lemmatizer", lambda code: stub)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/items/base",
                json={
                    "surface": "ga",
                    "lemma": "on",
                    "sentence": "Ona ga vidi",
                    "language_code": "sl",
                    "translation": "him",
                },
            )

        assert resp.status_code == 200
        assert resp.json()["item"]["card_type"] == "cloze"
        coll = api_app_state.get_collocation_by_guid(compute_guid("on", "sl", ""))
        assert coll.syntactic_unit.card_type == "cloze"
        assert coll.syntactic_unit.text == "on"
        assert coll.syntactic_unit.source_sentence == "Ona {{c1::ga}} vidi"
        assert coll.directions[Direction.PRODUCTION].state == SRSState.NEW

    async def test_idempotent_returns_existing(self, api_app_state):
        """POST twice → one row; second call was_created False, same id."""
        body = {
            "surface": "kava",
            "lemma": "kava",
            "sentence": "Pijem kavo.",
            "language_code": "sl",
            "translation": "coffee",
        }
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r1 = await client.post("/api/srs/items/base", json=body)
            r2 = await client.post("/api/srs/items/base", json=body)

        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r1.json()["was_created"] is True
        assert r2.json()["was_created"] is False
        assert r1.json()["id"] == r2.json()["id"]

        with api_app_state._get_conn() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM collocations WHERE guid = ?", (compute_guid("kava", "sl", ""),)
            ).fetchone()[0]
        assert count == 1

    async def test_translation_defaults_empty(self, api_app_state):
        """translation is optional; omitting it stores empty (no LLM call)."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/items/base",
                json={"surface": "hotel", "lemma": "hotel", "sentence": "To je hotel.", "language_code": "sl"},
            )
        assert resp.status_code == 200
        coll = api_app_state.get_collocation_by_guid(compute_guid("hotel", "sl", ""))
        assert coll.syntactic_unit.translation == ""
        assert coll.syntactic_unit.card_type == "vocab"

    async def test_audio_synth_failure_does_not_crash(self, api_app_state, monkeypatch):
        """A cloze base whose audio synthesis raises still returns 200."""
        import app.api.srs as srs_mod

        monkeypatch.setattr(srs_mod, "synthesize_cloze_audios", AsyncMock(side_effect=RuntimeError("TTS failed")))

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/items/base",
                json={"surface": "na", "lemma": "na", "sentence": "Kava na mizi.", "language_code": "sl"},
            )

        assert resp.status_code == 200
        assert resp.json()["was_created"] is True

    async def test_sync_create_new_round_trip_vocab(self, api_app_state):
        """A vocab base links a 2-card Anki note via sync_create_new."""
        from app.plugins.anki_sync.sync import AnkiSync, OfflineWriter
        from tests._helpers.anki_sync_create_new import FakeReader, _make_dual_collection_conn

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/items/base",
                json={
                    "surface": "kava",
                    "lemma": "kava",
                    "sentence": "Pijem kavo.",
                    "language_code": "sl",
                    "translation": "coffee",
                },
            )
        assert resp.status_code == 200

        anki_conn = _make_dual_collection_conn()
        writer = OfflineWriter(anki_conn)
        await AnkiSync(db=api_app_state, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary"
        )

        notes = anki_conn.execute("SELECT n.id, n.mid FROM notes n").fetchall()
        assert len(notes) == 1
        cards = anki_conn.execute("SELECT * FROM cards WHERE nid = ?", (notes[0]["id"],)).fetchall()
        assert len(cards) == 2  # recognition + production
        coll = api_app_state.get_collocation_by_guid(compute_guid("kava", "sl", ""))
        assert coll.anki_note_id == notes[0]["id"]

    async def test_sync_create_new_round_trip_cloze(self, api_app_state):
        """A function-word cloze base links a single-card Cloze note."""
        from app.plugins.anki_sync.sync import AnkiSync, OfflineWriter
        from tests._helpers.anki_sync_create_new import FakeReader, _make_dual_collection_conn

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/items/base",
                json={"surface": "na", "lemma": "na", "sentence": "Kava na mizi.", "language_code": "sl"},
            )
        assert resp.status_code == 200

        anki_conn = _make_dual_collection_conn()
        writer = OfflineWriter(anki_conn)
        await AnkiSync(db=api_app_state, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary"
        )

        notes = anki_conn.execute("SELECT n.id, n.mid, n.flds FROM notes n").fetchall()
        assert len(notes) == 1
        assert notes[0]["mid"] == 1000002  # built-in Cloze notetype
        assert notes[0]["flds"].split("\x1f")[0] == "Kava {{c1::na}} mizi."
        cards = anki_conn.execute("SELECT * FROM cards WHERE nid = ?", (notes[0]["id"],)).fetchall()
        assert len(cards) == 1

    async def test_surfaces_same_day_in_review_queue(self, api_app_state):
        """A freshly minted NEW base appears in /review-queue without a sync."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/items/base",
                json={
                    "surface": "kava",
                    "lemma": "kava",
                    "sentence": "Pijem kavo.",
                    "language_code": "sl",
                    "translation": "coffee",
                },
            )
        assert resp.status_code == 200

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            queue_resp = await client.get("/api/srs/review-queue")
        assert queue_resp.status_code == 200

        matching = [q for q in queue_resp.json()["queue"] if q["text"] == "kava"]
        assert len(matching) >= 1
        assert matching[0]["state"] == "new"

    async def test_truncated_lemma_falls_back_to_surface(self, api_app_state, monkeypatch):
        """Stanza's truncated lemma (trøtt → trø) is rejected as the headword;
        the card fronts with the surface as it appeared — never a card for a non-word."""
        import app.api.srs as srs_mod
        from app.srs.lemmatizer import TokenAnalysis
        from tests._helpers.lemmatizer import StubLemmatizer

        stub = StubLemmatizer()
        stub.set_sentence(
            "Trøtt",
            [TokenAnalysis(surface="Trøtt", lemma="trø", upos="ADJ", case="", number="", person="", gender="")],
        )
        monkeypatch.setattr(srs_mod, "get_lemmatizer", lambda code: stub)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/items/base",
                json={
                    "surface": "Trøtt",
                    "lemma": "trø",
                    "sentence": "Trøtt",
                    "language_code": "no",
                    "translation": "tired",
                },
            )

        assert resp.status_code == 200
        assert resp.json()["item"]["text"] == "trøtt"
        coll = api_app_state.get_collocation_by_guid(compute_guid("trøtt", "no", ""))
        assert coll is not None
        assert coll.syntactic_unit.text == "trøtt"
        assert coll.syntactic_unit.lemma == "trøtt"
        assert coll.syntactic_unit.translation == "tired"
        assert api_app_state.get_collocation_by_guid(compute_guid("trø", "no", "")) is None
