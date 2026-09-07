"""The confirm-before-write cloze-sentence pair (tunatale-keb0).

``POST /api/srs/items/{id}/cloze/propose`` offers a replacement sentence and
its blind verdict; ``PUT /api/srs/items/{id}/cloze/sentence`` stores what the
human chose. The split exists because the first version wrote on the single
click that produced the sentence: the row went dirty immediately, so a sync
firing before the learner had read it had already rewritten the Anki note in
place — guid, ``sfld`` and ``csum`` with it — and there was nothing cheap left
to undo.

⚠️ Neither endpoint may open the Anki collection —
``.claude/rules/anki-safety-core.md`` puts collection access at sync time only.
The PUT writes the TT row and marks it dirty; ``sync_push`` carries it over via
``update_cloze_text``.

The LLM double is passed via ``app.state.llm``, never patched.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.syntactic_unit import SyntacticUnit
from app.srs.database import SRSDatabase


class ScriptedLLM:
    """Answers the judge, the generator and the translator from fixed scripts.

    The three are told apart by the system prompt, not the user prompt: the
    judge's names the blank, the translator's names a translation, and the
    generator's is neither. ``fillers_for`` is keyed on the blanked sentence so
    a candidate can be judged differently from the stored one.
    """

    def __init__(
        self,
        *,
        fillers_for: dict[str, str],
        generated: list[str | None] | None = None,
        translations: dict[str, str] | None = None,
    ):
        self._fillers_for = fillers_for
        self._generated = list(generated or [])
        self._translations = translations or {}
        self.judge_prompts: list[str] = []
        self.translate_prompts: list[str] = []
        self.generate_calls = 0

    async def complete(self, prompt, system_prompt=None, temperature=0.7, max_tokens=256):
        system = system_prompt or ""
        if "blank" in system:
            self.judge_prompts.append(prompt)
            for needle, reply in self._fillers_for.items():
                if needle in prompt:
                    return reply
            return ""
        if "translator" in system:
            self.translate_prompts.append(prompt)
            return self._translations.get(prompt, "")
        self.generate_calls += 1
        if not self._generated:
            return ""
        nxt = self._generated.pop(0)
        if nxt is None:
            raise RuntimeError("429 rate limited")
        return nxt


def _setup(
    llm,
    *,
    sentence="{{c1::han}} kommer i morgen",
    card_type="cloze",
    sentence_translation="he is coming tomorrow",
) -> tuple[SRSDatabase, int]:
    db = SRSDatabase(":memory:")
    unit = SyntacticUnit(
        text="han",
        translation="he",
        word_count=1,
        difficulty=1,
        source="anki",
        lemma="han",
        card_type=card_type,
        source_sentence=sentence,
        source_sentence_translation=sentence_translation,
    )
    db.add_collocation(unit, language_code="no")
    row_id = db.get_collocation_id_by_guid(db.get_collocation_by_lemma("han").guid)
    app.state.srs_db = db
    app.state.llm = llm
    return db, row_id


async def _propose(row_id: int):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post(f"/api/srs/items/{row_id}/cloze/propose")


async def _put(row_id: int, sentence: str):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.put(f"/api/srs/items/{row_id}/cloze/sentence", json={"sentence": sentence})


def _stored(db: SRSDatabase, row_id: int) -> SyntacticUnit:
    _rid, item, _lang = db.get_collocation_by_id(row_id)
    return item.syntactic_unit


class TestProposeWritesNothing:
    """The reason the endpoint was split. Every other propose test is detail."""

    @pytest.mark.asyncio
    async def test_a_better_candidate_is_offered_not_stored(self):
        llm = ScriptedLLM(
            fillers_for={
                "___ kommer i morgen": "han, hun, jeg, du, vi, de",
                "Kari kommer i morgen. ___ tar toget.": "han",
            },
            generated=["Kari kommer i morgen. Han tar toget."],
        )
        db, row_id = _setup(llm)

        body = (await _propose(row_id)).json()
        assert body["candidate"]["sentence"] == "Kari kommer i morgen. {{c1::Han}} tar toget."

        unit = _stored(db, row_id)
        assert unit.source_sentence == "{{c1::han}} kommer i morgen"
        assert unit.source_sentence_translation == "he is coming tomorrow"

    @pytest.mark.asyncio
    async def test_the_row_is_not_flagged_for_sync(self):
        """A dirty flag is what a sync acts on, so propose must leave none.

        This is the mechanism the old one-shot version got wrong: the flag was
        set before the learner had read the sentence, and `sync_push` needs no
        further permission to rewrite the Anki note from it.
        """
        llm = ScriptedLLM(
            fillers_for={"___ kommer i morgen": "han, hun, jeg", "Kari ringte. ___ tar toget.": "han"},
            generated=["Kari ringte. Han tar toget."],
        )
        db, row_id = _setup(llm)
        guid = _stored(db, row_id) and db.get_collocation_by_lemma("han").guid

        await _propose(row_id)

        assert "source_sentence" not in db.get_dirty_fields(guid)
        assert "sentence_translation" not in db.get_dirty_fields(guid)


class TestProposeVerdicts:
    @pytest.mark.asyncio
    async def test_reports_the_stored_sentence_and_its_verdict(self):
        llm = ScriptedLLM(
            fillers_for={"___ kommer i morgen": "han, hun, jeg", "___ er her": "han"},
            generated=["Han er her."],
        )
        _db, row_id = _setup(llm)

        current = (await _propose(row_id)).json()["current"]
        assert current["sentence"] == "{{c1::han}} kommer i morgen"
        assert current["status"] == "underdetermined"
        assert current["competitors"] == ["hun", "jeg"]

    @pytest.mark.asyncio
    async def test_current_shows_the_stored_translation_not_a_fresh_one(self):
        """The pane says what the card says TODAY.

        Re-translating here would paper over exactly the mismatch a reader is
        being asked to judge — the stale English the one-shot version left
        behind.
        """
        llm = ScriptedLLM(
            fillers_for={"___ kommer i morgen": "han, hun"},
            generated=[None, None],
        )
        _db, row_id = _setup(llm, sentence_translation="an English line from some older sentence")

        current = (await _propose(row_id)).json()["current"]
        assert current["translation"] == "an English line from some older sentence"

    @pytest.mark.asyncio
    async def test_the_candidate_carries_its_own_translation(self):
        """So the human confirms the English they were actually shown."""
        llm = ScriptedLLM(
            fillers_for={"___ kommer i morgen": "han, hun, jeg", "Kari ringte. ___ tar toget.": "han"},
            generated=["Kari ringte. Han tar toget."],
            translations={"Kari ringte. Han tar toget.": "Kari called. He is taking the train."},
        )
        _db, row_id = _setup(llm)

        candidate = (await _propose(row_id)).json()["candidate"]
        assert candidate["translation"] == "Kari called. He is taking the train."

    @pytest.mark.asyncio
    async def test_recommends_a_strictly_better_candidate(self):
        llm = ScriptedLLM(
            fillers_for={
                "___ kommer i morgen": "han, hun, jeg, du, vi, de",
                "Kari ringte. ___ tar toget.": "han, hun",
            },
            generated=["Kari ringte. Han tar toget."],
        )
        _db, row_id = _setup(llm)

        body = (await _propose(row_id)).json()
        assert body["recommended"] is True
        assert body["candidate"]["status"] == "underdetermined"
        assert body["candidate"]["competitors"] == ["hun"]

    @pytest.mark.asyncio
    async def test_a_worse_candidate_is_still_offered_but_not_recommended(self):
        """The human decides. `_is_better` labels the offer, it does not gate it.

        The one-shot version returned `changed: false` here and the learner saw
        nothing — which reads as a broken button when the words this feature is
        for are mostly ones no short sentence determines.
        """
        llm = ScriptedLLM(
            fillers_for={
                "___ kommer i morgen": "han, hun",
                "___ liker kaffe": "han, hun, jeg, du, vi, de",
            },
            generated=["Han liker kaffe.", "Han liker kaffe."],
        )
        _db, row_id = _setup(llm)

        body = (await _propose(row_id)).json()
        assert body["recommended"] is False
        assert body["candidate"]["sentence"] == "{{c1::Han}} liker kaffe."
        assert body["candidate"]["competitors"] == ["hun", "jeg", "du", "vi", "de"]

    @pytest.mark.asyncio
    async def test_candidate_is_null_when_the_llm_produces_nothing(self):
        llm = ScriptedLLM(fillers_for={"___ kommer i morgen": "han, hun"}, generated=[None, None])
        _db, row_id = _setup(llm)

        body = (await _propose(row_id)).json()
        assert body["candidate"] is None
        assert body["recommended"] is False

    @pytest.mark.asyncio
    async def test_retries_once_when_the_first_draft_fails(self):
        llm = ScriptedLLM(
            fillers_for={"___ kommer i morgen": "han, hun, jeg", "Kari ringte. ___ tar toget.": "han"},
            generated=[None, "Kari ringte. Han tar toget."],
        )
        _db, row_id = _setup(llm)

        body = (await _propose(row_id)).json()
        assert body["candidate"] is not None
        assert llm.generate_calls == 2

    @pytest.mark.asyncio
    async def test_stops_after_two_attempts(self):
        """Bounded, because the common case here never reaches "determined".

        "Suggest another" is the loop, and it is driven by a human who can stop.
        """
        llm = ScriptedLLM(
            fillers_for={"___ kommer i morgen": "han, hun", "___ er her": "han, hun, jeg, du"},
            generated=["Han er her.", "Han er her.", "Han er her."],
        )
        _db, row_id = _setup(llm)
        await _propose(row_id)
        assert llm.generate_calls == 2

    @pytest.mark.asyncio
    async def test_never_sends_the_answer_to_the_judge(self):
        """The blind fill-in is the design; see app.llm.cloze_quality."""
        llm = ScriptedLLM(fillers_for={"___ kommer i morgen": "han, hun"}, generated=[None, None])
        _db, row_id = _setup(llm)
        await _propose(row_id)

        assert llm.judge_prompts, "the stored sentence must be judged"
        for prompt in llm.judge_prompts:
            assert "han" not in prompt.lower()
            assert "___" in prompt

    @pytest.mark.asyncio
    async def test_unknown_item_is_404(self):
        _setup(ScriptedLLM(fillers_for={}))
        assert (await _propose(999_999)).status_code == 404

    @pytest.mark.asyncio
    async def test_non_cloze_item_is_409(self):
        _db, row_id = _setup(ScriptedLLM(fillers_for={}), card_type="vocab")
        assert (await _propose(row_id)).status_code == 409

    @pytest.mark.asyncio
    async def test_no_llm_configured_is_503(self):
        _db, row_id = _setup(ScriptedLLM(fillers_for={}))
        app.state.llm = None
        assert (await _propose(row_id)).status_code == 503


class TestSetClozeSentence:
    @pytest.mark.asyncio
    async def test_stores_the_sentence_and_flags_it_for_sync(self):
        llm = ScriptedLLM(fillers_for={}, translations={"Kari ringte. Han tar toget.": "Kari called."})
        db, row_id = _setup(llm)
        guid = db.get_collocation_by_lemma("han").guid

        resp = await _put(row_id, "Kari ringte. {{c1::Han}} tar toget.")
        assert resp.status_code == 200
        assert resp.json()["sentence"] == "Kari ringte. {{c1::Han}} tar toget."

        assert _stored(db, row_id).source_sentence == "Kari ringte. {{c1::Han}} tar toget."
        assert "source_sentence" in db.get_dirty_fields(guid)

    @pytest.mark.asyncio
    async def test_replaces_the_stale_translation(self):
        """The defect that made a one-click rewrite unsafe to confirm.

        `set_cloze_sentence` used to write `source_sentence` alone, leaving an
        English line under a Norwegian sentence it does not translate — on the
        `/review` back and on the Anki note's Back Extra alike.
        """
        llm = ScriptedLLM(fillers_for={}, translations={"Kari ringte. Han tar toget.": "Kari called."})
        db, row_id = _setup(llm, sentence_translation="he is coming tomorrow")
        guid = db.get_collocation_by_lemma("han").guid

        body = (await _put(row_id, "Kari ringte. {{c1::Han}} tar toget.")).json()

        assert body["translation"] == "Kari called."
        assert _stored(db, row_id).source_sentence_translation == "Kari called."
        # Back Extra is rebuilt off this flag; without it the new English never
        # reaches Anki even though TT shows it.
        assert "sentence_translation" in db.get_dirty_fields(guid)

    @pytest.mark.asyncio
    async def test_translates_the_sentence_without_its_cloze_markup(self):
        """`{{c1::Han}}` is markup, not Norwegian — the translator must not see it."""
        llm = ScriptedLLM(fillers_for={}, translations={"Kari ringte. Han tar toget.": "Kari called."})
        _db, row_id = _setup(llm)

        await _put(row_id, "Kari ringte. {{c1::Han}} tar toget.")

        assert llm.translate_prompts == ["Kari ringte. Han tar toget."]

    @pytest.mark.asyncio
    async def test_an_untranslatable_sentence_stores_an_empty_translation(self):
        """Empty is honest. Keeping the previous sentence's English is a lie."""
        llm = ScriptedLLM(fillers_for={}, translations={})
        db, row_id = _setup(llm, sentence_translation="he is coming tomorrow")

        body = (await _put(row_id, "Kari ringte. {{c1::Han}} tar toget.")).json()

        assert body["translation"] == ""
        assert _stored(db, row_id).source_sentence_translation == ""

    @pytest.mark.asyncio
    async def test_wraps_a_plain_hand_typed_sentence(self):
        """The edit box takes Norwegian, not Anki markup."""
        llm = ScriptedLLM(fillers_for={})
        db, row_id = _setup(llm)

        body = (await _put(row_id, "Kari ringte. Han tar toget.")).json()

        assert body["sentence"] == "Kari ringte. {{c1::Han}} tar toget."
        assert _stored(db, row_id).source_sentence == "Kari ringte. {{c1::Han}} tar toget."

    @pytest.mark.asyncio
    async def test_rejects_a_sentence_the_word_does_not_occur_in(self):
        """It would blank nothing: Anki calls such a note an empty card, and the
        learner would be shown the answer as the question."""
        llm = ScriptedLLM(fillers_for={})
        db, row_id = _setup(llm)

        resp = await _put(row_id, "Kari tar toget.")
        assert resp.status_code == 422
        assert "han" in resp.json()["detail"]
        assert _stored(db, row_id).source_sentence == "{{c1::han}} kommer i morgen"

    @pytest.mark.asyncio
    async def test_rejects_an_empty_sentence(self):
        llm = ScriptedLLM(fillers_for={})
        db, row_id = _setup(llm)

        assert (await _put(row_id, "   ")).status_code == 422
        assert _stored(db, row_id).source_sentence == "{{c1::han}} kommer i morgen"

    @pytest.mark.asyncio
    async def test_drops_the_sentence_audio_that_reads_the_old_sentence(self):
        """The clip is named `tts_sentence_{sha256(sentence)}.mp3`.

        After a rewrite the stored row still points at the previous sentence's
        file, so the note would read a different sentence aloud over the right
        one. Dropped and re-synthesized; if TTS is down the card is left with no
        sentence audio, which is the state `backfill_cloze_tts` repairs.
        """
        llm = ScriptedLLM(fillers_for={})
        db, row_id = _setup(llm)
        db.add_media(
            row_id,
            "audio_tts_sentence",
            "tts_sentence_deadbeefdeadbeef.mp3",
            "/tmp/tts_sentence_deadbeefdeadbeef.mp3",
            "tts_sentence_deadbeefdeadbeef.mp3",
            "0" * 64,
            123,
        )
        assert db.get_sentence_audio_filename(row_id) == "tts_sentence_deadbeefdeadbeef.mp3"

        await _put(row_id, "Kari ringte. {{c1::Han}} tar toget.")

        assert db.get_sentence_audio_filename(row_id) != "tts_sentence_deadbeefdeadbeef.mp3"

    @pytest.mark.asyncio
    async def test_a_tts_failure_does_not_undo_the_accepted_sentence(self, monkeypatch):
        """Fail-OPEN, like `_persist_new_card`. Only the audio is lost.

        The 2-arg `monkeypatch.setattr(srs_mod, ...)` is the seam four existing
        suites already use for this exact branch (`test_api_audio.py`,
        `test_api_base_cards.py`, `test_api_inflection_clozes.py`) — the
        synthesis call is the TTS side effect, and there is no other way to make
        it raise without contriving a filesystem fault.
        """
        import app.api.srs as srs_mod

        db, row_id = _setup(ScriptedLLM(fillers_for={}))
        monkeypatch.setattr(srs_mod, "synthesize_cloze_audios", AsyncMock(side_effect=RuntimeError("TTS failed")))

        resp = await _put(row_id, "Kari ringte. {{c1::Han}} tar toget.")

        assert resp.status_code == 200
        assert _stored(db, row_id).source_sentence == "Kari ringte. {{c1::Han}} tar toget."

    @pytest.mark.asyncio
    async def test_stores_without_an_llm_rather_than_discarding_the_choice(self):
        """503 here would throw away a decision the human already made.

        Unlike `propose`, which has nothing to do without a generator, the PUT's
        job is to record a sentence that already exists.
        """
        db, row_id = _setup(ScriptedLLM(fillers_for={}), sentence_translation="stale English")
        app.state.llm = None

        resp = await _put(row_id, "Kari ringte. {{c1::Han}} tar toget.")

        assert resp.status_code == 200
        assert _stored(db, row_id).source_sentence == "Kari ringte. {{c1::Han}} tar toget."
        assert _stored(db, row_id).source_sentence_translation == ""

    @pytest.mark.asyncio
    async def test_unknown_item_is_404(self):
        _setup(ScriptedLLM(fillers_for={}))
        assert (await _put(999_999, "Han er her.")).status_code == 404

    @pytest.mark.asyncio
    async def test_non_cloze_item_is_409(self):
        _db, row_id = _setup(ScriptedLLM(fillers_for={}), card_type="vocab")
        assert (await _put(row_id, "Han er her.")).status_code == 409
