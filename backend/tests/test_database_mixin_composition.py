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
from app.srs.db_listens import DbListensMixin
from app.srs.db_media import DbMediaMixin
from app.srs.db_pending_grades import DbPendingGradesMixin
from app.srs.db_queue import DbQueueMixin
from app.srs.db_reviews import DbReviewsMixin
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
    DbListensMixin,
    DbPendingGradesMixin,
    DbReviewsMixin,
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
    # +latest_listen_at (lesson_listens)
    # +count_new_created_today (staged-listen creation budget)
    # +record_review, latest_review_at (lesson_reviews)
    # +has_counting_review_today (budget-neutral Check-your-work re-grade)
    # +stage_pending_grade, get_pending_grades, get_pending_grade,
    #  clear_pending_grade, count_pending_grades (pending-listen-grades mixin)
    # +clear_pending_grade_by_guid (sync_pull clears by guid, not row id)
    # +clear_pending_grades_for_lesson (a listen resets its own bucket, then stages fresh)
    # +add_production_direction (just-in-time production mint, tunatale-qf6.2)
    # +count_words_awaiting_production, list_words_awaiting_production (its selection)
    # +set_base_collocation_id, get_base_collocation_id, get_covering_cloze
    #  (the base-cloze link: one word, two rows, one resolver — tunatale-qf6.9)
    # +get_inflection_candidates (the deck's own Inflections table as a dedup
    #  key — tunatale-qi4b; returns raw extras, NOT hydrated items, because the
    #  Norwegian deck has 2591 such rows and only the one winner is hydrated)
    # +set_translation_dirty (gloss retry backfills a translation and marks it
    #  for push, without re-keying the guid — tunatale-1wiw)
    assert count == 131
