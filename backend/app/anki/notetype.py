"""Identity of the ``Slovene Vocabulary`` notetype (name + field layout).

These back-compat constants are kept for the archived one-shot migrations and
the legacy write-path fallback. The canonical per-language vocab notetype
descriptors (including Norwegian) now live in ``app.anki.vocab_notetype`` as
:class:`~app.anki.vocab_notetype.VocabNotetype`; these constants are derived
from the Slovene one so there is a single source of truth for its field order.
"""

from __future__ import annotations

from app.anki.vocab_notetype import SLOVENE_VOCAB

SLOVENE_VOCAB_NOTETYPE_NAME = SLOVENE_VOCAB.name

SLOVENE_VOCAB_FIELD_NAMES = list(SLOVENE_VOCAB.field_names)
