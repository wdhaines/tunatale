"""Guards for the SRSDatabase mixin decomposition (god-module split).

(a) MRO pin: every expected base must stay in SRSDatabase.__mro__ — catches an
accidental `class SRSDatabase:` that drops the composition.
(b) Public-method-count pin: a mixin move that silently loses a method fails
here loudly instead of as an obscure AttributeError elsewhere.
"""

from app.srs.database import SRSDatabase
from app.srs.db_base import SRSDatabaseBase
from app.srs.db_histogram import DbHistogramMixin
from app.srs.db_kv_cache import DbKvCacheMixin
from app.srs.db_media import DbMediaMixin

_EXPECTED_BASES = [DbMediaMixin, DbKvCacheMixin, DbHistogramMixin, SRSDatabaseBase]


def test_mixin_composition() -> None:
    for base in _EXPECTED_BASES:
        assert base in SRSDatabase.__mro__, base


def test_public_method_count_pinned() -> None:
    count = sum(1 for m in dir(SRSDatabase) if not m.startswith("_") and callable(getattr(SRSDatabase, m)))
    assert count == 99  # measured pre-split (stage 3 god-module extraction)
