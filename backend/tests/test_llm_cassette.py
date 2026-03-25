"""Cassette system tests."""

import json
from pathlib import Path

import pytest

from app.llm.cassette import CassetteLLMClient, _hash_prompt


def _write_cassette(path: Path, calls: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"recorded_at": "2026-01-01T00:00:00+00:00", "calls": calls}))


@pytest.fixture
def cassette_dir(tmp_path: Path) -> Path:
    return tmp_path / "cassettes"


def test_hash_prompt_is_deterministic():
    h1 = _hash_prompt("hello world")
    h2 = _hash_prompt("hello world")
    assert h1 == h2
    assert h1.startswith("sha256:")


def test_hash_prompt_different_prompts():
    assert _hash_prompt("a") != _hash_prompt("b")


@pytest.mark.asyncio
async def test_mock_mode_replays_cassette(cassette_dir, tmp_path):
    prompt = "test prompt"
    h = _hash_prompt(prompt)
    cassette_path = cassette_dir / "test.json"
    _write_cassette(
        cassette_path,
        [
            {
                "prompt_hash": h,
                "prompt_preview": prompt[:80],
                "response": "mocked!",
                "max_tokens": 256,
                "provider": "groq",
            }
        ],
    )

    client = CassetteLLMClient(mode="mock", cassette_path=cassette_path)
    result = await client.complete(prompt)
    assert result == "mocked!"


@pytest.mark.asyncio
async def test_mock_mode_raises_on_cache_miss(cassette_dir):
    cassette_path = cassette_dir / "empty.json"
    _write_cassette(cassette_path, [])

    client = CassetteLLMClient(mode="mock", cassette_path=cassette_path)
    with pytest.raises(RuntimeError, match="no entry"):
        await client.complete("unknown prompt")


@pytest.mark.asyncio
async def test_record_mode_saves_response(cassette_dir, monkeypatch):
    cassette_path = cassette_dir / "record.json"
    cassette_path.parent.mkdir(parents=True, exist_ok=True)

    class FakeClient:
        last_provider = "groq"

        async def complete(self, prompt, system_prompt=None, temperature=None, max_tokens=256):
            return "recorded answer"

    client = CassetteLLMClient(mode="record", cassette_path=cassette_path, real_client=FakeClient())
    result = await client.complete("record this")
    assert result == "recorded answer"

    client.save()
    data = json.loads(cassette_path.read_text())
    assert len(data["calls"]) == 1
    assert data["calls"][0]["response"] == "recorded answer"


@pytest.mark.asyncio
async def test_mock_mode_repeated_same_prompt(cassette_dir):
    prompt = "repeat me"
    h = _hash_prompt(prompt)
    cassette_path = cassette_dir / "repeat.json"
    _write_cassette(
        cassette_path,
        [
            {"prompt_hash": h, "prompt_preview": prompt, "response": "first", "max_tokens": 256, "provider": "groq"},
            {"prompt_hash": h, "prompt_preview": prompt, "response": "second", "max_tokens": 256, "provider": "groq"},
        ],
    )

    client = CassetteLLMClient(mode="mock", cassette_path=cassette_path)
    r1 = await client.complete(prompt)
    r2 = await client.complete(prompt)
    assert r1 == "first"
    assert r2 == "second"


@pytest.mark.asyncio
async def test_patch_mode_replays_known_then_records_new(cassette_dir):
    prompt_known = "known prompt"
    h = _hash_prompt(prompt_known)
    cassette_path = cassette_dir / "patch.json"
    _write_cassette(
        cassette_path,
        [
            {
                "prompt_hash": h,
                "prompt_preview": prompt_known,
                "response": "known!",
                "max_tokens": 256,
                "provider": "groq",
            }
        ],
    )

    class FakeClient:
        last_provider = "groq"

        async def complete(self, prompt, system_prompt=None, temperature=None, max_tokens=256):
            return "new answer"

    client = CassetteLLMClient(mode="patch", cassette_path=cassette_path, real_client=FakeClient())
    r_known = await client.complete(prompt_known)
    r_new = await client.complete("brand new prompt")
    assert r_known == "known!"
    assert r_new == "new answer"
