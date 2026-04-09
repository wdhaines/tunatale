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


async def test_mock_mode_raises_on_cache_miss(cassette_dir):
    cassette_path = cassette_dir / "empty.json"
    _write_cassette(cassette_path, [])

    client = CassetteLLMClient(mode="mock", cassette_path=cassette_path)
    with pytest.raises(RuntimeError, match="no entry"):
        await client.complete("unknown prompt")


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


async def test_replay_raises_when_entry_used_more_times_than_recorded(cassette_dir):
    """Using the same prompt more times than recorded entries → RuntimeError."""
    prompt = "used twice"
    h = _hash_prompt(prompt)
    cassette_path = cassette_dir / "once.json"
    _write_cassette(
        cassette_path,
        [{"prompt_hash": h, "prompt_preview": prompt, "response": "once", "max_tokens": 256, "provider": "groq"}],
    )

    client = CassetteLLMClient(mode="mock", cassette_path=cassette_path)
    await client.complete(prompt)  # first use — OK
    with pytest.raises(RuntimeError, match="used"):
        await client.complete(prompt)  # second use — exceeds the 1 recorded entry


def test_save_is_noop_for_mock_mode(cassette_dir, tmp_path):
    """Calling save() in mock mode does nothing (no file written)."""
    cassette_path = cassette_dir / "mock.json"
    _write_cassette(cassette_path, [])

    client = CassetteLLMClient(mode="mock", cassette_path=cassette_path)
    output = tmp_path / "should_not_exist.json"
    client._cassette_path = output  # redirect to a path that doesn't exist yet
    client.save()  # should be a no-op
    assert not output.exists()


async def test_live_mode_calls_real_client_without_saving(tmp_path):
    """live mode calls real client and returns response without writing a cassette (73->84)."""

    class FakeClient:
        last_provider = "groq"

        async def complete(self, prompt, system_prompt=None, temperature=None, max_tokens=256):
            return "live answer"

    cassette_path = tmp_path / "cassettes" / "live.json"
    client = CassetteLLMClient(mode="live", cassette_path=cassette_path, real_client=FakeClient())
    result = await client.complete("live prompt")
    assert result == "live answer"
    assert not cassette_path.exists()  # live mode never writes


async def test_patch_mode_calls_real_when_entries_exhausted(cassette_dir):
    """In patch mode, exhausting recorded entries falls through to real client (108->114)."""
    prompt = "exhausted"
    h = _hash_prompt(prompt)
    cassette_path = cassette_dir / "exhausted.json"
    _write_cassette(
        cassette_path,
        [{"prompt_hash": h, "prompt_preview": prompt, "response": "first", "max_tokens": 256, "provider": "groq"}],
    )

    class FakeClient:
        last_provider = "groq"

        async def complete(self, prompt, **kwargs):
            return "real answer"

    client = CassetteLLMClient(mode="patch", cassette_path=cassette_path, real_client=FakeClient())
    r1 = await client.complete(prompt)  # first use — replays from cassette
    r2 = await client.complete(prompt)  # second use — cassette exhausted → calls real client
    assert r1 == "first"
    assert r2 == "real answer"
