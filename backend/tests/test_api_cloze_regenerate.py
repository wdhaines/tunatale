"""POST /api/srs/items/{id}/cloze/regenerate — the "try again" control (tunatale-keb0).

The learner has met a cloze whose blank several words fit and asked for another
sentence. The endpoint generates one, judges it blind, and keeps it only if it
beats what is stored.

⚠️ It must NOT open the Anki collection — ``.claude/rules/anki-safety-core.md``
puts collection access at sync time only. The endpoint writes the TT row and
marks it dirty; ``sync_push`` carries it over via ``update_cloze_text``.

The LLM double is passed via ``app.state.llm``, never patched.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.syntactic_unit import SyntacticUnit
from app.srs.database import SRSDatabase


class ScriptedLLM:
    """Answers the judge and the generator from fixed scripts.

    The two are told apart by the system prompt, not the user prompt: the judge
    sends a blanked sentence and the generator sends a bare word, and both are
    short. ``fillers_for`` is keyed on the blanked sentence so a regenerated
    candidate can be judged differently from the stored one.
    """

    def __init__(self, *, fillers_for: dict[str, str], generated: list[str | None]):
        self._fillers_for = fillers_for
        self._generated = list(generated)
        self.judge_prompts: list[str] = []
        self.generate_calls = 0

    async def complete(self, prompt, system_prompt=None, temperature=0.7, max_tokens=256):
        if "blank" in (system_prompt or ""):
            self.judge_prompts.append(prompt)
            for needle, reply in self._fillers_for.items():
                if needle in prompt:
                    return reply
            return ""
        self.generate_calls += 1
        if not self._generated:
            return ""
        nxt = self._generated.pop(0)
        if nxt is None:
            raise RuntimeError("429 rate limited")
        return nxt


def _setup(llm, *, sentence="{{c1::han}} kommer i morgen", card_type="cloze") -> tuple[SRSDatabase, int]:
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
        source_sentence_translation="he is coming tomorrow",
    )
    db.add_collocation(unit, language_code="no")
    row_id = db.get_collocation_id_by_guid(db.get_collocation_by_lemma("han").guid)
    app.state.srs_db = db
    app.state.llm = llm
    return db, row_id


async def _post(row_id: int):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post(f"/api/srs/items/{row_id}/cloze/regenerate")


class TestRegenerateCloze:
    @pytest.mark.asyncio
    async def test_replaces_an_ambiguous_sentence_with_a_determined_one(self):
        """The whole point: ten pronouns fit the stored blank, one fits the new one."""
        llm = ScriptedLLM(
            fillers_for={
                "___ kommer i morgen": "han, hun, jeg, du, vi, de",
                "Kari kommer i morgen. ___ tar toget.": "han",
            },
            generated=["Kari kommer i morgen. Han tar toget."],
        )
        db, row_id = _setup(llm)

        resp = await _post(row_id)
        assert resp.status_code == 200
        body = resp.json()
        assert body["changed"] is True
        assert body["status"] == "determined"
        assert body["competitors"] == []
        assert body["sentence"] == "Kari kommer i morgen. {{c1::Han}} tar toget."

    @pytest.mark.asyncio
    async def test_stores_the_sentence_cloze_marked_and_flags_it_for_sync(self):
        llm = ScriptedLLM(
            fillers_for={"___ kommer i morgen": "han, hun, jeg", "Kari ringte. ___ tar toget.": "han"},
            generated=["Kari ringte. Han tar toget."],
        )
        db, row_id = _setup(llm)
        await _post(row_id)

        _rid, item, _lang = db.get_collocation_by_id(row_id)
        assert item.syntactic_unit.source_sentence == "Kari ringte. {{c1::Han}} tar toget."
        assert "source_sentence" in db.get_dirty_fields(item.guid)

    @pytest.mark.asyncio
    async def test_keeps_the_stored_sentence_when_the_candidate_is_no_better(self):
        """Churn costs the learner a card they have partly learned and buys nothing."""
        llm = ScriptedLLM(
            fillers_for={
                "___ kommer i morgen": "han, hun",
                "___ liker kaffe": "han, hun, jeg, du, vi, de",
            },
            generated=["Han liker kaffe.", "Han liker kaffe."],
        )
        db, row_id = _setup(llm)

        body = (await _post(row_id)).json()
        assert body["changed"] is False
        assert body["sentence"] == "{{c1::han}} kommer i morgen"
        _rid, item, _lang = db.get_collocation_by_id(row_id)
        assert item.syntactic_unit.source_sentence == "{{c1::han}} kommer i morgen"

    @pytest.mark.asyncio
    async def test_accepts_a_still_ambiguous_but_strictly_better_sentence(self):
        """Most of these words have no fully determining short sentence.

        Ten competitors down to one is the improvement the learner actually
        feels; a rule that only accepted "determined" would keep the worse
        sentence and report failure.
        """
        llm = ScriptedLLM(
            fillers_for={
                "___ kommer i morgen": "han, hun, jeg, du, vi, de",
                "Kari ringte. ___ tar toget.": "han, hun",
            },
            generated=["Kari ringte. Han tar toget."],
        )
        db, row_id = _setup(llm)

        body = (await _post(row_id)).json()
        assert body["changed"] is True
        assert body["status"] == "underdetermined"
        assert body["competitors"] == ["hun"]

    @pytest.mark.asyncio
    async def test_retries_once_when_the_first_draft_fails(self):
        llm = ScriptedLLM(
            fillers_for={"___ kommer i morgen": "han, hun, jeg", "Kari ringte. ___ tar toget.": "han"},
            generated=[None, "Kari ringte. Han tar toget."],
        )
        db, row_id = _setup(llm)

        body = (await _post(row_id)).json()
        assert body["changed"] is True
        assert llm.generate_calls == 2

    @pytest.mark.asyncio
    async def test_stops_after_two_attempts(self):
        """Bounded, because the common case here never reaches "determined"."""
        llm = ScriptedLLM(
            fillers_for={"___ kommer i morgen": "han, hun", "___ er her": "han, hun, jeg, du"},
            generated=["Han er her.", "Han er her.", "Han er her."],
        )
        db, row_id = _setup(llm)
        await _post(row_id)
        assert llm.generate_calls == 2

    @pytest.mark.asyncio
    async def test_never_sends_the_answer_to_the_judge(self):
        """The blind fill-in is the design; see app.llm.cloze_quality."""
        llm = ScriptedLLM(
            fillers_for={"___ kommer i morgen": "han, hun"},
            generated=[None, None],
        )
        db, row_id = _setup(llm)
        await _post(row_id)

        assert llm.judge_prompts, "the stored sentence must be judged"
        for prompt in llm.judge_prompts:
            assert "han" not in prompt.lower()
            assert "___" in prompt

    @pytest.mark.asyncio
    async def test_unchanged_when_the_llm_cannot_produce_anything(self):
        llm = ScriptedLLM(fillers_for={"___ kommer i morgen": "han, hun"}, generated=[None, None])
        db, row_id = _setup(llm)

        body = (await _post(row_id)).json()
        assert body["changed"] is False
        _rid, item, _lang = db.get_collocation_by_id(row_id)
        assert item.syntactic_unit.source_sentence == "{{c1::han}} kommer i morgen"

    @pytest.mark.asyncio
    async def test_unknown_item_is_404(self):
        llm = ScriptedLLM(fillers_for={}, generated=[])
        _db, _row_id = _setup(llm)
        resp = await _post(999_999)
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_non_cloze_item_is_409(self):
        llm = ScriptedLLM(fillers_for={}, generated=[])
        _db, row_id = _setup(llm, card_type="vocab")
        resp = await _post(row_id)
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_no_llm_configured_is_503(self):
        _db, row_id = _setup(ScriptedLLM(fillers_for={}, generated=[]))
        app.state.llm = None
        resp = await _post(row_id)
        assert resp.status_code == 503
