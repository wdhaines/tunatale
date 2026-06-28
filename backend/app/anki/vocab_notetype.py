"""TT-managed vocabulary notetypes (one per language).

A :class:`VocabNotetype` describes the Anki notetype that TunaTale *mints its
own* cards into for a language — field layout (ord order), the L2/sort field,
and the two card templates (Recognition ord 0, Production ord 1). This is the
*write* side: it is what ``OfflineWriter.create_note`` serializes into and what
the per-language ``create_vocab_notetype`` migration builds in the collection.

The user's *imported* deck may use a different, curated notetype (e.g.
Norwegian's 17-field "6000 Most Frequent Norwegian Words", recognition-only) —
that is the *read* side, handled by ``field_map.NotetypeProfile``. TT never
mints into the imported notetype; new TT cards go into the language's
``VocabNotetype`` so production cards + an Image field fit cleanly.

The Slovene definition reproduces the existing "Slovene Vocabulary" notetype's
field order exactly so the write path stays byte-identical for Slovene.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.anki.protobuf_wire import encode_tag, encode_varint, encode_varint_field

# Shared field roster for TT vocab notetypes. Only the L2 (first) field name
# differs between languages; the rest are identical so the write path is uniform.
_TAIL_FIELDS: tuple[str, ...] = ("English", "Audio", "Image", "Grammar", "Note", "DisambigKey")


@dataclass(frozen=True)
class VocabNotetype:
    """A TT-managed vocabulary notetype (Recognition + Production templates)."""

    name: str
    l2_field: str  # field ord 0 — the L2 word + the note's sort field
    l2_css_class: str  # CSS class wrapping the L2 word in the card templates

    @property
    def field_names(self) -> tuple[str, ...]:
        """Field names in ord order (L2 first = sort field)."""
        return (self.l2_field, *_TAIL_FIELDS)


SLOVENE_VOCAB = VocabNotetype(name="Slovene Vocabulary", l2_field="Slovene", l2_css_class="slovene")
NORWEGIAN_VOCAB = VocabNotetype(name="Norwegian Vocabulary", l2_field="Norwegian", l2_css_class="norwegian")

_BY_NAME: dict[str, VocabNotetype] = {nt.name: nt for nt in (SLOVENE_VOCAB, NORWEGIAN_VOCAB)}


def get_vocab_notetype_by_name(name: str) -> VocabNotetype | None:
    """Return the :class:`VocabNotetype` named *name*, or ``None`` if unknown."""
    return _BY_NAME.get(name)


# ── Card templates + CSS (parametrised by the L2 field/class) ─────────────


def _css(l2_class: str) -> str:
    return (
        ".card {\n"
        " font-family: arial;\n"
        " font-size: 22px;\n"
        " text-align: center;\n"
        " color: black;\n"
        " background-color: white;\n"
        "}\n"
        f".{l2_class} {{ font-size: 28px; font-weight: bold; }}\n"
        ".english { font-size: 22px; }\n"
        ".gram    { font-size: 16px; color: #555; }\n"
        ".note    { font-size: 16px; color: #a00; margin-top: 8px; }\n"
        ".img img { max-height: 240px; }\n"
    )


def _recognition_qfmt(l2_field: str, l2_class: str) -> str:
    return f'{{{{Audio}}}}<div class="{l2_class}">{{{{{l2_field}}}}}</div>'


def _recognition_afmt() -> str:
    return (
        '{{FrontSide}}<hr id="answer">'
        "{{Image}}"
        '<div class="english">{{English}}</div>'
        '<div class="gram">{{Grammar}}</div>'
        '<div class="note">{{Note}}</div>'
    )


def _production_qfmt() -> str:
    return "{{Image}}"


def _production_afmt(l2_field: str, l2_class: str) -> str:
    return (
        '{{FrontSide}}<hr id="answer">'
        "{{Audio}}"
        f'<div class="{l2_class}">{{{{{l2_field}}}}}</div>'
        '<div class="english">{{English}}</div>'
        '<div class="gram">{{Grammar}}</div>'
        '<div class="note">{{Note}}</div>'
    )


# ── Protobuf config builders (minimal, integrity-check-safe) ──────────────
#
# Anki stores notetype/field/template definitions as serialized protobuf in the
# *.config BLOB columns. Anki tolerates sparse configs; we emit only what newly
# inserted rows need to survive PRAGMA integrity_check and render on open.


def _length_delimited(field_number: int, payload: bytes) -> bytes:
    return encode_tag(field_number, 2) + encode_varint(len(payload)) + payload


def build_field_config(font: str = "Arial", font_size: int = 20) -> bytes:
    """Protobuf blob for one ``fields.config`` row."""
    other_json = b'{"media":[],"preventDeletion":false,"id":null,"tag":null}'
    return (
        _length_delimited(3, font.encode("utf-8"))
        + encode_varint_field(4, font_size)
        + _length_delimited(255, other_json)
    )


def build_template_config(qfmt: str, afmt: str) -> bytes:
    """Protobuf blob for one ``templates.config`` row."""
    other_json = b'{"id":null}'
    return (
        _length_delimited(1, qfmt.encode("utf-8"))
        + _length_delimited(2, afmt.encode("utf-8"))
        + _length_delimited(255, other_json)
    )


def build_notetype_config(css: str) -> bytes:
    """Protobuf blob for one ``notetypes.config`` row."""
    other_json = b'{"tags":[],"vers":[]}'
    return _length_delimited(3, css.encode("utf-8")) + _length_delimited(255, other_json)


def create_vocab_notetype(conn, vocab: VocabNotetype, mid: int, now_ts: int) -> None:
    """Insert *vocab*'s notetype + fields + templates rows into *conn*.

    Caller is responsible for the surrounding transaction and the ``col.scm`` /
    ``col.mod`` bump (a notetype insert is a schema change — see
    ``.claude/rules/anki-sync.md``). Rows carry ``usn = -1``.
    """
    conn.execute(
        "INSERT INTO notetypes (id, name, mtime_secs, usn, config) VALUES (?, ?, ?, -1, ?)",
        (mid, vocab.name, now_ts, build_notetype_config(_css(vocab.l2_css_class))),
    )
    for ord_, name in enumerate(vocab.field_names):
        conn.execute(
            "INSERT INTO fields (ntid, ord, name, config) VALUES (?, ?, ?, ?)",
            (mid, ord_, name, build_field_config()),
        )
    templates = [
        ("Recognition", _recognition_qfmt(vocab.l2_field, vocab.l2_css_class), _recognition_afmt()),
        ("Production", _production_qfmt(), _production_afmt(vocab.l2_field, vocab.l2_css_class)),
    ]
    for ord_, (tname, qfmt, afmt) in enumerate(templates):
        conn.execute(
            "INSERT INTO templates (ntid, ord, name, mtime_secs, usn, config) VALUES (?, ?, ?, ?, -1, ?)",
            (mid, ord_, tname, now_ts, build_template_config(qfmt, afmt)),
        )
