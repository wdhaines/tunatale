"""Guards for the SRSDatabase mixin decomposition (god-module split).

(a) MRO pin: every expected base must stay in SRSDatabase.__mro__ — catches an
accidental `class SRSDatabase:` that drops the composition.
(b) Public-method-count pin: a mixin move that silently loses a method fails
here loudly instead of as an obscure AttributeError elsewhere.
"""

from app.srs.database import SRSDatabase
from app.srs.db_base import SRSDatabaseBase
from app.srs.db_collocations import DbCollocationsMixin
from app.srs.db_counts import DbCountsMixin
from app.srs.db_directions import DbDirectionsMixin
from app.srs.db_histogram import DbHistogramMixin
from app.srs.db_ignored_lemmas import DbIgnoredLemmasMixin
from app.srs.db_kv_cache import DbKvCacheMixin
from app.srs.db_lemma_cache import DbLemmaCacheMixin
from app.srs.db_media import DbMediaMixin
from app.srs.db_queue import DbQueueMixin
from app.srs.db_revlog import DbRevlogMixin
from app.srs.db_sync import DbSyncMixin
from app.srs.db_sync_conflicts import DbSyncConflictsMixin

_EXPECTED_BASES = [
    DbCollocationsMixin,
    DbDirectionsMixin,
    DbQueueMixin,
    DbCountsMixin,
    DbRevlogMixin,
    DbSyncMixin,
    DbMediaMixin,
    DbKvCacheMixin,
    DbHistogramMixin,
    DbLemmaCacheMixin,
    DbIgnoredLemmasMixin,
    DbSyncConflictsMixin,
    SRSDatabaseBase,
]


def test_mixin_composition() -> None:
    for base in _EXPECTED_BASES:
        assert base in SRSDatabase.__mro__, base


def test_public_method_count_pinned() -> None:
    count = sum(1 for m in dir(SRSDatabase) if not m.startswith("_") and callable(getattr(SRSDatabase, m)))
    # 100 + count_interday_learning_due (Layer 79); the variant scan+hydrate
    # single-query merge was a net-zero swap; +get_unpushed_revlog_rows (Layer 80)
    # +list_media_by_collocation_and_filename +update_media_stat (media refresh optimisation)
    # +add_dirty_field_by_id (Step 6: manual image-update API)
    # +get_image_filenames (Step 6-tail: batched image lookup for /items)
    # +is_media_filename_referenced (fix #4: shared-file reference check for orphan cleanup)
    # +record_listen, has_listen, count_listens, get_listened_lessons (lesson_listens)
    assert count == 111
