"""AnkiConnect JSON-RPC client."""

from __future__ import annotations

from typing import Any

import httpx


class AnkiConnectError(Exception):
    """AnkiConnect returned a non-null error field."""


class AnkiConnectUnavailable(Exception):
    """AnkiConnect is not reachable (connection refused)."""


class AnkiConnectClient:
    def __init__(
        self,
        url: str = "http://127.0.0.1:8765",
        http_client: httpx.Client | None = None,
    ) -> None:
        self._url = url
        self._http = http_client or httpx.Client()

    def invoke(self, action: str, **params: Any) -> Any:
        payload: dict[str, Any] = {"action": action, "version": 6}
        if params:
            payload["params"] = params
        try:
            response = self._http.post(self._url, json=payload)
        except httpx.ConnectError as exc:
            raise AnkiConnectUnavailable(str(exc)) from exc
        data = response.json()
        if data.get("error") is not None:
            raise AnkiConnectError(data["error"])
        return data["result"]

    # --- Convenience wrappers ---

    def ping(self) -> int:
        return self.invoke("version")

    def api_reflect(self) -> list[str]:
        result = self.invoke("apiReflect", scopes=["actions"], actions=[])
        return result.get("actions", [])

    # Read actions

    def deck_names(self) -> list[str]:
        return self.invoke("deckNames")

    def find_notes(self, query: str) -> list[int]:
        return self.invoke("findNotes", query=query)

    def notes_info(self, notes: list[int]) -> list[dict]:
        return self.invoke("notesInfo", notes=notes)

    def find_cards(self, query: str) -> list[int]:
        return self.invoke("findCards", query=query)

    def cards_info(self, cards: list[int]) -> list[dict]:
        return self.invoke("cardsInfo", cards=cards)

    def get_model_field_names(self, model_name: str) -> list[str]:
        return self.invoke("getModelFieldNames", modelName=model_name)

    # Write actions

    def update_note_fields(self, note_id: int, fields: dict[str, str]) -> None:
        self.invoke("updateNoteFields", note={"id": note_id, "fields": fields})

    def add_note(self, note: dict) -> int:
        return self.invoke("addNote", note=note)

    def add_tags(self, note_ids: list[int], tags: str) -> None:
        self.invoke("addTags", notes=note_ids, tags=tags)

    def remove_tags(self, note_ids: list[int], tags: str) -> None:
        self.invoke("removeTags", notes=note_ids, tags=tags)

    def set_due_date(self, card_ids: list[int], due: str) -> None:
        self.invoke("setDueDate", cards=card_ids, days=due)

    def suspend(self, card_ids: list[int]) -> bool:
        return self.invoke("suspend", cards=card_ids)

    def unsuspend(self, card_ids: list[int]) -> bool:
        return self.invoke("unsuspend", cards=card_ids)

    def forget_cards(self, card_ids: list[int]) -> None:
        self.invoke("forgetCards", cards=card_ids)

    def store_media_file(self, filename: str, data: str) -> str:
        return self.invoke("storeMediaFile", filename=filename, data=data)

    def set_specific_value_of_card(self, card_id: int, keys: list[str], newValues: list[str]) -> list[bool]:
        return self.invoke("setSpecificValueOfCard", card=card_id, keys=keys, newValues=newValues)

    def get_deck_config(self, deck: str) -> dict:
        return self.invoke("getDeckConfig", deck=deck)
