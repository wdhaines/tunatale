"""The "TT Sentence" notetype: one note per lesson line, its cards siblings (tunatale-h0q3.55/.57).

A cloze-kind notetype TT owns. One note carries a line's blanks and its whole-line
card as clozes of one ``Text`` field (the whole line is cloze 1 and wraps the
blanks), so Anki buries them as siblings. The other fields are the line's
parts, which the templates lay out as a dialogue: the cue on the left, the
learner's line on the right (owner-approved mockups, 2026-11-09:
https://claude.ai/artifact/RPJTgZsKcBAR86zsggxSDS).

The active blank renders as ``<span class="cloze" data-ordinal="N">`` (measured
with the anki engine), so the CSS alone shows the active blank's gloss
(``.tt-bg[data-c=N]``) and turns the whole-line front into a dashed bubble.
No template script.

Creating it is a schema change (``col.scm``): ``anki_sync.add_sentence_notetype``.
"""

from __future__ import annotations

from app.cards.vocab_notetype import build_field_config, build_notetype_config, build_template_config
from app.srs.anki_mirror.protobuf_wire import encode_varint_field

NAME = "TT Sentence"

#: Ord order. Text first: it is the sort field.
FIELD_NAMES: tuple[str, ...] = (
    "Text",
    "Speaker",
    "CueSpeaker",
    "Cue",
    "CueAudio",
    "AnswerAudio",
    "Picture",
    "English",
    "BlankGlosses",
    "Glosses",
    "CueMeans",
    "Source",
)

#: Blanks are clozes 2..MAX_ORD; the CSS carries one gloss rule per number.
MAX_ORD = 20

_PICTURE = '{{#Picture}}<div class="tt-pic">{{Picture}}</div>{{/Picture}}'
_CUE = (
    '{{#Cue}}<div class="tt-turn tt-them"><div class="tt-who">{{CueSpeaker}}</div>'
    '<div class="tt-bubble">{{CueAudio}}<span class="tt-text">{{Cue}}</span></div></div>{{/Cue}}'
)

QFMT = (
    '<div class="tt-card tt-front">'
    + _PICTURE
    + _CUE
    + '<div class="tt-turn tt-you"><div class="tt-who">{{Speaker}}</div>'
    '<div class="tt-bubble tt-line"><span class="tt-text">{{cloze:Text}}</span></div></div>'
    "</div>"
)

_SOURCE = '{{#Source}}<div class="tt-source">{{Source}}</div>{{/Source}}'

AFMT = (
    '<div class="tt-card tt-back">'
    + _PICTURE
    + _CUE
    + '<div class="tt-turn tt-you"><div class="tt-who">{{Speaker}}</div>'
    '<div class="tt-bubble tt-line">{{AnswerAudio}}<span class="tt-text">{{cloze:Text}}</span></div></div>'
    '<div class="tt-focus">{{BlankGlosses}}</div>'
    '<div class="tt-en">{{English}}</div>'
    '<div class="tt-folds">'
    '{{#Glosses}}<details class="tt-fold"><summary>Word by word</summary>'
    '<div class="tt-glosses">{{Glosses}}</div>' + _SOURCE + "</details>{{/Glosses}}"
    '{{#CueMeans}}<details class="tt-fold"><summary>What {{CueSpeaker}} said</summary>{{CueMeans}}'
    "{{^Glosses}}" + _SOURCE + "{{/Glosses}}</details>{{/CueMeans}}"
    "</div>"
    "</div>"
)

_GLOSS_SHOWN = ",\n".join(
    f'.tt-card:has(.cloze[data-ordinal="{n}"]) .tt-bg[data-c="{n}"]' for n in range(2, MAX_ORD + 1)
)

CSS = (
    """.card {
 font-family: Roboto, system-ui, -apple-system, "Segoe UI", sans-serif;
 background-color: #FFFFFF;
 color: #1C1F23;
 text-align: left;
}
.card.nightMode, .card.night_mode, .nightMode .card, .night_mode .card { background-color: #121417; }
.tt-card {
 --ink: #1C1F23; --muted: #5F6368; --them: #F1F3F4; --them-ink: #3C4043; --you: #E6F2F3;
 --accent: #0B6E78; --warm: #9A4A00; --warm-bg: #FDF1E3; --rule: #DADCE0; --pic: #F7F6F2;
 color: var(--ink); max-width: 480px; margin: 0 auto; padding: 20px 16px 28px; box-sizing: border-box;
 display: flex; flex-direction: column; gap: 18px; text-align: left; font-size: 18px; line-height: 1.45;
}
.nightMode .tt-card, .night_mode .tt-card {
 --ink: #E8EAED; --muted: #9AA0A6; --them: #24272B; --them-ink: #C4C7CB; --you: #10363A;
 --accent: #5CC4CC; --warm: #FFB868; --warm-bg: #3A2A17; --rule: #3A3F45; --pic: #24272B;
}
.tt-pic { background: var(--pic); border-radius: 18px; overflow: hidden; }
.tt-pic img { display: block; max-width: 100%; max-height: 260px; margin: 0 auto; }
.tt-turn { display: flex; flex-direction: column; gap: 6px; }
.tt-them { align-items: flex-start; }
.tt-you { align-items: flex-end; }
.tt-who { font-size: 13px; font-weight: 500; letter-spacing: .06em; text-transform: uppercase; color: var(--muted); }
.tt-you .tt-who { color: var(--accent); }
.tt-you .tt-who::after { content: " · you"; }
.tt-bubble { display: flex; align-items: center; gap: 12px; max-width: 88%; padding: 10px 16px; box-sizing: border-box; font-size: 20px; }
.tt-them .tt-bubble { background: var(--them); border-radius: 18px 18px 18px 4px; }
.tt-you .tt-bubble { background: var(--you); border-radius: 18px 18px 4px 18px; font-size: 22px; }
.tt-back .tt-them .tt-bubble { font-size: 16px; color: var(--them-ink); padding: 6px 14px; }
.tt-card .replay-button { flex: none; }
.tt-card .cloze { font-weight: 700; color: var(--warm); border-bottom: 3px solid var(--warm); }
.tt-card .cloze-inactive { color: inherit; font-weight: inherit; }
.tt-front .cloze { display: inline-block; min-width: 2.6em; text-align: center; line-height: 1.1; }
.tt-front .tt-line:has(.cloze[data-ordinal="1"]) {
 background: transparent; border: 2px dashed var(--warm); min-width: 200px; min-height: 54px; justify-content: center;
}
.tt-front .cloze[data-ordinal="1"] { border: 0; min-width: 0; font-size: 0; }
.tt-front .cloze[data-ordinal="1"]::after { content: "\\2022  \\2022  \\2022"; font-size: 20px; }
.tt-back .cloze[data-ordinal="1"] { color: inherit; font-weight: 500; border: 0; }
.tt-bg { display: none; }
"""
    + _GLOSS_SHOWN
    + """ {
 display: block; background: var(--warm-bg); border-radius: 14px; padding: 14px 16px; font-size: 17px;
}
.tt-bg b { display: block; font-size: 24px; color: var(--warm); }
.tt-en { font-size: 18px; color: var(--them-ink); }
.tt-folds { border-top: 1px solid var(--rule); }
.tt-folds:empty { display: none; }
.tt-fold { border-bottom: 1px solid var(--rule); }
.tt-fold summary {
 list-style: none; min-height: 48px; display: flex; align-items: center; justify-content: space-between;
 font-size: 16px; cursor: pointer;
}
.tt-fold summary::-webkit-details-marker { display: none; }
.tt-fold summary::after { content: "\\25BE"; color: var(--muted); font-size: 20px; }
.tt-fold[open] summary::after { content: "\\25B4"; }
.tt-glosses { display: grid; grid-template-columns: max-content minmax(0, 1fr); column-gap: 14px; row-gap: 8px; padding-bottom: 14px; font-size: 16px; }
.tt-w { font-weight: 700; }
.tt-g { color: var(--them-ink); }
.tt-fold .tt-en { font-size: 16px; padding-bottom: 14px; }
.tt-source { font-size: 12px; letter-spacing: .08em; text-transform: uppercase; color: var(--muted); padding-bottom: 12px; }
"""
)

#: Notetype.Config field 1 (kind) = 1: a cloze notetype.
_CLOZE_KIND = encode_varint_field(1, 1)


def create_sentence_notetype(conn, mid: int, now_ts: int) -> None:
    """Insert the notetype, its fields and its one template into *conn*, rows at ``usn = -1``.

    The caller owns the transaction and the ``col.scm`` / ``col.mod`` bump.
    """
    conn.execute(
        "INSERT INTO notetypes (id, name, mtime_secs, usn, config) VALUES (?, ?, ?, -1, ?)",
        (mid, NAME, now_ts, _CLOZE_KIND + build_notetype_config(CSS)),
    )
    for ord_, name in enumerate(FIELD_NAMES):
        conn.execute(
            "INSERT INTO fields (ntid, ord, name, config) VALUES (?, ?, ?, ?)",
            (mid, ord_, name, build_field_config()),
        )
    conn.execute(
        "INSERT INTO templates (ntid, ord, name, mtime_secs, usn, config) VALUES (?, 0, ?, ?, -1, ?)",
        (mid, NAME, now_ts, build_template_config(QFMT, AFMT)),
    )
