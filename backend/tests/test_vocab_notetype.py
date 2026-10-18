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


# ── Dictionary layers (tunatale-h0q3.2) ─────────────────────────────────────


def _templates(conn: sqlite3.Connection, mid: int) -> list[tuple[str, str]]:
    from app.srs.anki_mirror.protobuf_wire import find_len_field

    rows = conn.execute("SELECT config FROM templates WHERE ntid = ? ORDER BY ord", (mid,)).fetchall()
    return [(find_len_field(bytes(r[0]), 1).decode(), find_len_field(bytes(r[0]), 2).decode()) for r in rows]


def test_cebuano_carries_the_dictionary_fields_after_the_shared_roster():
    from app.cards.vocab_notetype import CEBUANO_VOCAB, DICTIONARY_FIELDS

    assert DICTIONARY_FIELDS == ("Spelling", "Meaning", "Verb", "Details", "Dictionary entry")
    assert CEBUANO_VOCAB.field_names == (
        "Cebuano",
        "English",
        "Audio",
        "Image",
        "Grammar",
        "Note",
        "DisambigKey",
        *DICTIONARY_FIELDS,
    )


def test_the_other_languages_rosters_do_not_move():
    from app.cards.vocab_notetype import TAGALOG_VOCAB

    for vocab in (SLOVENE_VOCAB, NORWEGIAN_VOCAB, TAGALOG_VOCAB):
        assert len(vocab.field_names) == 7
        assert not vocab.dictionary


def test_the_ceb_card_renderer_writes_exactly_the_notetypes_fields():
    from app.cards.vocab_notetype import DICTIONARY_FIELDS
    from app.plugins.languages.ceb.wolff_cards import FIELD_NAMES

    assert FIELD_NAMES == DICTIONARY_FIELDS


def test_spelling_sits_under_the_word_on_the_recognition_front_and_the_production_back():
    from app.cards.vocab_notetype import CEBUANO_VOCAB, SPELLING_BLOCK

    conn = _make_notetype_conn()
    create_vocab_notetype(conn, CEBUANO_VOCAB, mid=1, now_ts=1)
    (rq, ra), (pq, pa) = _templates(conn, 1)
    word = '<div class="cebuano">{{Cebuano}}</div>'
    assert rq == "{{Audio}}" + word + SPELLING_BLOCK
    assert word + SPELLING_BLOCK in pa
    # The recognition back repeats its front through {{FrontSide}}, and the
    # production front is the picture: neither gets a second copy.
    assert SPELLING_BLOCK not in ra
    assert SPELLING_BLOCK not in pq


def test_both_backs_end_with_the_layers_and_the_fronts_never_show_them():
    from app.cards.vocab_notetype import CEBUANO_VOCAB, DICTIONARY_BLOCK

    conn = _make_notetype_conn()
    create_vocab_notetype(conn, CEBUANO_VOCAB, mid=1, now_ts=1)
    (rq, ra), (pq, pa) = _templates(conn, 1)
    assert ra.endswith(DICTIONARY_BLOCK)
    assert pa.endswith(DICTIONARY_BLOCK)
    assert "{{Meaning}}" not in rq + pq


def test_the_layers_are_conditional_and_the_entry_is_nested_inside_details():
    from app.cards.vocab_notetype import DICTIONARY_BLOCK, SPELLING_BLOCK

    assert SPELLING_BLOCK.startswith("{{#Spelling}}") and SPELLING_BLOCK.endswith("{{/Spelling}}")
    for name in ("Meaning", "Verb", "Dictionary entry"):
        assert "{{#" + name + "}}" in DICTIONARY_BLOCK and "{{/" + name + "}}" in DICTIONARY_BLOCK
    details = DICTIONARY_BLOCK.index("<summary>Details</summary>")
    entry = DICTIONARY_BLOCK.index("<summary>Dictionary entry</summary>")
    # Progressive disclosure: the entry opens from inside Details, as on the
    # Norwegian cards, and Details closes after it.
    assert details < DICTIONARY_BLOCK.index("{{Details}}") < entry < DICTIONARY_BLOCK.rindex("</details></details>")


def test_a_plain_notetype_gets_no_layers():
    from app.cards.vocab_notetype import DICTIONARY_BLOCK, SPELLING_BLOCK

    conn = _make_notetype_conn()
    create_vocab_notetype(conn, NORWEGIAN_VOCAB, mid=1, now_ts=1)
    text = "".join(q + a for q, a in _templates(conn, 1))
    assert SPELLING_BLOCK not in text and DICTIONARY_BLOCK not in text


def test_dictionary_css_is_added_only_with_the_layers():
    from app.cards.vocab_notetype import CEBUANO_VOCAB, DICTIONARY_CSS
    from app.srs.anki_mirror.protobuf_wire import find_len_field

    conn = _make_notetype_conn()
    create_vocab_notetype(conn, CEBUANO_VOCAB, mid=1, now_ts=1)
    create_vocab_notetype(conn, NORWEGIAN_VOCAB, mid=2, now_ts=1)
    css = {r[0]: find_len_field(bytes(r[1]), 3).decode() for r in conn.execute("SELECT id, config FROM notetypes")}
    assert css[1].endswith(DICTIONARY_CSS)
    assert DICTIONARY_CSS not in css[2]


def test_with_dictionary_refuses_a_template_without_exactly_one_word_div():
    import pytest

    from app.cards.vocab_notetype import CEBUANO_VOCAB, with_dictionary

    with pytest.raises(ValueError, match="word"):
        with_dictionary(CEBUANO_VOCAB, "{{Audio}}{{Cebuano}}", "{{FrontSide}}", recognition=True)
    word = '<div class="cebuano">{{Cebuano}}</div>'
    with pytest.raises(ValueError, match="word"):
        with_dictionary(CEBUANO_VOCAB, "{{Image}}", word + word, recognition=False)
