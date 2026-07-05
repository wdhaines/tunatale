"""Cassette system tests."""

import json
from pathlib import Path

import pytest

from app.llm.cassette import CASSETTE_VERSION, CassetteLLMClient, _hash_prompt


def _write_cassette(path: Path, calls: list[dict], version: int | None = CASSETTE_VERSION) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict = {"recorded_at": "2026-01-01T00:00:00+00:00", "calls": calls}
    if version is not None:
        payload["version"] = version
    path.write_text(json.dumps(payload))


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


def test_hash_prompt_includes_system_prompt():
    """Same user prompt + different system prompts must NOT collide.

    Before this, editing a system prompt left the cassette hash unchanged, so
    mock-mode tests kept replaying responses recorded under stale instructions.
    """
    prompt = "identical user prompt"
    h_none = _hash_prompt(prompt)
    h_a = _hash_prompt(prompt, "system A")
    h_b = _hash_prompt(prompt, "system B")
    # Three distinct system-prompt contexts → three distinct hashes.
    assert len({h_none, h_a, h_b}) == 3
    # A None system prompt and an empty-string system prompt collapse together
    # (both mean "no system instructions").
    assert _hash_prompt(prompt, None) == _hash_prompt(prompt, "")


async def test_version_mismatch_raises_on_load(cassette_dir):
    """Loading a cassette whose version != CASSETTE_VERSION fails loudly with a hint."""
    cassette_path = cassette_dir / "stale.json"
    # A pre-versioning cassette (no "version" key) is treated as a mismatch.
    _write_cassette(cassette_path, [], version=None)
    with pytest.raises(RuntimeError, match="(?i)re-record"):
        CassetteLLMClient(mode="mock", cassette_path=cassette_path)

    # An explicitly wrong version also fails.
    _write_cassette(cassette_path, [], version=CASSETTE_VERSION - 1)
    with pytest.raises(RuntimeError, match=str(CASSETTE_VERSION)):
        CassetteLLMClient(mode="patch", cassette_path=cassette_path)


async def test_saved_cassette_carries_current_version(cassette_dir):
    """record mode stamps the current version into the saved JSON."""

    class FakeClient:
        last_provider = "groq"

        async def complete(self, prompt, system_prompt=None, temperature=None, max_tokens=256):
            return "recorded"

    cassette_path = cassette_dir / "versioned.json"
    client = CassetteLLMClient(mode="record", cassette_path=cassette_path, real_client=FakeClient())
    await client.complete("hello", system_prompt="be terse")
    data = json.loads(cassette_path.read_text())
    assert data["version"] == CASSETTE_VERSION
    # The recorded hash reflects the system prompt (round-trips through _hash_prompt).
    assert data["calls"][0]["prompt_hash"] == _hash_prompt("hello", "be terse")


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
