"""Curriculum days grouped by a book's lessons, in an order of their own (tunatale-cs3p slice 1).

``day`` is a stable key that lessons, listens and pipeline jobs hang off, so the
book's order cannot come from renumbering it. A day carries an optional
``group`` / ``group_title`` and an ``order``; display order (and so every
learner-facing ``position``) follows ``order`` when it is set.

The fixture is the live Wolff curriculum's shape on 2026-11-22: days 1-2 are
Lesson 1's dialogues, 3-4 Lesson 2's, 5-7 Lesson 1's practice and 8-12 Lesson
2's practice. Design: .beads-tasks/briefs/design-curriculum-page-2026-11.md
(oracle C1, C2).
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.curriculum import Curriculum, CurriculumDay
from app.storage.plan_io import export_plan, import_plan, validate_plan_days
from app.storage.store import ContentStore

# day key -> (group, group title); list order is the book's order.
WOLFF_BOOK_ORDER = [
    (1, "wolff-1", "Lesson 1"),
    (2, "wolff-1", "Lesson 1"),
    (5, "wolff-1", "Lesson 1"),
    (6, "wolff-1", "Lesson 1"),
    (7, "wolff-1", "Lesson 1"),
    (3, "wolff-2", "Lesson 2"),
    (4, "wolff-2", "Lesson 2"),
    (8, "wolff-2", "Lesson 2"),
    (9, "wolff-2", "Lesson 2"),
    (10, "wolff-2", "Lesson 2"),
    (11, "wolff-2", "Lesson 2"),
    (12, "wolff-2", "Lesson 2"),
]


def _day(day: int, **kw) -> CurriculumDay:
    return CurriculumDay(day=day, title=f"t{day}", focus="f", collocations=["hei"], learning_objective="o", **kw)


def _wolff() -> Curriculum:
    days = [_day(d, group=g, group_title=t, order=i) for i, (d, g, t) in enumerate(WOLFF_BOOK_ORDER, start=1)]
    # Stored in day-key order, as the live row is: the order must come from the field.
    days.sort(key=lambda d: d.day)
    return Curriculum(id="wolff", topic="Wolff", language_code="no", cefr_level="A1", days=days)


class TestModel:
    def test_a_day_has_no_group_and_no_order_by_default(self):
        d = _day(1)
        assert (d.group, d.group_title, d.order) == ("", "", None)

    def test_ordered_days_follow_order_not_day_key(self):
        assert [d.day for d in _wolff().ordered_days()] == [d for d, _, _ in WOLFF_BOOK_ORDER]

    def test_positions_follow_the_books_order(self):
        """Every learner-facing "Day N" reads position, so it must follow the book."""
        positions = _wolff().day_positions()
        assert [positions[d] for d, _, _ in WOLFF_BOOK_ORDER] == list(range(1, 13))
        assert positions[5] == 3  # Lesson 1 practice 1 comes straight after its dialogues

    def test_without_order_days_keep_day_key_order(self):
        """C2: a curriculum with no order reads exactly as before."""
        c = Curriculum(id="c", topic="t", language_code="no", cefr_level="A1", days=[_day(7), _day(2), _day(4)])
        assert [d.day for d in c.ordered_days()] == [2, 4, 7]
        assert c.day_positions() == {2: 1, 4: 2, 7: 3}

    def test_days_added_without_an_order_follow_the_ordered_ones(self):
        """A planner batch committed after the book's days carries no order: it goes last, by key."""
        c = _wolff()
        c.days += [_day(14), _day(13)]
        assert [d.day for d in c.ordered_days()][-3:] == [12, 13, 14]

    def test_order_ties_break_by_day_key(self):
        c = Curriculum(
            id="c", topic="t", language_code="no", cefr_level="A1", days=[_day(3, order=1), _day(2, order=1)]
        )
        assert [d.day for d in c.ordered_days()] == [2, 3]

    @pytest.mark.parametrize("bad", [0, -1])
    def test_order_must_be_positive(self, bad):
        with pytest.raises(ValueError, match="order must be ≥ 1"):
            _day(1, order=bad)

    def test_json_round_trip_keeps_the_grouping(self):
        c = _wolff()
        back = Curriculum.from_json(c.to_json())
        assert [(d.day, d.group, d.group_title, d.order) for d in back.ordered_days()] == [
            (d, g, t, i) for i, (d, g, t) in enumerate(WOLFF_BOOK_ORDER, start=1)
        ]

    def test_a_row_saved_before_grouping_existed_loads(self):
        """Live rows hold no group fields; they load ungrouped and unordered."""
        raw = (
            '{"id": "c", "topic": "t", "language_code": "no", "cefr_level": "A1", "metadata": {}, "days": '
            '[{"day": 1, "title": "a", "focus": "f", "collocations": [], "learning_objective": "o"}]}'
        )
        d = Curriculum.from_json(raw).days[0]
        assert (d.group, d.group_title, d.order) == ("", "", None)


def _plan_day(day: int, **kw) -> dict:
    return {"day": day, "title": f"t{day}", "focus": "f", "collocations": ["hei"], "learning_objective": "o", **kw}


class TestPlanFile:
    def test_group_fields_are_accepted(self):
        validate_plan_days([_plan_day(1, group="wolff-1", group_title="Lesson 1", order=1)])

    @pytest.mark.parametrize(
        ("field", "value", "message"),
        [
            ("group", 3, r"days\[0\].group must be a string"),
            ("group_title", None, r"days\[0\].group_title must be a string"),
            ("order", 0, r"days\[0\].order must be an integer >= 1"),
            ("order", "2", r"days\[0\].order must be an integer >= 1"),
            ("order", True, r"days\[0\].order must be an integer >= 1"),
        ],
    )
    def test_group_fields_are_checked(self, field, value, message):
        with pytest.raises(ValueError, match=message):
            validate_plan_days([_plan_day(1, **{field: value})])

    def test_order_may_be_null(self):
        """An exported ungrouped day carries ``order: null``; it must import back."""
        validate_plan_days([_plan_day(1, order=None)])

    def test_export_then_import_keeps_the_grouping(self):
        """The hand-edit route: export, add groups, import. This is how a live plan gets its blocks."""
        with ContentStore(":memory:") as store:
            c = _wolff()
            store.save_curriculum(c.id, c)
            plan = export_plan(store, c.id)
            assert [d["day"] for d in plan["days"]] == list(range(1, 13))  # the file stays in key order
            _, back = import_plan(store, plan)
            assert [(d.day, d.group, d.order) for d in back.ordered_days()] == [
                (d, g, i) for i, (d, g, _) in enumerate(WOLFF_BOOK_ORDER, start=1)
            ]


@pytest.mark.asyncio
class TestGetCurriculum:
    async def _get(self, curriculum: Curriculum) -> dict:
        store = ContentStore(":memory:")
        store.save_curriculum(curriculum.id, curriculum)
        app.state.content_store = store
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(f"/api/curriculum/{curriculum.id}")
        assert response.status_code == 200
        return response.json()

    async def test_days_come_in_the_books_order_with_their_groups(self):
        """C1: two blocks, Lesson 1 (5 rows) then Lesson 2 (7 rows); every day key unchanged."""
        data = await self._get(_wolff())
        rows = [(d["day"], d["group"], d["group_title"], d["position"]) for d in data["days"]]
        assert rows == [(d, g, t, i) for i, (d, g, t) in enumerate(WOLFF_BOOK_ORDER, start=1)]
        assert sorted(d["day"] for d in data["days"]) == list(range(1, 13))

    async def test_an_ungrouped_curriculum_is_unchanged(self):
        """C2: no groups, day-key order, contiguous positions."""
        c = Curriculum(id="c", topic="t", language_code="no", cefr_level="A1", days=[_day(4), _day(1), _day(2)])
        data = await self._get(c)
        assert [(d["day"], d["group"], d["position"]) for d in data["days"]] == [(1, "", 1), (2, "", 2), (4, "", 3)]
