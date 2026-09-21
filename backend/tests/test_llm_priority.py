"""Foreground LLM calls must not queue behind background work (tunatale-rwkz.3).

2026-09-19: a user-facing import waited ~2.5 min on Groq pacing because a sync
had just queued ~30 prestage calls, all sharing one pacing timestamp with no
ordering. These pin the two halves of the fix against the real ``LLMClient``,
with only the Groq HTTP endpoint faked (respx):

  ORDER   — with background calls already waiting, a foreground call issued
            afterwards is sent first.
  SPACING — waiting callers are admitted one at a time, each after the pacing
            deadline the previous response set. The old code slept every
            waiter until the SAME instant and fired them together, which is the
            burst that drew the 429.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time

import respx
from httpx import Response

from app.common.background_work import BackgroundWork
from app.llm.client import GROQ_API_URL, LLMClient
from app.llm.priority_gate import BACKGROUND, FOREGROUND, PriorityGate


def _ok(content: str, headers: dict | None = None) -> Response:
    return Response(200, headers=headers or {}, json={"choices": [{"message": {"content": content}}]})


def _prompt_of(request) -> str:
    return json.loads(request.content)["messages"][-1]["content"]


class TestForegroundGoesFirst:
    async def test_a_later_foreground_call_is_sent_before_queued_background_calls(self):
        client = LLMClient(groq_api_key="k", groq_extra_body_params={})
        work = BackgroundWork()
        sent: list[str] = []

        def _record(request):
            sent.append(_prompt_of(request))
            return _ok("ok")

        # A pacing deadline in the near future, so everything issued now queues.
        client._next_call_at = time.monotonic() + 0.3

        async def background(i: int) -> None:
            await client.complete(f"bg{i}", call_site="media_choose")

        with respx.mock:
            respx.post(GROQ_API_URL).mock(side_effect=_record)
            tasks = [asyncio.create_task(work.track("prestage_images", background)(i)) for i in range(3)]
            await asyncio.sleep(0.05)  # let all three reach the gate
            fg = asyncio.create_task(client.complete("fg", call_site="glossing"))
            await asyncio.gather(*tasks, fg)

        assert sent[0] == "fg", f"foreground was not first: {sent}"
        assert sent[1:] == ["bg0", "bg1", "bg2"], f"background lost FIFO order: {sent}"

    async def test_without_background_work_calls_stay_first_come_first_served(self):
        """The control: two foreground calls keep arrival order, so the test
        above measures priority, not some reordering of every call."""
        client = LLMClient(groq_api_key="k", groq_extra_body_params={})
        sent: list[str] = []

        def _record(request):
            sent.append(_prompt_of(request))
            return _ok("ok")

        client._next_call_at = time.monotonic() + 0.2
        with respx.mock:
            respx.post(GROQ_API_URL).mock(side_effect=_record)
            first = asyncio.create_task(client.complete("a", call_site="glossing"))
            await asyncio.sleep(0.02)
            second = asyncio.create_task(client.complete("b", call_site="glossing"))
            await asyncio.gather(first, second)

        assert sent == ["a", "b"]


class TestPacingSpacesTheQueue:
    async def test_queued_callers_are_spaced_by_the_pacing_each_response_sets(self):
        client = LLMClient(groq_api_key="k", groq_extra_body_params={})
        stamps: list[float] = []
        # remaining=1, reset=0.15s → proactive delay of 0.15s after every call.
        headers = {"x-ratelimit-remaining-requests": "1", "x-ratelimit-reset-requests": "0.15s"}

        def _record(request):
            stamps.append(time.monotonic())
            return _ok("ok", headers)

        client._next_call_at = time.monotonic() + 0.1
        with respx.mock:
            respx.post(GROQ_API_URL).mock(side_effect=_record)
            await asyncio.gather(*(client.complete(f"p{i}", call_site="glossing") for i in range(3)))

        gaps = [b - a for a, b in zip(stamps, stamps[1:], strict=False)]
        assert len(stamps) == 3
        assert all(g >= 0.13 for g in gaps), f"calls fired as a burst, gaps={gaps}"

    async def test_a_429_retry_waits_its_retry_after_through_the_gate(self):
        client = LLMClient(groq_api_key="k", groq_extra_body_params={}, max_retries_429=1, max_retry_after_s=5.0)
        stamps: list[float] = []
        responses = iter([Response(429, headers={"retry-after": "0.2"}, json={}), _ok("done")])

        def _record(request):
            stamps.append(time.monotonic())
            return next(responses)

        with respx.mock:
            respx.post(GROQ_API_URL).mock(side_effect=_record)
            assert await client.complete("q", call_site="glossing") == "done"

        assert stamps[1] - stamps[0] >= 0.18


class TestGate:
    async def test_cancelling_a_waiter_does_not_wedge_the_gate(self):
        gate = PriorityGate(ready_at=lambda: 0.0)
        await gate.acquire(FOREGROUND, gate.ticket())  # held
        stuck = asyncio.create_task(gate.acquire(BACKGROUND, gate.ticket()))
        await asyncio.sleep(0)
        stuck.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await stuck
        assert gate.waiting() == 0
        gate.release()
        await asyncio.wait_for(gate.acquire(FOREGROUND, gate.ticket()), timeout=1)

    async def test_a_cancelled_head_passes_the_slot_on(self):
        """The head is the one sleeping out the pacing deadline; cancelling it
        must wake the next waiter rather than leave it asleep forever."""
        deadline = time.monotonic() + 0.1
        gate = PriorityGate(ready_at=lambda: deadline)
        head = asyncio.create_task(gate.acquire(FOREGROUND, gate.ticket()))
        await asyncio.sleep(0)
        nxt = asyncio.create_task(gate.acquire(BACKGROUND, gate.ticket()))
        await asyncio.sleep(0)
        head.cancel()
        await asyncio.wait_for(nxt, timeout=1)
