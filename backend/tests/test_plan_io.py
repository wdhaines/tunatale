"""Curriculum plan export/import round-trip (plan_io.py).

Day plans become an editable source artifact, exactly like lessons already are
(see lesson_io.py). This module validates, exports, and imports curriculum day
plans independently of the LLM generation pipeline.
"""

from __future__ import annotations

import pytest

from app.models.curriculum import Curriculum, CurriculumDay
from app.storage.plan_io import (
    export_plan,
    get_planner_state,
    import_plan,
    validate_plan_days,
)
from app.storage.store import ContentStore


@pytest.fixture
def store():
    with ContentStore(":memory:") as s:
        yield s


def _curriculum() -> Curriculum:
    return Curriculum(
        id="test-coffee",
        topic="ordering coffee",
        language_code="sl",
        cefr_level="A2",
        days=[
            CurriculumDay(
                day=1,
                title="First Day",
                focus="greetings",
                collocations=["dober dan"],
                learning_objective="say hello",
                story_guidance="café scene",
            ),
            CurriculumDay(
                day=2,
                title="Second Day",
                focus="ordering",
                collocations=["prosim kavo", "hvala"],
                learning_objective="order coffee",
            ),
        ],
    )


def _plan_dict(**overrides) -> dict:
    d = {
        "id": "test-coffee",
        "topic": "ordering coffee",
        "language_code": "sl",
        "cefr_level": "A2",
        "days": [
            {
                "day": 1,
                "title": "First Day",
                "focus": "greetings",
                "collocations": ["dober dan"],
                "learning_objective": "say hello",
                "story_guidance": "café scene",
            },
            {
                "day": 2,
                "title": "Second Day",
                "focus": "ordering",
                "collocations": ["prosim kavo", "hvala"],
                "learning_objective": "order coffee",
                "story_guidance": "",
            },
        ],
    }
    d.update(overrides)
    return d


class TestValidatePlanDays:
    def test_valid_days_passes(self):
        validate_plan_days(_plan_dict()["days"])

    def test_days_must_be_a_list(self):
        with pytest.raises(ValueError, match="days must be a list"):
            validate_plan_days("not a list")

    def test_days_must_be_non_empty(self):
        with pytest.raises(ValueError, match="days must be a non-empty list"):
            validate_plan_days([])

    def test_entry_must_be_a_dict(self):
        with pytest.raises(ValueError, match=r"days\[0\] must be an object"):
            validate_plan_days(["not a dict"])

    def test_entry_must_be_a_dict_nested(self):
        with pytest.raises(ValueError, match=r"days\[1\] must be an object"):
            validate_plan_days(
                [{"day": 1, "title": "a", "focus": "b", "collocations": ["x"], "learning_objective": "c"}, 42]
            )

    def test_missing_day(self):
        days = _plan_dict()["days"]
        del days[0]["day"]
        with pytest.raises(ValueError, match=r"days\[0\].*'day'"):
            validate_plan_days(days)

    def test_day_not_int(self):
        days = _plan_dict()["days"]
        days[0]["day"] = "one"
        with pytest.raises(ValueError, match=r"days\[0\].day must be an integer >= 1"):
            validate_plan_days(days)

    def test_day_less_than_one(self):
        days = _plan_dict()["days"]
        days[0]["day"] = 0
        with pytest.raises(ValueError, match=r"days\[0\].day must be an integer >= 1"):
            validate_plan_days(days)

    def test_missing_title(self):
        days = _plan_dict()["days"]
        del days[1]["title"]
        with pytest.raises(ValueError, match=r"days\[1\].*'title'"):
            validate_plan_days(days)

    def test_title_not_string(self):
        days = _plan_dict()["days"]
        days[0]["title"] = 42
        with pytest.raises(ValueError, match=r"days\[0\].title must be a non-empty string"):
            validate_plan_days(days)

    def test_title_empty(self):
        days = _plan_dict()["days"]
        days[0]["title"] = ""
        with pytest.raises(ValueError, match=r"days\[0\].title must be a non-empty string"):
            validate_plan_days(days)

    def test_title_whitespace_only(self):
        days = _plan_dict()["days"]
        days[0]["title"] = "   "
        with pytest.raises(ValueError, match=r"days\[0\].title must be a non-empty string"):
            validate_plan_days(days)

    def test_missing_focus(self):
        days = _plan_dict()["days"]
        del days[0]["focus"]
        with pytest.raises(ValueError, match=r"days\[0\].*'focus'"):
            validate_plan_days(days)

    def test_focus_empty(self):
        days = _plan_dict()["days"]
        days[1]["focus"] = ""
        with pytest.raises(ValueError, match=r"days\[1\].focus must be a non-empty string"):
            validate_plan_days(days)

    def test_missing_collocations(self):
        days = _plan_dict()["days"]
        del days[0]["collocations"]
        with pytest.raises(ValueError, match=r"days\[0\].*'collocations'"):
            validate_plan_days(days)

    def test_collocations_not_list(self):
        days = _plan_dict()["days"]
        days[1]["collocations"] = "not a list"
        with pytest.raises(ValueError, match=r"days\[1\].collocations must be a non-empty list"):
            validate_plan_days(days)

    def test_collocations_empty_list(self):
        days = _plan_dict()["days"]
        days[1]["collocations"] = []
        with pytest.raises(ValueError, match=r"days\[1\].collocations must be a non-empty list"):
            validate_plan_days(days)

    def test_collocations_non_string_element(self):
        days = _plan_dict()["days"]
        days[0]["collocations"].append(42)
        with pytest.raises(ValueError, match=r"days\[0\].collocations\[1\].*non-empty"):
            validate_plan_days(days)

    def test_collocations_empty_string_element(self):
        days = _plan_dict()["days"]
        days[0]["collocations"].append("")
        with pytest.raises(ValueError, match=r"days\[0\].collocations\[1\].*non-empty"):
            validate_plan_days(days)

    def test_missing_learning_objective(self):
        days = _plan_dict()["days"]
        del days[1]["learning_objective"]
        with pytest.raises(ValueError, match=r"days\[1\].*'learning_objective'"):
            validate_plan_days(days)

    def test_learning_objective_empty(self):
        days = _plan_dict()["days"]
        days[1]["learning_objective"] = ""
        with pytest.raises(ValueError, match=r"days\[1\].learning_objective must be a non-empty string"):
            validate_plan_days(days)

    def test_story_guidance_must_be_str_when_present(self):
        days = _plan_dict()["days"]
        days[0]["story_guidance"] = 42
        with pytest.raises(ValueError, match=r"days\[0\].story_guidance must be a string"):
            validate_plan_days(days)

    def test_story_guidance_null_rejected(self):
        days = _plan_dict()["days"]
        days[0]["story_guidance"] = None
        with pytest.raises(ValueError, match=r"days\[0\].story_guidance must be a string"):
            validate_plan_days(days)

    def test_unknown_field_rejected(self):
        days = _plan_dict()["days"]
        days[0]["notes"] = "stray field from a hand edit"
        with pytest.raises(ValueError, match=r"days\[0\] has unknown field 'notes'"):
            validate_plan_days(days)

    def test_day_bool_rejected(self):
        days = _plan_dict()["days"]
        days[0]["day"] = True
        with pytest.raises(ValueError, match=r"days\[0\].day must be an integer >= 1"):
            validate_plan_days(days)

    def test_story_guidance_absent_is_ok(self):
        days = _plan_dict()["days"]
        del days[0]["story_guidance"]
        validate_plan_days(days)

    def test_start_day_contiguous_accept(self):
        days = _plan_dict()["days"]
        validate_plan_days(days, start_day=1)

    def test_start_day_rejects_wrong_first(self):
        days = _plan_dict()["days"]
        days[0]["day"] = 3
        with pytest.raises(ValueError, match=r"days\[0\].day must be 1 \(got 3\)"):
            validate_plan_days(days, start_day=1)

    def test_start_day_rejects_non_contiguous(self):
        days = _plan_dict()["days"]
        days[1]["day"] = 9
        with pytest.raises(ValueError, match=r"days\[1\].day must be 2 \(got 9\)"):
            validate_plan_days(days, start_day=1)

    def test_start_day_5_accepts(self):
        days = [
            {"day": 5, "title": "Day Five", "focus": "t", "collocations": ["a"], "learning_objective": "o"},
            {"day": 6, "title": "Day Six", "focus": "t", "collocations": ["b"], "learning_objective": "o"},
        ]
        validate_plan_days(days, start_day=5)

    def test_start_day_5_rejects_gap(self):
        days = [
            {"day": 5, "title": "Day Five", "focus": "t", "collocations": ["a"], "learning_objective": "o"},
            {"day": 7, "title": "Day Seven", "focus": "t", "collocations": ["b"], "learning_objective": "o"},
        ]
        with pytest.raises(ValueError, match=r"days\[1\].day must be 6 \(got 7\)"):
            validate_plan_days(days, start_day=5)


class TestExportPlan:
    def test_unknown_id_raises_key_error(self, store):
        with pytest.raises(KeyError):
            export_plan(store, "no-such-id")

    def test_returns_expected_structure(self, store):
        c = _curriculum()
        store.save_curriculum(c.id, c)
        out = export_plan(store, c.id)
        assert out["id"] == "test-coffee"
        assert out["topic"] == "ordering coffee"
        assert out["language_code"] == "sl"
        assert out["cefr_level"] == "A2"
        assert len(out["days"]) == 2

    def test_days_sorted(self, store):
        c = Curriculum(
            id="c1",
            topic="test",
            language_code="sl",
            cefr_level="A2",
            days=[
                CurriculumDay(day=3, title="D3", focus="f", collocations=["a"], learning_objective="o"),
                CurriculumDay(day=1, title="D1", focus="f", collocations=["a"], learning_objective="o"),
                CurriculumDay(day=2, title="D2", focus="f", collocations=["a"], learning_objective="o"),
            ],
        )
        store.save_curriculum("c1", c)
        out = export_plan(store, "c1")
        assert [d["day"] for d in out["days"]] == [1, 2, 3]

    def test_excludes_metadata(self, store):
        c = _curriculum()
        c.metadata = {"planner": {"chat": [], "proposed": None, "feedback": []}}
        store.save_curriculum(c.id, c)
        out = export_plan(store, c.id)
        assert "metadata" not in out

    def test_days_include_all_six_fields(self, store):
        c = _curriculum()
        store.save_curriculum(c.id, c)
        out = export_plan(store, c.id)
        for day in out["days"]:
            assert set(day.keys()) == {"day", "title", "focus", "collocations", "learning_objective", "story_guidance"}


class TestImportPlan:
    def test_new_id_minted_when_id_absent(self, store):
        file = _plan_dict()
        del file["id"]
        cid, curriculum = import_plan(store, file)
        assert cid.startswith("ordering-coffee-")
        assert curriculum.topic == "ordering coffee"
        assert curriculum.language_code == "sl"
        assert curriculum.cefr_level == "A2"
        assert len(curriculum.days) == 2

    def test_new_curriculum_persisted(self, store):
        file = _plan_dict()
        del file["id"]
        cid, _ = import_plan(store, file)
        restored = store.get_curriculum(cid)
        assert restored is not None
        assert restored.topic == "ordering coffee"

    def test_same_id_preserves_metadata(self, store):
        existing = _curriculum()
        existing.metadata = {
            "planner": {"chat": [{"role": "user", "content": "hello"}], "proposed": None, "feedback": []}
        }
        store.save_curriculum(existing.id, existing)
        file = _plan_dict()
        file["days"] = [
            {
                "day": 1,
                "title": "Updated Day",
                "focus": "new focus",
                "collocations": ["new phrase"],
                "learning_objective": "new objective",
                "story_guidance": "",
            },
        ]
        cid, curriculum = import_plan(store, file)
        assert cid == "test-coffee"
        assert curriculum.metadata["planner"]["chat"] == [{"role": "user", "content": "hello"}]
        assert curriculum.days[0].title == "Updated Day"

    def test_same_id_clears_stale_proposal_keeps_chat_and_feedback(self, store):
        """A pending proposal was numbered against the pre-import day list; a
        re-import can renumber/remove days, so committing it afterwards would
        produce colliding day numbers. Import keeps chat/feedback (the hand-edit
        round-trip contract) but drops the proposal."""
        existing = _curriculum()
        existing.metadata = {
            "planner": {
                "chat": [{"role": "user", "content": "hello"}],
                "proposed": {"start_day": 3, "days": [{"day": 3}]},
                "feedback": [{"day": 1, "note": "great"}],
            }
        }
        store.save_curriculum(existing.id, existing)

        _, curriculum = import_plan(store, _plan_dict())

        planner = curriculum.metadata["planner"]
        assert planner["proposed"] is None
        assert planner["chat"] == [{"role": "user", "content": "hello"}]
        assert planner["feedback"] == [{"day": 1, "note": "great"}]
        assert store.get_curriculum(existing.id).metadata["planner"]["proposed"] is None

    def test_unknown_given_id_raises_key_error(self, store):
        file = _plan_dict(id="no-such-id")
        with pytest.raises(KeyError):
            import_plan(store, file)

    def test_missing_topic_rejected(self, store):
        file = _plan_dict()
        del file["topic"]
        with pytest.raises(ValueError, match="topic must be a non-empty string"):
            import_plan(store, file)

    def test_empty_cefr_level_rejected(self, store):
        file = _plan_dict(cefr_level="")
        with pytest.raises(ValueError, match="cefr_level must be a non-empty string"):
            import_plan(store, file)

    def test_missing_language_code_rejected(self, store):
        file = _plan_dict()
        del file["language_code"]
        with pytest.raises(ValueError, match="language_code must be a non-empty string"):
            import_plan(store, file)

    def test_validates_days(self, store):
        file = _plan_dict()
        file["days"] = []
        with pytest.raises(ValueError, match="days must be a non-empty list"):
            import_plan(store, file)

    def test_validates_days_start_at_1(self, store):
        file = _plan_dict()
        file["days"] = [
            {
                "day": 2,
                "title": "Day Two",
                "focus": "f",
                "collocations": ["a"],
                "learning_objective": "o",
                "story_guidance": "",
            },
        ]
        with pytest.raises(ValueError, match=r"days\[0\].day must be 1 \(got 2\)"):
            import_plan(store, file)


class TestRoundTrip:
    def test_export_import_export_days_equal(self, store):
        c = _curriculum()
        store.save_curriculum(c.id, c)
        exported = export_plan(store, c.id)
        new_id, _ = import_plan(store, exported)
        re_exported = export_plan(store, new_id)
        assert re_exported["days"] == exported["days"]

    def test_export_import_export_preserves_topic_and_level(self, store):
        c = _curriculum()
        store.save_curriculum(c.id, c)
        exported = export_plan(store, c.id)
        new_id, _ = import_plan(store, exported)
        re_exported = export_plan(store, new_id)
        assert re_exported["topic"] == "ordering coffee"
        assert re_exported["cefr_level"] == "A2"
        assert re_exported["language_code"] == "sl"


class TestGetPlannerState:
    def test_default_when_metadata_has_no_planner(self):
        c = _curriculum()
        state = get_planner_state(c)
        assert state == {"chat": [], "proposed": None, "feedback": []}

    def test_default_when_metadata_empty(self):
        c = _curriculum()
        c.metadata = {}
        state = get_planner_state(c)
        assert state == {"chat": [], "proposed": None, "feedback": []}

    def test_passthrough_when_planner_present(self):
        c = _curriculum()
        planner = {"chat": [{"role": "user", "content": "hi"}], "proposed": {"days": []}, "feedback": ["looks good"]}
        c.metadata = {"planner": planner}
        state = get_planner_state(c)
        assert state == planner

    def test_no_mutation(self):
        c = _curriculum()
        c.metadata = {"planner": {"chat": [], "proposed": None, "feedback": []}}
        state = get_planner_state(c)
        state["proposed"] = {"days": [{"day": 1}]}
        assert c.metadata["planner"]["proposed"] is None
