"""Storage for review sessions (bd tunatale-y354, under tunatale-9p9d).

⚠️ WHAT THESE TESTS ARE ABOUT is the ABSENCE of two columns. A review session is
not a curriculum day: no theme, no position in a sequence, content drawn from the
whole language deck rather than one plan. The user decided that from four mocked
placements after a first attempt put a "Review story" button on the LESSON page,
beside Regenerate — a path whose entire job is REPLACING a lesson, when a review
session is ADDITIVE.

So ``test_the_table_has_no_day_and_no_curriculum`` is the load-bearing one. The
round-trip tests would all still pass against a table that quietly carried a
``day`` column, and that table would be the decision undone.

WHY A NEW TABLE and not nullable columns on ``lessons``: ``lessons.curriculum_id``
and ``lessons.day`` are both NOT NULL, SQLite has no DROP NOT NULL, and the
alternative was a twelve-step rebuild of the table holding every real lesson the
user owns. Zero migration risk beat a cheaper read path.

Dates here are fixed literals, never ``date.today()`` — a test whose oracle moves
with the clock is one that goes red at a time of day nobody is watching.
"""

from __future__ import annotations

from app.models.lesson import Lesson, Phrase, Section, SectionType
from app.storage.store import ContentStore

# ── helpers ──────────────────────────────────────────────────────────────────


def _lesson(title: str = "Coffee and a Missed Train", language_code: str = "no") -> Lesson:
    return Lesson(
        title=title,
        language_code=language_code,
        sections=[
            Section(
                section_type=SectionType.NATURAL_SPEED,
                phrases=[Phrase(text="Har du sovet godt?", voice_id="test-voice", language_code=language_code)],
            )
        ],
    )


def _store() -> ContentStore:
    return ContentStore(":memory:")


# ── the distinguishing property ──────────────────────────────────────────────


class TestItIsNotACurriculumDay:
    def test_the_table_has_no_day_and_no_curriculum(self):
        """The whole point of the epic, expressed as a schema assertion.

        Every other test in this file would pass against a table that carried a
        ``day`` column nobody set. This is the one that would not.
        """
        store = _store()
        with store._get_conn() as conn:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(review_sessions)")}

        assert columns, "review_sessions table does not exist"
        assert "day" not in columns
        assert "curriculum_id" not in columns
        assert "position" not in columns

    def test_a_session_does_not_appear_among_the_lessons(self):
        """Two stores of content, and a session must not leak into the other.

        ``list_lessons`` is what the one-shot migrations walk. A session showing
        up there would be handed to code that assumes a curriculum and a day.
        """
        store = _store()
        store.save_review_session("sess-1", "no", "2026-09-02", _lesson())

        assert store.list_lessons() == []
        assert store.get_lesson("sess-1") is None


# ── round trip ───────────────────────────────────────────────────────────────


class TestRoundTrip:
    def test_a_saved_session_comes_back(self):
        store = _store()
        store.save_review_session("sess-1", "no", "2026-09-02", _lesson())

        got = store.get_review_session("sess-1")
        assert got is not None
        assert got.title == "Coffee and a Missed Train"
        assert got.sections[0].phrases[0].text == "Har du sovet godt?"

    def test_an_unknown_id_is_none_not_an_error(self):
        assert _store().get_review_session("nope") is None

    def test_the_row_carries_the_language_and_the_date(self):
        store = _store()
        store.save_review_session("sess-1", "no", "2026-09-02", _lesson())

        row = store.get_review_session_row("sess-1")
        assert row is not None
        assert row["language_code"] == "no"
        assert row["session_date"] == "2026-09-02"

    def test_an_unknown_row_is_none(self):
        assert _store().get_review_session_row("nope") is None

    def test_it_survives_a_real_file_and_a_reopen(self, tmp_path):
        """The in-memory store commits explicitly; the file-backed one commits
        through ``_file_conn``. Only the second is what production runs, and a
        write that never reaches disk looks identical to a correct one until the
        process restarts — so the assertion is made against a SECOND store
        opened on the same path, not against the one that did the writing."""
        db = str(tmp_path / "content.db")
        with ContentStore(db) as store:
            store.save_review_session(
                "sess-1", "no", "2026-09-02", _lesson(), review_requested=["oppføre"], review_used=[]
            )

        with ContentStore(db) as reopened:
            assert reopened.get_review_session("sess-1").title == "Coffee and a Missed Train"
            row = reopened.list_review_sessions("no")[0]
            assert row["review_requested"] == ["oppføre"]
            assert row["review_used"] == []


# ── the dated list ───────────────────────────────────────────────────────────


class TestTheDatedList:
    def test_it_is_scoped_to_one_language(self):
        """The bug class with no symptom: a Norwegian session in a Slovene list
        reads perfectly well and simply drills the wrong deck."""
        store = _store()
        store.save_review_session("no-1", "no", "2026-09-02", _lesson())
        store.save_review_session("sl-1", "sl", "2026-09-02", _lesson(language_code="sl"))

        assert [row["id"] for row in store.list_review_sessions("no")] == ["no-1"]
        assert [row["id"] for row in store.list_review_sessions("sl")] == ["sl-1"]

    def test_newest_first(self):
        store = _store()
        store.save_review_session("older", "no", "2026-08-28", _lesson())
        store.save_review_session("newer", "no", "2026-09-02", _lesson())

        assert [row["id"] for row in store.list_review_sessions("no")] == ["newer", "older"]

    def test_two_sessions_on_one_day_are_both_listed(self):
        """A date is not a key. Nothing stops a learner reviewing twice."""
        store = _store()
        store.save_review_session("first", "no", "2026-09-02", _lesson())
        store.save_review_session("second", "no", "2026-09-02", _lesson())

        assert {row["id"] for row in store.list_review_sessions("no")} == {"first", "second"}

    def test_no_sessions_is_an_empty_list(self):
        assert _store().list_review_sessions("no") == []

    def test_the_list_carries_the_title_without_the_body(self):
        """The list renders a row per session; deserialising a whole Lesson for
        each one to read its title would be the obvious and wrong way to do it."""
        store = _store()
        store.save_review_session("sess-1", "no", "2026-09-02", _lesson())

        row = store.list_review_sessions("no")[0]
        assert row["title"] == "Coffee and a Missed Train"
        assert "data_json" not in row


# ── the coverage pair ────────────────────────────────────────────────────────


class TestTheCoveragePair:
    def test_the_words_asked_for_and_the_words_used_survive(self):
        store = _store()
        store.save_review_session(
            "sess-1",
            "no",
            "2026-09-02",
            _lesson(),
            review_requested=["oppføre", "dessuten", "forskrift"],
            review_used=["oppføre", "dessuten"],
        )

        row = store.list_review_sessions("no")[0]
        assert row["review_requested"] == ["oppføre", "dessuten", "forskrift"]
        assert row["review_used"] == ["oppføre", "dessuten"]

    def test_unmeasured_is_none_not_empty(self):
        """Empty means unmeasurable, not zero — the same distinction the lesson
        readout already makes. A session stored without the pair must not render
        as 'reused 0 of 0', which is a grade rather than an observation."""
        store = _store()
        store.save_review_session("sess-1", "no", "2026-09-02", _lesson())

        row = store.list_review_sessions("no")[0]
        assert row["review_requested"] is None
        assert row["review_used"] is None

    def test_asked_for_but_used_none_is_an_empty_list_not_none(self):
        """The other side of the same distinction: a measured zero is a real
        result and must be distinguishable from never having measured."""
        store = _store()
        store.save_review_session("sess-1", "no", "2026-09-02", _lesson(), review_requested=["oppføre"], review_used=[])

        row = store.list_review_sessions("no")[0]
        assert row["review_requested"] == ["oppføre"]
        assert row["review_used"] == []


# ── deletion ─────────────────────────────────────────────────────────────────


class TestDeletion:
    def test_it_removes_the_session_and_returns_the_orphaned_audio(self):
        """Rows here, files by the caller — the same split delete_lesson uses."""
        store = _store()
        store.save_review_session("sess-1", "no", "2026-09-02", _lesson())
        store.save_audio_file(
            "audio-1", "sess-1", "/media/no/sess-1/0.mp3", section_index=0, section_type="natural_speed"
        )

        paths = store.delete_review_session("sess-1")

        assert paths == ["/media/no/sess-1/0.mp3"]
        assert store.get_review_session("sess-1") is None
        assert store.list_audio_files_for_lesson("sess-1") == []

    def test_deleting_an_unknown_session_is_not_an_error(self):
        assert _store().delete_review_session("nope") == []

    def test_it_leaves_the_other_sessions_alone(self):
        store = _store()
        store.save_review_session("sess-1", "no", "2026-09-02", _lesson())
        store.save_review_session("sess-2", "no", "2026-09-02", _lesson())

        store.delete_review_session("sess-1")

        assert [row["id"] for row in store.list_review_sessions("no")] == ["sess-2"]
