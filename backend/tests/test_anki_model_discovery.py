"""Tests for offline (collection.anki2) model-name discovery."""


def _make_offline_conn_with_model(model_name: str, deck_name: str = "0. Slovene") -> object:
    """Build a minimal in-memory DB with one note linked to the given model."""
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE col (id INTEGER, crt INTEGER, mod INTEGER, scm INTEGER, ver INTEGER,
            dty INTEGER, usn INTEGER, ls INTEGER, conf TEXT, models TEXT,
            decks TEXT, dconf TEXT, tags TEXT);
        CREATE TABLE notes (id INTEGER PRIMARY KEY, guid TEXT, mid INTEGER, mod INTEGER,
            usn INTEGER, tags TEXT, flds TEXT, sfld TEXT, csum INTEGER, flags INTEGER, data TEXT);
        CREATE TABLE cards (id INTEGER PRIMARY KEY, nid INTEGER, did INTEGER, ord INTEGER,
            mod INTEGER, usn INTEGER, type INTEGER, queue INTEGER, due INTEGER, ivl INTEGER,
            factor INTEGER, reps INTEGER, lapses INTEGER, left INTEGER,
            odue INTEGER, odid INTEGER, flags INTEGER, data TEXT);
        CREATE TABLE notetypes (id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER,
            usn INTEGER, config BLOB);
        CREATE TABLE decks (id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER,
            usn INTEGER, common BLOB);
    """)
    conn.execute("INSERT INTO col VALUES (1, 0, 0, 0, 18, 0, 0, 0, '{}', '{}', '{}', '{}', '{}')")
    conn.execute("INSERT INTO decks VALUES (12345, ?, 0, 0, x'')", (deck_name,))
    conn.execute("INSERT INTO notetypes VALUES (9001, ?, 0, 0, x'')", (model_name,))
    conn.execute("INSERT INTO notes VALUES (100, 'guid1', 9001, 0, 0, '', 'f\x1ft', 'f', 0, 0, '')")
    conn.execute("INSERT INTO cards VALUES (1000, 100, 12345, 0, 0, 0, 2, 2, 10, 7, 2500, 1, 0, 0, 0, 0, 0, '')")
    conn.commit()
    return conn


def test_get_or_discover_model_name_offline_returns_name(tmp_path, monkeypatch):
    import app.anki.model_discovery as md

    cache_file = tmp_path / "anki_model_name.txt"
    monkeypatch.setattr(md, "_CACHE_PATH", cache_file)

    conn = _make_offline_conn_with_model("Slovene Vocabulary")
    result = md.get_or_discover_model_name_offline(conn, "0. Slovene")
    assert result == "Slovene Vocabulary"


def test_get_or_discover_model_name_offline_writes_cache(tmp_path, monkeypatch):
    import app.anki.model_discovery as md

    cache_file = tmp_path / "anki_model_name.txt"
    monkeypatch.setattr(md, "_CACHE_PATH", cache_file)

    conn = _make_offline_conn_with_model("Slovene Vocabulary")
    md.get_or_discover_model_name_offline(conn, "0. Slovene")
    assert cache_file.read_text().strip() == "Slovene Vocabulary"


def test_get_or_discover_model_name_offline_uses_cache(tmp_path, monkeypatch):
    """Cache hit must not query the DB."""
    import app.anki.model_discovery as md

    cache_file = tmp_path / "anki_model_name.txt"
    cache_file.write_text("Cached Model\n")
    monkeypatch.setattr(md, "_CACHE_PATH", cache_file)

    # DB with no notes — would return "" if queried
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    result = md.get_or_discover_model_name_offline(conn, "0. Slovene")
    assert result == "Cached Model"


def test_get_or_discover_model_name_offline_returns_empty_when_deck_not_found(tmp_path, monkeypatch):
    import app.anki.model_discovery as md

    cache_file = tmp_path / "anki_model_name.txt"
    monkeypatch.setattr(md, "_CACHE_PATH", cache_file)

    conn = _make_offline_conn_with_model("Slovene Vocabulary")
    result = md.get_or_discover_model_name_offline(conn, "No Such Deck")
    assert result == ""


def test_get_or_discover_model_name_offline_returns_empty_when_no_notes(tmp_path, monkeypatch):
    """Deck exists but has no notes → return ''."""
    import app.anki.model_discovery as md

    cache_file = tmp_path / "anki_model_name.txt"
    monkeypatch.setattr(md, "_CACHE_PATH", cache_file)

    conn = _make_offline_conn_with_model("Slovene Vocabulary")
    # Delete all notes so the inner query returns nothing
    conn.execute("DELETE FROM cards")
    conn.commit()
    result = md.get_or_discover_model_name_offline(conn, "0. Slovene")
    assert result == ""


def test_get_or_discover_model_name_offline_cache_empty_falls_through(tmp_path, monkeypatch):
    """Cache file exists but is empty → must still query the DB."""
    import app.anki.model_discovery as md

    cache_file = tmp_path / "anki_model_name.txt"
    cache_file.write_text("")  # empty file
    monkeypatch.setattr(md, "_CACHE_PATH", cache_file)

    conn = _make_offline_conn_with_model("Slovene Vocabulary")
    result = md.get_or_discover_model_name_offline(conn, "0. Slovene")
    assert result == "Slovene Vocabulary"


def test_get_or_discover_model_name_offline_empty_name_no_cache(tmp_path, monkeypatch):
    """Notetype exists but has empty name → return '' without writing cache."""
    import app.anki.model_discovery as md

    cache_file = tmp_path / "anki_model_name.txt"
    monkeypatch.setattr(md, "_CACHE_PATH", cache_file)

    conn = _make_offline_conn_with_model("")  # empty model name
    result = md.get_or_discover_model_name_offline(conn, "0. Slovene")
    assert result == ""
    assert not cache_file.exists()
