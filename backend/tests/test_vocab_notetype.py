"""Tests for app.cards.vocab_notetype (TT-managed vocab notetype descriptors)."""

from __future__ import annotations

import sqlite3

from app.cards.vocab_notetype import (
    NORWEGIAN_VOCAB,
    SLOVENE_VOCAB,
    build_field_config,
    build_notetype_config,
    build_template_config,
    create_vocab_notetype,
)


def test_slovene_field_order_unchanged():
    # The Slovene write path must stay byte-identical — field order is load-bearing.
    assert SLOVENE_VOCAB.field_names == ("Slovene", "English", "Audio", "Image", "Grammar", "Note", "DisambigKey")
    assert SLOVENE_VOCAB.l2_field == "Slovene"


def test_norwegian_field_order_mirrors_slovene_with_norwegian_l2():
    assert NORWEGIAN_VOCAB.field_names == ("Norwegian", "English", "Audio", "Image", "Grammar", "Note", "DisambigKey")
    assert NORWEGIAN_VOCAB.l2_field == "Norwegian"
    assert NORWEGIAN_VOCAB.l2_css_class == "norwegian"


def test_builders_emit_nonempty_bytes_containing_payload():
    fc = build_field_config()
    assert isinstance(fc, bytes) and len(fc) > 0
    tc = build_template_config("Q", "A")
    assert b"Q" in tc and b"A" in tc
    nc = build_notetype_config("body { color: red; }")
    assert b"color: red" in nc


def _make_notetype_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE notetypes (id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER, usn INTEGER, config BLOB);
        CREATE TABLE fields (ntid INTEGER, ord INTEGER, name TEXT, config BLOB, PRIMARY KEY (ntid, ord));
        CREATE TABLE templates (ntid INTEGER, ord INTEGER, name TEXT, mtime_secs INTEGER, usn INTEGER, config BLOB,
            PRIMARY KEY (ntid, ord));
    """)
    return conn


def test_create_vocab_notetype_inserts_notetype_fields_templates():
    conn = _make_notetype_conn()
    create_vocab_notetype(conn, NORWEGIAN_VOCAB, mid=1700000000000, now_ts=1700000000)

    nt = conn.execute("SELECT name, usn FROM notetypes WHERE id = 1700000000000").fetchone()
    assert nt["name"] == "Norwegian Vocabulary"
    assert nt["usn"] == -1

    fields = conn.execute("SELECT name FROM fields WHERE ntid = 1700000000000 ORDER BY ord").fetchall()
    assert [r["name"] for r in fields] == list(NORWEGIAN_VOCAB.field_names)

    templates = conn.execute("SELECT name, usn FROM templates WHERE ntid = 1700000000000 ORDER BY ord").fetchall()
    assert [r["name"] for r in templates] == ["Recognition", "Production"]
    assert all(r["usn"] == -1 for r in templates)


def test_create_vocab_notetype_templates_reference_l2_field():
    conn = _make_notetype_conn()
    create_vocab_notetype(conn, NORWEGIAN_VOCAB, mid=1700000000000, now_ts=1700000000)
    configs = conn.execute("SELECT config FROM templates WHERE ntid = 1700000000000 ORDER BY ord").fetchall()
    recognition_blob = bytes(configs[0]["config"])
    # The Recognition front references the L2 field + its CSS class.
    assert b"{{Norwegian}}" in recognition_blob
    assert b"norwegian" in recognition_blob
