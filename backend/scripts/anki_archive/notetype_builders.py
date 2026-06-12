"""Protobuf payload builders for the ``Slovene Vocabulary`` notetype (retired).

These builders only ever created the notetype during the one-shot
``merge_dupes`` / ``migrate_homonyms`` migrations (now in this archive package);
the live tree creates no notetypes and references only
``app.anki.notetype.SLOVENE_VOCAB_FIELD_NAMES``. Kept here — coverage-omitted
via ``app/anki/archive/*`` — so the archived migrations remain runnable.

Anki stores notetype/field/template definitions as serialized protobuf blobs in
the ``notetypes.config``, ``fields.config`` and ``templates.config`` columns.
Anki's loader tolerates sparse configs, so this module emits only the handful
of fields required for newly-inserted rows to survive ``PRAGMA integrity_check``
and to render correctly when Anki next opens the collection:

    field.config    = tag 3 (font_name) + tag 4 (font_size) + tag 255 (other JSON)
    template.config = tag 1 (qfmt)      + tag 2 (afmt)      + tag 255 (other JSON)
    notetype.config = tag 3 (css)                            + tag 255 (other JSON)
"""

from __future__ import annotations

from dataclasses import dataclass

from app.anki.notetype import SLOVENE_VOCAB_FIELD_NAMES
from app.anki.protobuf_wire import encode_tag, encode_varint, encode_varint_field


def _length_delimited(field_number: int, payload: bytes) -> bytes:
    return encode_tag(field_number, 2) + encode_varint(len(payload)) + payload


SLOVENE_VOCAB_CSS = """.card {
 font-family: arial;
 font-size: 22px;
 text-align: center;
 color: black;
 background-color: white;
}
.slovene { font-size: 28px; font-weight: bold; }
.english { font-size: 22px; }
.gram    { font-size: 16px; color: #555; }
.note    { font-size: 16px; color: #a00; margin-top: 8px; }
.img img { max-height: 240px; }
"""

_RECOGNITION_QFMT = '{{Audio}}<div class="slovene">{{Slovene}}</div>'
_RECOGNITION_AFMT = (
    '{{FrontSide}}<hr id="answer">'
    "{{Image}}"
    '<div class="english">{{English}}</div>'
    '<div class="gram">{{Grammar}}</div>'
    '<div class="note">{{Note}}</div>'
)
_PRODUCTION_QFMT = "{{Image}}"
_PRODUCTION_AFMT = (
    '{{FrontSide}}<hr id="answer">'
    "{{Audio}}"
    '<div class="slovene">{{Slovene}}</div>'
    '<div class="english">{{English}}</div>'
    '<div class="gram">{{Grammar}}</div>'
    '<div class="note">{{Note}}</div>'
)


@dataclass(frozen=True)
class FieldSpec:
    ord: int
    name: str


@dataclass(frozen=True)
class TemplateSpec:
    ord: int
    name: str
    qfmt: str
    afmt: str


def slovene_vocab_fields() -> list[FieldSpec]:
    return [FieldSpec(ord=i, name=name) for i, name in enumerate(SLOVENE_VOCAB_FIELD_NAMES)]


def slovene_vocab_templates() -> list[TemplateSpec]:
    return [
        TemplateSpec(ord=0, name="Recognition", qfmt=_RECOGNITION_QFMT, afmt=_RECOGNITION_AFMT),
        TemplateSpec(ord=1, name="Production", qfmt=_PRODUCTION_QFMT, afmt=_PRODUCTION_AFMT),
    ]


def build_field_config(name: str, font: str = "Arial", font_size: int = 20) -> bytes:
    """Return the protobuf blob for a single ``fields.config`` row."""
    other_json = b'{"media":[],"preventDeletion":false,"id":null,"tag":null}'
    out = b""
    out += _length_delimited(3, font.encode("utf-8"))
    out += encode_varint_field(4, font_size)
    out += _length_delimited(255, other_json)
    return out


def build_template_config(qfmt: str, afmt: str) -> bytes:
    """Return the protobuf blob for a single ``templates.config`` row."""
    other_json = b'{"id":null}'
    out = b""
    out += _length_delimited(1, qfmt.encode("utf-8"))
    out += _length_delimited(2, afmt.encode("utf-8"))
    out += _length_delimited(255, other_json)
    return out


def build_notetype_config(css: str) -> bytes:
    """Return the protobuf blob for a single ``notetypes.config`` row."""
    other_json = b'{"tags":[],"vers":[]}'
    out = b""
    out += _length_delimited(3, css.encode("utf-8"))
    out += _length_delimited(255, other_json)
    return out
