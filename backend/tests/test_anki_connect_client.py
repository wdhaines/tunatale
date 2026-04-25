"""Tests for AnkiConnect JSON-RPC client."""

import json

import httpx
import pytest

from app.anki.anki_connect import (
    AnkiConnectClient,
    AnkiConnectError,
    AnkiConnectUnavailable,
)

# --- Test transport helpers ---


class FakeTransport(httpx.BaseTransport):
    def __init__(self, response_factory):
        self._response_factory = response_factory
        self.last_request = None

    def handle_request(self, request):
        self.last_request = request
        return self._response_factory(request)


def success_transport(result):
    def handler(request):
        return httpx.Response(200, json={"result": result, "error": None})

    return FakeTransport(handler)


def error_transport(error_msg):
    def handler(request):
        return httpx.Response(200, json={"result": None, "error": error_msg})

    return FakeTransport(handler)


class RefusedTransport(httpx.BaseTransport):
    def handle_request(self, request):
        raise httpx.ConnectError("Connection refused")


def _body(transport: FakeTransport) -> dict:
    return json.loads(transport.last_request.content)


# --- Core protocol ---


def test_invoke_returns_result_on_success():
    transport = success_transport(42)
    client = AnkiConnectClient(http_client=httpx.Client(transport=transport))
    assert client.invoke("version") == 42


def test_invoke_raises_on_error_field():
    transport = error_transport("collection is not available")
    client = AnkiConnectClient(http_client=httpx.Client(transport=transport))
    with pytest.raises(AnkiConnectError, match="collection is not available"):
        client.invoke("version")


def test_connection_refused_raises_unavailable():
    client = AnkiConnectClient(http_client=httpx.Client(transport=RefusedTransport()))
    with pytest.raises(AnkiConnectUnavailable):
        client.invoke("version")


def test_ping_returns_version_int():
    transport = success_transport(6)
    client = AnkiConnectClient(http_client=httpx.Client(transport=transport))
    assert client.ping() == 6


def test_api_reflect_lists_actions():
    actions = ["version", "deckNames", "findNotes"]
    transport = success_transport({"scopes": [], "actions": actions})
    client = AnkiConnectClient(http_client=httpx.Client(transport=transport))
    assert client.api_reflect() == actions


def test_missing_set_specific_value_of_card_detected():
    actions = ["version", "findNotes", "notesInfo"]  # setSpecificValueOfCard absent
    transport = success_transport({"scopes": [], "actions": actions})
    client = AnkiConnectClient(http_client=httpx.Client(transport=transport))
    assert "setSpecificValueOfCard" not in client.api_reflect()


# --- Read wrapper envelope shapes ---


def test_deck_names_sends_correct_action():
    transport = success_transport(["0. Slovene"])
    client = AnkiConnectClient(http_client=httpx.Client(transport=transport))
    client.deck_names()
    assert _body(transport)["action"] == "deckNames"


def test_find_notes_sends_query():
    transport = success_transport([1, 2, 3])
    client = AnkiConnectClient(http_client=httpx.Client(transport=transport))
    client.find_notes("deck:0. Slovene")
    body = _body(transport)
    assert body["action"] == "findNotes"
    assert body["params"]["query"] == "deck:0. Slovene"


def test_notes_info_sends_note_ids():
    transport = success_transport([])
    client = AnkiConnectClient(http_client=httpx.Client(transport=transport))
    client.notes_info([10, 20])
    body = _body(transport)
    assert body["action"] == "notesInfo"
    assert body["params"]["notes"] == [10, 20]


def test_find_cards_sends_query():
    transport = success_transport([5, 6])
    client = AnkiConnectClient(http_client=httpx.Client(transport=transport))
    client.find_cards("deck:0. Slovene")
    body = _body(transport)
    assert body["action"] == "findCards"
    assert body["params"]["query"] == "deck:0. Slovene"


def test_cards_info_sends_card_ids():
    transport = success_transport([])
    client = AnkiConnectClient(http_client=httpx.Client(transport=transport))
    client.cards_info([7, 8])
    body = _body(transport)
    assert body["action"] == "cardsInfo"
    assert body["params"]["cards"] == [7, 8]


def test_get_model_field_names_sends_model_name():
    transport = success_transport(["Front", "Back"])
    client = AnkiConnectClient(http_client=httpx.Client(transport=transport))
    client.get_model_field_names("Basic")
    body = _body(transport)
    assert body["action"] == "getModelFieldNames"
    assert body["params"]["modelName"] == "Basic"


# --- Write wrapper envelope shapes ---


def test_update_note_fields_sends_correct_envelope():
    transport = success_transport(None)
    client = AnkiConnectClient(http_client=httpx.Client(transport=transport))
    client.update_note_fields(note_id=123, fields={"Front": "hello"})
    body = _body(transport)
    assert body["action"] == "updateNoteFields"
    assert body["params"]["note"]["id"] == 123
    assert body["params"]["note"]["fields"] == {"Front": "hello"}


def test_add_note_sends_correct_envelope():
    transport = success_transport(1001)
    client = AnkiConnectClient(http_client=httpx.Client(transport=transport))
    note = {"deckName": "0. Slovene", "modelName": "Basic", "fields": {}, "tags": []}
    client.add_note(note)
    body = _body(transport)
    assert body["action"] == "addNote"
    assert body["params"]["note"] == note


def test_add_tags_sends_correct_envelope():
    transport = success_transport(None)
    client = AnkiConnectClient(http_client=httpx.Client(transport=transport))
    client.add_tags(note_ids=[1, 2], tags="tt-import")
    body = _body(transport)
    assert body["action"] == "addTags"
    assert body["params"]["notes"] == [1, 2]
    assert body["params"]["tags"] == "tt-import"


def test_remove_tags_sends_correct_envelope():
    transport = success_transport(None)
    client = AnkiConnectClient(http_client=httpx.Client(transport=transport))
    client.remove_tags(note_ids=[3], tags="old-tag")
    body = _body(transport)
    assert body["action"] == "removeTags"
    assert body["params"]["notes"] == [3]
    assert body["params"]["tags"] == "old-tag"


def test_set_due_date_sends_correct_envelope():
    transport = success_transport(None)
    client = AnkiConnectClient(http_client=httpx.Client(transport=transport))
    client.set_due_date(card_ids=[42], due="5")
    body = _body(transport)
    assert body["action"] == "setDueDate"
    assert body["params"]["cards"] == [42]
    assert body["params"]["days"] == "5"


def test_suspend_sends_correct_envelope():
    transport = success_transport(True)
    client = AnkiConnectClient(http_client=httpx.Client(transport=transport))
    client.suspend(card_ids=[99])
    body = _body(transport)
    assert body["action"] == "suspend"
    assert body["params"]["cards"] == [99]


def test_unsuspend_sends_correct_envelope():
    transport = success_transport(True)
    client = AnkiConnectClient(http_client=httpx.Client(transport=transport))
    client.unsuspend(card_ids=[88])
    body = _body(transport)
    assert body["action"] == "unsuspend"
    assert body["params"]["cards"] == [88]


def test_forget_cards_sends_correct_envelope():
    transport = success_transport(None)
    client = AnkiConnectClient(http_client=httpx.Client(transport=transport))
    client.forget_cards(card_ids=[55])
    body = _body(transport)
    assert body["action"] == "forgetCards"
    assert body["params"]["cards"] == [55]


def test_store_media_file_sends_correct_envelope():
    transport = success_transport("audio.mp3")
    client = AnkiConnectClient(http_client=httpx.Client(transport=transport))
    client.store_media_file(filename="audio.mp3", data="base64data==")
    body = _body(transport)
    assert body["action"] == "storeMediaFile"
    assert body["params"]["filename"] == "audio.mp3"
    assert body["params"]["data"] == "base64data=="


def test_set_specific_value_of_card_sends_correct_envelope():
    transport = success_transport([True])
    client = AnkiConnectClient(http_client=httpx.Client(transport=transport))
    client.set_specific_value_of_card(card_id=77, keys=["due", "ivl"], newValues=["3", "10"])
    body = _body(transport)
    assert body["action"] == "setSpecificValueOfCard"
    assert body["params"]["card"] == 77
    assert body["params"]["keys"] == ["due", "ivl"]
    assert body["params"]["newValues"] == ["3", "10"]


def test_get_deck_config_sends_correct_action_and_returns_config():
    config = {"id": 1, "name": "Default", "new": {"perDay": 30}, "rev": {"perDay": 200}}
    transport = success_transport(config)
    client = AnkiConnectClient(http_client=httpx.Client(transport=transport))
    result = client.get_deck_config("0. Slovene")
    body = _body(transport)
    assert body["action"] == "getDeckConfig"
    assert body["params"]["deck"] == "0. Slovene"
    assert result["new"]["perDay"] == 30


def test_get_deck_config_raises_unavailable_when_refused():
    client = AnkiConnectClient(http_client=httpx.Client(transport=RefusedTransport()))
    with pytest.raises(AnkiConnectUnavailable):
        client.get_deck_config("0. Slovene")
