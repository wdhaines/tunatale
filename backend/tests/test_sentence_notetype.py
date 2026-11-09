"""The "TT Sentence" notetype: a TT-owned cloze notetype for lesson lines (tunatale-h0q3.55/.57).

Rendering is pinned against the anki engine in ``test_parity_sentence_notetype.py``;
this file pins what TT writes: the cloze kind, the field roster, that every field
a template names exists, and the CSS that picks the active blank's gloss.
"""

from __future__ import annotations

import re
import sqlite3

from app.cards.sentence_notetype import (
    AFMT,
    CSS,
    FIELD_NAMES,
    NAME,
    QFMT,
    create_sentence_notetype,
)
from app.cards.vocab_notetype import build_notetype_config


def _template_fields(template: str) -> set[str]:
    names = set()
    for tag in re.findall(r"\{\{([^}]+)\}\}", template):
        name = tag.lstrip("#^/").split(":")[-1]
        if name != "FrontSide":
            names.add(name)
    return names


class TestRoster:
    def test_text_is_the_first_field_so_it_sorts(self):
        assert FIELD_NAMES[0] == "Text"

    def test_every_field_a_template_names_exists(self):
        assert _template_fields(QFMT) | _template_fields(AFMT) <= set(FIELD_NAMES)

    def test_both_sides_render_the_cloze(self):
        assert "{{cloze:Text}}" in QFMT
        assert "{{cloze:Text}}" in AFMT

    def test_front_and_back_are_told_apart_for_the_css(self):
        assert 'class="tt-card tt-front"' in QFMT
        assert 'class="tt-card tt-back"' in AFMT

    def test_no_english_on_the_front(self):
        """Owner rule: card fronts never English. The English, glosses and cue meaning are back-only."""
        for field in ("English", "Glosses", "CueMeans", "BlankGlosses", "AnswerAudio", "Source"):
            assert "{{" + field + "}}" not in QFMT
            assert "{{" + field + "}}" in AFMT

    def test_source_sits_inside_a_fold_out_on_the_back(self):
        """Owner 2026-11-09: the lesson-and-line label only on the back, inside the details."""
        details = re.findall(r"<details.*?</details>", AFMT, re.S)
        assert any("{{Source}}" in d for d in details)

    def test_cue_fold_out_is_named_for_the_speaker(self):
        assert "<summary>What {{CueSpeaker}} said</summary>" in AFMT


class TestCss:
    def test_each_blank_gloss_shows_only_on_its_own_card(self):
        """The active blank renders as ``.cloze[data-ordinal=N]`` (measured with anki);
        its gloss is ``.tt-bg[data-c=N]``. Every other gloss stays hidden."""
        assert ".tt-bg { display: none; }" in CSS
        for n in range(2, 21):
            assert f'.tt-card:has(.cloze[data-ordinal="{n}"]) .tt-bg[data-c="{n}"]' in CSS

    def test_whole_line_front_is_a_dashed_bubble(self):
        assert '.tt-front .tt-line:has(.cloze[data-ordinal="1"])' in CSS

    def test_a_line_with_no_fold_outs_draws_no_empty_divider(self):
        """An opener has no glosses and no cue: its fold-out area renders empty (seen 2026-11-09)."""
        assert ".tt-folds:empty { display: none; }" in CSS

    def test_night_mode_under_both_class_names(self):
        assert ".nightMode .tt-card" in CSS
        assert ".night_mode .tt-card" in CSS


def _schema() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE notetypes (id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER, usn INTEGER, config BLOB);
        CREATE TABLE fields (ntid INTEGER, ord INTEGER, name TEXT, config BLOB, PRIMARY KEY (ntid, ord));
        CREATE TABLE templates (ntid INTEGER, ord INTEGER, name TEXT, mtime_secs INTEGER, usn INTEGER, config BLOB,
            PRIMARY KEY (ntid, ord));
    """)
    return conn


class TestCreate:
    def test_writes_a_cloze_kind_notetype_with_one_template(self):
        conn = _schema()
        create_sentence_notetype(conn, mid=42, now_ts=1700)
        name, mtime, usn, config = conn.execute("SELECT name, mtime_secs, usn, config FROM notetypes").fetchone()
        assert (name, mtime, usn) == (NAME, 1700, -1)
        # Notetype.Config field 1 (kind) = 1 (CLOZE), then the same blob as a normal notetype.
        assert config == bytes([0x08, 0x01]) + build_notetype_config(CSS)
        assert [r[0] for r in conn.execute("SELECT name FROM fields WHERE ntid = 42 ORDER BY ord")] == list(FIELD_NAMES)
        assert conn.execute("SELECT ord, name, usn FROM templates WHERE ntid = 42").fetchall() == [(0, NAME, -1)]
