"""Identity of the ``Slovene Vocabulary`` notetype (name + field layout).

These two constants are live: ``app.anki.sync`` maps Anki note fields by
position via ``SLOVENE_VOCAB_FIELD_NAMES``, and the note-creation round-trip
tests target ``SLOVENE_VOCAB_NOTETYPE_NAME``. The protobuf notetype/field/
template *builders* (used solely by the retired one-shot migrations) live in
``scripts.anki_archive.notetype_builders``.
"""

from __future__ import annotations

SLOVENE_VOCAB_NOTETYPE_NAME = "Slovene Vocabulary"

SLOVENE_VOCAB_FIELD_NAMES = ["Slovene", "English", "Audio", "Image", "Grammar", "Note", "DisambigKey"]
