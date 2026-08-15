"""Slovene A1 morphology bundle — the person/number-driven vocabulary.

Slovene registers the default A1 vocabulary from ``app.srs.function_words``
unmodified, so its feature strings, A1 whitelist, and hint text stay
byte-identical to the pre-registry behaviour (``verb:1sg``, ``noun:loc:sg``,
``adj:nom:f:sg`` → "1st person singular", "locative singular", …).
"""

from app.srs.a1_morphology import A1Morphology
from app.srs.function_words import (
    _DEFAULT_A1_PREFIXES,
    _default_format_morphology_hint,
    _default_ud_feats_to_tt_feature,
)

SLOVENE_A1_MORPHOLOGY = A1Morphology(
    to_feature=_default_ud_feats_to_tt_feature,
    a1_prefixes=_DEFAULT_A1_PREFIXES,
    format_hint=_default_format_morphology_hint,
)
