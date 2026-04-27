"""AnkiConnect model-name discovery with file-based cache."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.anki.anki_connect import AnkiConnectClient
from app.config import settings

_CACHE_PATH = Path("~/.tunatale/anki_model_name.txt").expanduser()


def discover_model_name(client: AnkiConnectClient, deck_name: str) -> str:
    """Return the modelName of any note in deck_name, or '' if the deck is empty."""
    note_ids = client.find_notes(f'deck:"{deck_name}"')
    if not note_ids:
        return ""
    info = client.notes_info([note_ids[0]])
    if not info:
        return ""
    return info[0].get("modelName", "")


def get_or_discover_model_name(client: AnkiConnectClient) -> str:
    """Return cached model name, re-discovering via AnkiConnect if cache is empty."""
    if _CACHE_PATH.exists():
        cached = _CACHE_PATH.read_text().strip()
        if cached:
            return cached
    name = discover_model_name(client, settings.anki_deck_name)
    if name:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_PATH.write_text(name + "\n")
    return name


def get_or_discover_model_name_offline(conn: sqlite3.Connection, deck_name: str) -> str:
    """Return model name from the notetypes table, falling back to file cache.

    Reads the name of the notetype used by notes in `deck_name`.  Caches the
    result in the same file as the online path so the two paths share the cache.
    Returns '' when the deck doesn't exist or has no notes.
    """
    if _CACHE_PATH.exists():
        cached = _CACHE_PATH.read_text().strip()
        if cached:
            return cached

    from app.anki.sqlite_reader import find_deck_id

    deck_id = find_deck_id(conn, deck_name)
    if deck_id is None:
        return ""

    row = conn.execute(
        "SELECT name FROM notetypes WHERE id IN "
        "(SELECT DISTINCT mid FROM notes WHERE id IN "
        "(SELECT nid FROM cards WHERE did = ?)) LIMIT 1",
        (deck_id,),
    ).fetchone()
    if row is None:
        return ""

    name = row[0] if isinstance(row, (tuple, list)) else row["name"]
    if name:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_PATH.write_text(name + "\n")
    return name
