"""Rewriting a review session's blob must not disturb its coverage pair.

``regen_key_phrases.py`` walks stored content and rewrites the KEY_PHRASES
section. It covered ``lessons`` only, so ``review-of-forgotten-collocations``
kept its doubled buildup rungs after every other lesson was converted
(measured 2026-09-05: 4 adjacent duplicate cues, all in that one session).

The obvious way to extend it — call ``save_review_session`` with the new
Lesson — is a TRAP. That method is ``INSERT OR REPLACE`` over the whole row and
takes ``review_requested`` / ``review_used`` as arguments, so omitting them
writes NULL. Its own docstring says why that matters: ``None`` is "never
measured" and renders as no readout at all, while ``[]`` is a measured zero.
A re-render would silently delete the coverage readout of every session it
touched, and nothing on screen would say so.

``update_review_session_data`` is the narrow counterpart, mirroring
``update_lesson_data``: it rewrites the blob and the denormalised title, and
touches no other column.
"""

from __future__ import annotations

import json

import pytest

from app.models.lesson import KeyPhraseInfo, Lesson, Phrase, Section, SectionType
from app.storage.store import ContentStore


def _lesson(title: str = "Review of forgotten collocations") -> Lesson:
    return Lesson(
        title=title,
        language_code="no",
        sections=[
            Section(
                section_type=SectionType.KEY_PHRASES,
                phrases=[Phrase(text="derimot", voice_id="nb-NO-PernilleNeural", language_code="no")],
            )
        ],
        key_phrases=[KeyPhraseInfo(phrase="derimot", translation="on the other hand")],
    )


@pytest.fixture
def store() -> ContentStore:
    s = ContentStore(":memory:")
    s.save_review_session(
        "sess-1",
        "no",
        "2026-09-02",
        _lesson(),
        review_requested=["derimot", "dessuten"],
        review_used=["derimot"],
    )
    return s


def _raw(store: ContentStore, sid: str) -> dict:
    with store._get_conn() as conn:
        row = conn.execute(
            "SELECT title, data_json, review_requested_json, review_used_json,"
            " language_code, session_date FROM review_sessions WHERE id = ?",
            (sid,),
        ).fetchone()
    return dict(row)


class TestUpdateReviewSessionData:
    def test_it_rewrites_the_blob(self, store):
        updated = _lesson()
        updated.sections[0].phrases.append(Phrase(text="dessuten", voice_id="nb-NO-PernilleNeural", language_code="no"))
        assert store.update_review_session_data("sess-1", updated) is True
        assert [p.text for p in store.get_review_session("sess-1").sections[0].phrases] == ["derimot", "dessuten"]

    def test_it_updates_the_denormalised_title(self, store):
        """``list_review_sessions`` reads ``title`` rather than deserialising a
        Lesson per row, so a stale title would show in the dated list."""
        store.update_review_session_data("sess-1", _lesson(title="Renamed"))
        assert _raw(store, "sess-1")["title"] == "Renamed"

    def test_THE_CONTROL_the_coverage_pair_survives(self, store):
        """The whole reason this method exists.

        ``save_review_session`` would null both columns here, and the loss is
        invisible: the readout simply stops rendering. Swap the implementation
        for a ``save_review_session`` call and only this test fails.
        """
        before = _raw(store, "sess-1")
        store.update_review_session_data("sess-1", _lesson(title="Renamed"))
        after = _raw(store, "sess-1")
        assert json.loads(after["review_requested_json"]) == ["derimot", "dessuten"]
        assert json.loads(after["review_used_json"]) == ["derimot"]
        assert after["review_requested_json"] == before["review_requested_json"]
        assert after["review_used_json"] == before["review_used_json"]

    def test_it_touches_no_other_column(self, store):
        before = _raw(store, "sess-1")
        store.update_review_session_data("sess-1", _lesson())
        after = _raw(store, "sess-1")
        assert after["language_code"] == before["language_code"]
        assert after["session_date"] == before["session_date"]

    def test_it_persists_to_an_ON_DISK_store(self, tmp_path):
        """The path the migration actually runs on.

        Every other test here uses ``:memory:``, where ``_get_conn`` commits
        explicitly. On disk that branch is skipped and the context manager owns
        the commit — so this is the arm that decides whether a real
        ``regen_key_phrases.py --go`` run survives process exit. Read back
        through a SECOND store instance, since the first one's connection would
        see an uncommitted write either way.
        """
        path = str(tmp_path / "content.db")
        writer = ContentStore(path)
        writer.save_review_session(
            "sess-1",
            "no",
            "2026-09-02",
            _lesson(),
            review_requested=["derimot"],
            review_used=[],
        )
        assert writer.update_review_session_data("sess-1", _lesson(title="Renamed")) is True

        reader = ContentStore(path)
        assert reader.get_review_session("sess-1").title == "Renamed"
        assert json.loads(_raw(reader, "sess-1")["review_requested_json"]) == ["derimot"]

    def test_absent_session_returns_false(self, store):
        """Mirrors ``update_lesson_data``: a missing row is False, not a raise,
        so a migration walking stale ids does not abort."""
        assert store.update_review_session_data("nope", _lesson()) is False
