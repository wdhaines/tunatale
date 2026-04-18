"""Tests for ``app.anki.notetype`` helpers that build the Slovene Vocabulary payload.

The helpers emit serialized protobuf blobs for ``notetypes.config``,
``fields.config``, and ``templates.config``. Anki tolerates sparse configs so the
blobs stay minimal — enough to satisfy ``PRAGMA integrity_check`` and enough to
render the cards on next open.
"""

from __future__ import annotations

import sqlite3

from app.anki.notetype import (
    SLOVENE_VOCAB_CSS,
    SLOVENE_VOCAB_FIELD_NAMES,
    SLOVENE_VOCAB_NOTETYPE_NAME,
    build_field_config,
    build_notetype_config,
    build_template_config,
    slovene_vocab_templates,
)


class TestFieldConfig:
    def test_field_config_is_bytes(self):
        cfg = build_field_config("Slovene")
        assert isinstance(cfg, bytes)
        assert len(cfg) > 0

    def test_field_config_contains_font_and_size(self):
        cfg = build_field_config("Slovene", font="Arial", font_size=20)
        # Tag 3 (font_name) wire 2 = 0x1a, then length 5, then "Arial"
        assert b"Arial" in cfg
        # Tag 4 (font_size) wire 0 = 0x20, then varint 20
        assert b"\x20\x14" in cfg

    def test_field_config_defaults_are_stable(self):
        cfg_a = build_field_config("Slovene")
        cfg_b = build_field_config("Slovene")
        assert cfg_a == cfg_b, "identical inputs must produce identical bytes"


class TestTemplateConfig:
    def test_template_config_is_bytes(self):
        cfg = build_template_config(qfmt="{{Slovene}}", afmt="{{Slovene}}")
        assert isinstance(cfg, bytes)
        assert len(cfg) > 0

    def test_template_config_embeds_qfmt_and_afmt(self):
        qfmt = '{{Audio}}<div class="slovene">{{Slovene}}</div>'
        afmt = '{{FrontSide}}<hr id="answer">{{English}}'
        cfg = build_template_config(qfmt=qfmt, afmt=afmt)
        assert qfmt.encode() in cfg
        assert afmt.encode() in cfg

    def test_template_config_handles_long_qfmt_with_multibyte_varint_length(self):
        """Template qfmt > 127 bytes requires a 2-byte varint length prefix."""
        qfmt = "x" * 200
        afmt = "y"
        cfg = build_template_config(qfmt=qfmt, afmt=afmt)
        assert qfmt.encode() in cfg
        assert afmt.encode() in cfg


class TestNotetypeConfig:
    def test_notetype_config_embeds_css(self):
        cfg = build_notetype_config(css="/*hello*/")
        assert b"/*hello*/" in cfg

    def test_notetype_config_includes_other_json_trailer(self):
        cfg = build_notetype_config(css=".card {}")
        # Tag 255 wire 2 = 0xfa 0x0f, then varint length, then JSON
        assert b"\xfa\x0f" in cfg
        assert b'"tags"' in cfg  # default "other" JSON


class TestSloveneVocabTemplates:
    def test_two_templates_recognition_and_production(self):
        tpls = slovene_vocab_templates()
        assert len(tpls) == 2
        ords = [t.ord for t in tpls]
        names = [t.name for t in tpls]
        assert ords == [0, 1]
        assert names == ["Recognition", "Production"]

    def test_recognition_shows_audio_front(self):
        tpls = slovene_vocab_templates()
        recognition = tpls[0]
        assert "{{Audio}}" in recognition.qfmt
        assert "{{Slovene}}" in recognition.qfmt
        assert "{{English}}" in recognition.afmt

    def test_production_shows_image_front_and_slovene_on_answer(self):
        tpls = slovene_vocab_templates()
        production = tpls[1]
        assert "{{Image}}" in production.qfmt
        # Image-only front — no Slovene or English on the question side
        assert "{{Slovene}}" not in production.qfmt
        assert "{{English}}" not in production.qfmt
        # Answer side reveals everything
        assert "{{Slovene}}" in production.afmt
        assert "{{Audio}}" in production.afmt
        assert "{{English}}" in production.afmt


class TestSloveneVocabConstants:
    def test_field_names_are_in_plan_order(self):
        assert SLOVENE_VOCAB_FIELD_NAMES == [
            "Slovene",
            "English",
            "Audio",
            "Image",
            "Grammar",
            "Note",
        ]

    def test_notetype_name_matches_plan(self):
        assert SLOVENE_VOCAB_NOTETYPE_NAME == "Slovene Vocabulary"

    def test_css_is_non_empty(self):
        assert SLOVENE_VOCAB_CSS.strip() != ""


class TestIntegrityAfterInsert:
    def _make_empty_db(self, tmp_path):
        db_path = tmp_path / "collection.anki2"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE notetypes (id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER, usn INTEGER, config BLOB)"
        )
        conn.execute("CREATE TABLE fields (ntid INTEGER, ord INTEGER, name TEXT, config BLOB, PRIMARY KEY (ntid, ord))")
        conn.execute(
            "CREATE TABLE templates (ntid INTEGER, ord INTEGER, name TEXT, mtime_secs INTEGER, usn INTEGER, config BLOB, PRIMARY KEY (ntid, ord))"
        )
        return conn

    def test_integrity_check_ok_after_inserting_notetype(self, tmp_path):
        from app.anki.notetype import slovene_vocab_fields

        conn = self._make_empty_db(tmp_path)
        mid = 999_000_111
        nt_config = build_notetype_config(css=SLOVENE_VOCAB_CSS)
        conn.execute(
            "INSERT INTO notetypes VALUES (?, ?, ?, -1, ?)",
            (mid, SLOVENE_VOCAB_NOTETYPE_NAME, 1700_000_000, nt_config),
        )
        for field in slovene_vocab_fields():
            conn.execute(
                "INSERT INTO fields VALUES (?, ?, ?, ?)",
                (mid, field.ord, field.name, build_field_config(field.name)),
            )
        for tpl in slovene_vocab_templates():
            conn.execute(
                "INSERT INTO templates VALUES (?, ?, ?, ?, -1, ?)",
                (mid, tpl.ord, tpl.name, 1700_000_000, build_template_config(tpl.qfmt, tpl.afmt)),
            )
        conn.commit()
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        assert integrity == "ok"

    def test_fields_readback_matches_input(self, tmp_path):
        from app.anki.notetype import slovene_vocab_fields

        conn = self._make_empty_db(tmp_path)
        mid = 42
        conn.execute(
            "INSERT INTO notetypes VALUES (?, ?, 0, -1, ?)",
            (mid, SLOVENE_VOCAB_NOTETYPE_NAME, build_notetype_config(css=".card {}")),
        )
        for field in slovene_vocab_fields():
            conn.execute(
                "INSERT INTO fields VALUES (?, ?, ?, ?)",
                (mid, field.ord, field.name, build_field_config(field.name)),
            )
        rows = conn.execute("SELECT ord, name FROM fields WHERE ntid=? ORDER BY ord", (mid,)).fetchall()
        assert rows == [(0, "Slovene"), (1, "English"), (2, "Audio"), (3, "Image"), (4, "Grammar"), (5, "Note")]
