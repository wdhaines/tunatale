"""Shared SRS item shape literals for openapi ledger batch 6a.

Independent LITERAL key-sets pinned against the UNFILTERED output of
``srs.py::_item_to_dict`` / ``_direction_to_dict``. Written by hand — never
derived from ``set(Model.model_fields)``, which is circular once
``response_model=`` is in (the model filters the payload to its own fields, so
deleting a field would shrink both sides of the assertion and the test would
stay green).

``DIRECTION_KEYS`` is the FULL 10-key set including ``left``; a direction whose
``left`` is None omits it, so the review/new branch asserts
``DIRECTION_KEYS - {"left"}``.
"""

DIRECTION_KEYS = {
    "state",
    "due_at",
    "stability",
    "difficulty",
    "reps",
    "lapses",
    "last_review",
    "last_review_time_ms",
    "anki_card_id",
    "left",
}

SRS_ITEM_KEYS = {
    "id",
    "text",
    "translation",
    "word_count",
    "state",
    "due_at",
    "stability",
    "difficulty",
    "reps",
    "lapses",
    "last_review",
    "language_code",
    "guid",
    "anki_note_id",
    "directions",
    "card_type",
    "source_sentence",
    "source_sentence_translation",
    "image_url",
    "audio_url",
    "grammar",
    "note",
    "article",
    "extras",
    "pos",
}

DIRECTION_WITHOUT_LEFT = DIRECTION_KEYS - {"left"}
