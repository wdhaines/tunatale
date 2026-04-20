"""Minimal protobuf payload builders for the ``Slovene Vocabulary`` notetype.

Anki stores notetype/field/template definitions as serialized protobuf blobs in
the ``notetypes.config``, ``fields.config`` and ``templates.config`` columns.
Anki's loader tolerates sparse configs, so this module emits only the handful
of fields required for the newly-inserted rows to survive ``PRAGMA
integrity_check`` and to render correctly when Anki next opens the collection.

The inspection output from a real Basic notetype is used as the reference
layout:

    field.config    = tag 3 (font_name) + tag 4 (font_size) + tag 255 (other JSON)
    template.config = tag 1 (qfmt)      + tag 2 (afmt)      + tag 255 (other JSON)
    notetype.config = tag 3 (css)                            + tag 255 (other JSON)
"""

from __future__ import annotations

from dataclasses import dataclass


def _varint(value: int) -> bytes:
    """Encode an unsigned integer as a protobuf varint."""
    out = bytearray()
    while True:
        to_write = value & 0x7F
        value >>= 7
        if value:
            out.append(to_write | 0x80)
        else:
            out.append(to_write)
            break
    return bytes(out)


def _tag(field_number: int, wire_type: int) -> bytes:
    return _varint((field_number << 3) | wire_type)


def _length_delimited(field_number: int, payload: bytes) -> bytes:
    return _tag(field_number, 2) + _varint(len(payload)) + payload


def _varint_field(field_number: int, value: int) -> bytes:
    return _tag(field_number, 0) + _varint(value)


SLOVENE_VOCAB_NOTETYPE_NAME = "Slovene Vocabulary"

SLOVENE_VOCAB_FIELD_NAMES = ["Slovene", "English", "Audio", "Image", "Grammar", "Note", "DisambigKey"]

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
    out += _varint_field(4, font_size)
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
