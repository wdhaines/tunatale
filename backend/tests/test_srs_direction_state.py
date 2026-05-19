"""Tests for per-direction DirectionState in the SRS database."""

from dataclasses import replace
from datetime import date

from app.models.srs_item import Direction, DirectionState, SRSState
from app.models.syntactic_unit import SyntacticUnit


def _unit(text: str = "banka", translation: str = "bank") -> SyntacticUnit:
    return SyntacticUnit(text=text, translation=translation, word_count=1, difficulty=1, source="corpus")


class TestDirectionState:
    def test_srs_item_has_directions_dict(self, srs_db):
        srs_db.add_collocation(_unit(), language_code="sl")
        item = srs_db.get_collocation("banka")
        assert item is not None
        assert isinstance(item.directions, dict)
        assert Direction.RECOGNITION in item.directions
        assert Direction.PRODUCTION in item.directions

    def test_direction_state_is_direction_state_instance(self, srs_db):
        srs_db.add_collocation(_unit(), language_code="sl")
        item = srs_db.get_collocation("banka")
        assert isinstance(item.directions[Direction.RECOGNITION], DirectionState)
        assert isinstance(item.directions[Direction.PRODUCTION], DirectionState)

    def test_recognition_shim_state_matches_direction(self, srs_db):
        srs_db.add_collocation(_unit(), language_code="sl")
        item = srs_db.get_collocation("banka")
        assert item.state == item.directions[Direction.RECOGNITION].state

    def test_recognition_shim_due_date_matches_direction(self, srs_db):
        srs_db.add_collocation(_unit(), language_code="sl")
        item = srs_db.get_collocation("banka")
        assert item.due_date == item.directions[Direction.RECOGNITION].due_at.date()

    def test_recognition_shim_stability_matches_direction(self, srs_db):
        srs_db.add_collocation(_unit(), language_code="sl")
        item = srs_db.get_collocation("banka")
        assert item.stability == item.directions[Direction.RECOGNITION].stability

    def test_shim_setter_updates_recognition_direction(self, srs_db):
        srs_db.add_collocation(_unit(), language_code="sl")
        item = srs_db.get_collocation("banka")
        item.reps = 7
        assert item.directions[Direction.RECOGNITION].reps == 7

    def test_update_direction_persists_recognition(self, srs_db):
        srs_db.add_collocation(_unit(), language_code="sl")
        item = srs_db.get_collocation("banka")
        new_rec = replace(
            item.directions[Direction.RECOGNITION],
            state=SRSState.REVIEW,
            reps=3,
            stability=12.0,
        )
        srs_db.update_direction(item.guid, Direction.RECOGNITION, new_rec)

        updated = srs_db.get_collocation("banka")
        assert updated.directions[Direction.RECOGNITION].state == SRSState.REVIEW
        assert updated.directions[Direction.RECOGNITION].reps == 3
        assert updated.directions[Direction.RECOGNITION].stability == 12.0

    def test_update_direction_does_not_affect_production(self, srs_db):
        srs_db.add_collocation(_unit(), language_code="sl")
        item = srs_db.get_collocation("banka")
        new_rec = replace(item.directions[Direction.RECOGNITION], reps=5, state=SRSState.REVIEW)
        srs_db.update_direction(item.guid, Direction.RECOGNITION, new_rec)

        updated = srs_db.get_collocation("banka")
        assert updated.directions[Direction.PRODUCTION].state == SRSState.NEW
        assert updated.directions[Direction.PRODUCTION].reps == 0

    def test_item_has_guid(self, srs_db):
        srs_db.add_collocation(_unit(), language_code="sl")
        item = srs_db.get_collocation("banka")
        assert item.guid is not None
        assert len(item.guid) == 16

    def test_get_collocation_by_guid(self, srs_db):
        srs_db.add_collocation(_unit(), language_code="sl")
        item = srs_db.get_collocation("banka")
        fetched = srs_db.get_collocation_by_guid(item.guid)
        assert fetched is not None
        assert fetched.syntactic_unit.text == "banka"

    def test_update_collocation_shim_proxies_recognition(self, srs_db):
        """Existing update_collocation shim must keep working."""
        srs_db.add_collocation(_unit(), language_code="sl")
        item = srs_db.get_collocation("banka")
        item.reps = 5
        item.stability = 20.0
        item.state = SRSState.REVIEW
        srs_db.update_collocation(item)

        updated = srs_db.get_collocation("banka")
        assert updated.reps == 5
        assert updated.stability == 20.0
        assert updated.state == SRSState.REVIEW

    def test_production_direction_has_spread_due_date(self, srs_db):
        """Production direction due date is today or within 30 days."""
        from datetime import timedelta

        srs_db.add_collocation(_unit(), language_code="sl")
        item = srs_db.get_collocation("banka")
        prod_due = item.directions[Direction.PRODUCTION].due_at.date()
        today = date.today()
        assert today <= prod_due <= today + timedelta(days=30)

    def test_last_review_shim_setter_updates_recognition(self, srs_db):
        srs_db.add_collocation(_unit(), language_code="sl")
        item = srs_db.get_collocation("banka")
        today = date.today()
        item.last_review = today
        assert item.directions[Direction.RECOGNITION].last_review == today

    def test_update_collocation_shim_works_when_guid_is_none(self, srs_db):
        """Legacy flows without guid fall back to text lookup."""
        srs_db.add_collocation(_unit(), language_code="sl")
        item = srs_db.get_collocation("banka")
        item.guid = None
        item.reps = 9
        srs_db.update_collocation(item)
        updated = srs_db.get_collocation("banka")
        assert updated.reps == 9

    def test_update_collocation_shim_noop_for_unknown_text(self, srs_db):
        """Unknown text with guid=None short-circuits without raising."""
        from app.models.srs_item import SRSItem

        phantom = SRSItem(syntactic_unit=_unit("missing", "?"))
        phantom.guid = None
        srs_db.update_collocation(phantom)  # no row; should not raise

    def test_update_direction_noop_for_unknown_guid(self, srs_db):
        srs_db.add_collocation(_unit(), language_code="sl")
        item = srs_db.get_collocation("banka")
        rec = item.directions[Direction.RECOGNITION]
        srs_db.update_direction("0" * 16, Direction.RECOGNITION, rec)
        # Original row untouched
        reloaded = srs_db.get_collocation("banka")
        assert reloaded.reps == item.reps

    def test_get_collocation_by_guid_missing_returns_none(self, srs_db):
        assert srs_db.get_collocation_by_guid("deadbeef" * 2) is None

    def test_update_collocation_fields_noop_for_missing_id(self, srs_db):
        srs_db.update_collocation_fields(9999, text="x", translation="y")  # no-op

    def test_reset_collocation_resets_specific_direction(self, srs_db):
        srs_db.add_collocation(_unit(), language_code="sl")
        item = srs_db.get_collocation("banka")
        # Dirty both directions so we can see which gets reset.
        rec = replace(item.directions[Direction.RECOGNITION], reps=3, stability=10.0, state=SRSState.REVIEW)
        prod = replace(item.directions[Direction.PRODUCTION], reps=4, stability=15.0, state=SRSState.REVIEW)
        srs_db.update_direction(item.guid, Direction.RECOGNITION, rec)
        srs_db.update_direction(item.guid, Direction.PRODUCTION, prod)

        rows, _ = srs_db.list_collocations()
        row_id = rows[0][0]
        srs_db.reset_collocation(row_id, direction=Direction.PRODUCTION)

        after = srs_db.get_collocation("banka")
        assert after.directions[Direction.RECOGNITION].reps == 3
        assert after.directions[Direction.PRODUCTION].reps == 0
        assert after.directions[Direction.PRODUCTION].state == SRSState.NEW

    def test_set_state_by_id_targets_specific_direction(self, srs_db):
        srs_db.add_collocation(_unit(), language_code="sl")
        rows, _ = srs_db.list_collocations()
        row_id = rows[0][0]
        srs_db.set_state_by_id(row_id, SRSState.SUSPENDED, direction=Direction.PRODUCTION)

        item = srs_db.get_collocation("banka")
        assert item.directions[Direction.RECOGNITION].state == SRSState.NEW
        assert item.directions[Direction.PRODUCTION].state == SRSState.SUSPENDED
