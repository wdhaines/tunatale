"""SQLite repository for SRS collocations and violations — composition facade.

Schema is managed by `app.srs.migrations`. Fresh DBs bootstrap the v0 base
tables (matching the pre-migration shape) and then `migrate()` runs every
pending step up to `CURRENT_VERSION`.

Supports ":memory:" for in-memory test databases.

Since the god-module split (stages 3–5, 2026-07), the method bodies live in
per-concern mixin modules (db_base infra + db_collocations, db_directions,
db_queue, db_counts, db_revlog, db_sync, db_media, db_kv_cache,
db_histogram, db_lemma_cache, db_ignored_lemmas, db_sync_conflicts).
This module composes them into `SRSDatabase` and re-exports the legacy
module-level names (`X as X` so ruff keeps them) — import and patch
through `app.srs.database` as before.
"""

from __future__ import annotations

from app.srs.db_base import _DIR_COLUMNS as _DIR_COLUMNS
from app.srs.db_base import _LEARNING_STATES as _LEARNING_STATES
from app.srs.db_base import _NEW_RESET_SET as _NEW_RESET_SET
from app.srs.db_base import _NON_REVIEWABLE_STATES as _NON_REVIEWABLE_STATES
from app.srs.db_base import SRSDatabaseBase as SRSDatabaseBase
from app.srs.db_base import _anki_day_bounds_utc as _anki_day_bounds_utc
from app.srs.db_base import _parse_last_review as _parse_last_review
from app.srs.db_collocations import DbCollocationsMixin as DbCollocationsMixin
from app.srs.db_counts import DbCountsMixin as DbCountsMixin
from app.srs.db_directions import DbDirectionsMixin as DbDirectionsMixin
from app.srs.db_histogram import DbHistogramMixin as DbHistogramMixin
from app.srs.db_ignored_lemmas import DbIgnoredLemmasMixin as DbIgnoredLemmasMixin
from app.srs.db_kv_cache import DbKvCacheMixin as DbKvCacheMixin
from app.srs.db_lemma_cache import DbLemmaCacheMixin as DbLemmaCacheMixin
from app.srs.db_listens import DbListensMixin as DbListensMixin
from app.srs.db_media import DbMediaMixin as DbMediaMixin
from app.srs.db_queue import DbQueueMixin as DbQueueMixin
from app.srs.db_reviews import DbReviewsMixin as DbReviewsMixin
from app.srs.db_revlog import DbRevlogMixin as DbRevlogMixin
from app.srs.db_sync import DbSyncMixin as DbSyncMixin
from app.srs.db_sync_conflicts import DbSyncConflictsMixin as DbSyncConflictsMixin


class SRSDatabase(
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
    DbListensMixin,
    DbReviewsMixin,
    DbIgnoredLemmasMixin,
    DbSyncConflictsMixin,
    SRSDatabaseBase,
):
    """SQLite-backed SRS repository.

    Use `:memory:` as db_path for in-memory test databases.
    """
