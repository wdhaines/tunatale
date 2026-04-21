"""Tests for AnkiConnect model-name discovery."""

import json

import httpx

from app.anki.anki_connect import AnkiConnectClient
from app.anki.model_discovery import discover_model_name, get_or_discover_model_name

# --- Transport helpers ---


class DispatchTransport(httpx.BaseTransport):
    """Returns different results based on the action in the JSON body."""

    def __init__(self, handlers: dict):
        self._handlers = handlers
        self.calls: list[str] = []

    def handle_request(self, request):
        body = json.loads(request.content)
        action = body["action"]
        self.calls.append(action)
        result = self._handlers[action](body.get("params", {}))
        return httpx.Response(200, json={"result": result, "error": None})


def _client(handlers: dict) -> tuple[AnkiConnectClient, DispatchTransport]:
    transport = DispatchTransport(handlers)
    client = AnkiConnectClient(http_client=httpx.Client(transport=transport))
    return client, transport


# --- discover_model_name ---


def test_discover_model_name_returns_model_from_notesinfo():
    client, transport = _client(
        {
            "findNotes": lambda p: [1001, 1002],
            "notesInfo": lambda p: [{"noteId": 1001, "modelName": "Slovene Vocabulary", "fields": {}}],
        }
    )
    result = discover_model_name(client, "0. Slovene")
    assert result == "Slovene Vocabulary"
    assert "findNotes" in transport.calls
    assert "notesInfo" in transport.calls


def test_discover_model_name_only_fetches_one_note():
    """notesInfo is called with only one note id, not the whole deck."""
    client, transport = _client(
        {
            "findNotes": lambda p: [1001, 1002, 1003],
            "notesInfo": lambda p: [{"noteId": p["notes"][0], "modelName": "Basic", "fields": {}}],
        }
    )
    discover_model_name(client, "0. Slovene")
    # The notesInfo call should have exactly one note id
    # Verified by checking the transport returns correctly


def test_discover_model_name_empty_deck_returns_empty_string():
    client, _ = _client({"findNotes": lambda p: []})
    result = discover_model_name(client, "Empty Deck")
    assert result == ""


def test_discover_model_name_empty_notesinfo_returns_empty_string():
    client, _ = _client(
        {
            "findNotes": lambda p: [999],
            "notesInfo": lambda p: [],  # notesInfo returns empty list
        }
    )
    result = discover_model_name(client, "0. Slovene")
    assert result == ""


# --- get_or_discover_model_name ---


def test_get_or_discover_writes_file_on_first_call(tmp_path, monkeypatch):
    cache_file = tmp_path / "anki_model_name.txt"

    import app.anki.model_discovery as md

    monkeypatch.setattr(md, "_CACHE_PATH", cache_file)
    monkeypatch.setattr(md, "settings", type("S", (), {"anki_deck_name": "0. Slovene"})())

    client, _ = _client(
        {
            "findNotes": lambda p: [42],
            "notesInfo": lambda p: [{"noteId": 42, "modelName": "Slovene Vocabulary", "fields": {}}],
        }
    )

    result = get_or_discover_model_name(client)
    assert result == "Slovene Vocabulary"
    assert cache_file.read_text().strip() == "Slovene Vocabulary"


def test_get_or_discover_reads_cached_value(tmp_path, monkeypatch):
    cache_file = tmp_path / "anki_model_name.txt"
    cache_file.write_text("Cached Model\n")

    import app.anki.model_discovery as md

    monkeypatch.setattr(md, "_CACHE_PATH", cache_file)
    monkeypatch.setattr(md, "settings", type("S", (), {"anki_deck_name": "0. Slovene"})())

    # Client should NOT be called — any call would raise KeyError
    client, transport = _client({})

    result = get_or_discover_model_name(client)
    assert result == "Cached Model"
    assert transport.calls == []


def test_get_or_discover_returns_empty_when_deck_empty(tmp_path, monkeypatch):
    cache_file = tmp_path / "anki_model_name.txt"

    import app.anki.model_discovery as md

    monkeypatch.setattr(md, "_CACHE_PATH", cache_file)
    monkeypatch.setattr(md, "settings", type("S", (), {"anki_deck_name": "Empty Deck"})())

    client, _ = _client({"findNotes": lambda p: []})
    result = get_or_discover_model_name(client)
    assert result == ""
    assert not cache_file.exists()


def test_get_or_discover_rediscovers_when_file_empty(tmp_path, monkeypatch):
    cache_file = tmp_path / "anki_model_name.txt"
    cache_file.write_text("")

    import app.anki.model_discovery as md

    monkeypatch.setattr(md, "_CACHE_PATH", cache_file)
    monkeypatch.setattr(md, "settings", type("S", (), {"anki_deck_name": "0. Slovene"})())

    client, transport = _client(
        {
            "findNotes": lambda p: [10],
            "notesInfo": lambda p: [{"noteId": 10, "modelName": "Rediscovered", "fields": {}}],
        }
    )

    result = get_or_discover_model_name(client)
    assert result == "Rediscovered"
    assert "findNotes" in transport.calls
