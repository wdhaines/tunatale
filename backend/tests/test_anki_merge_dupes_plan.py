"""Plan-side tests for ``app.anki.merge_dupes``.

These tests never touch sqlite — they exercise the pure functions that parse
``AnkiNote`` rows into ``ParsedNote`` records, group them by meaning, and build
a ``MergePlan`` describing every keeper/reparent/delete. The apply path is
covered in ``test_anki_merge_dupes_apply.py``.
"""

from __future__ import annotations

import pytest

from app.anki.merge_dupes import (
    MergePlan,
    build_plan,
    group_by_meaning,
    parse_notes,
)
from app.anki.sqlite_reader import AnkiNote
from app.common.guid import compute_guid


def _recognition_note(
    nid: int, slovene: str, english: str, audio: str = "sl_x", image: str = "x.jpg", note: str = ""
) -> AnkiNote:
    front = f'[sound:{audio}.mp3]<div class="slovene">{slovene}</div>'
    back = f'<img src="{image}"><div class="english">{english}</div>'
    if note:
        back += f'<div class="note">{note}</div>'
    return AnkiNote(id=nid, anki_guid=f"g{nid}", mid=1, mod=0, tags=[], fields=[front, back])


def _production_note(
    nid: int, slovene: str, english: str, audio: str = "sl_x", image: str = "x.jpg", note: str = ""
) -> AnkiNote:
    front = f'<div class="img"><img src="{image}"></div>'
    back = f'[sound:{audio}.mp3]<div class="slovene">{slovene}</div><div class="english">{english}</div>'
    if note:
        back += f'<div class="note">{note}</div>'
    return AnkiNote(id=nid, anki_guid=f"g{nid}", mid=1, mod=0, tags=[], fields=[front, back])


def _unknown_note(nid: int, slovene: str, english: str) -> AnkiNote:
    return AnkiNote(
        id=nid,
        anki_guid=f"g{nid}",
        mid=1,
        mod=0,
        tags=[],
        fields=[f'<div class="slovene">{slovene}</div>', f'<div class="english">{english}</div>'],
    )


class TestParseNotes:
    def test_recognition_note_direction_is_recognition(self):
        notes = parse_notes([_recognition_note(1, "pes", "dog")], card_ord_by_note={1: 0}, card_id_by_note={1: 10})
        assert len(notes) == 1
        parsed = notes[0]
        assert parsed.direction == "recognition"
        assert parsed.slovene == "pes"
        assert parsed.english == "dog"
        assert parsed.audio.startswith("[sound:")
        assert "<img" in parsed.image

    def test_production_note_direction_is_production(self):
        notes = parse_notes([_production_note(2, "pes", "dog")], card_ord_by_note={2: 0}, card_id_by_note={2: 20})
        parsed = notes[0]
        assert parsed.direction == "production"
        assert parsed.slovene == "pes"
        assert parsed.english == "dog"

    def test_unknown_direction_kept(self):
        notes = parse_notes([_unknown_note(3, "zmeda", "confusion")], card_ord_by_note={3: 0}, card_id_by_note={3: 30})
        parsed = notes[0]
        assert parsed.direction == "unknown"
        assert parsed.slovene == "zmeda"
        assert parsed.english == "confusion"

    def test_grammar_and_note_extracted(self):
        note = _recognition_note(4, "jabolko", "apple")
        note.fields[1] = (
            '<img src="a.jpg"><div class="english">apple</div><div class="gram">n.</div><div class="note">seed</div>'
        )
        parsed = parse_notes([note], card_ord_by_note={4: 0}, card_id_by_note={4: 40})[0]
        assert parsed.grammar == "n."
        assert parsed.note == "seed"


class TestGroupByMeaning:
    def test_homonyms_separate_when_english_differs(self):
        notes = [
            _recognition_note(1, "barva", "color"),
            _production_note(2, "barva", "color"),
            _recognition_note(3, "barva", "paint"),
            _production_note(4, "barva", "paint"),
        ]
        parsed = parse_notes(
            notes, card_ord_by_note={1: 0, 2: 0, 3: 0, 4: 0}, card_id_by_note={1: 10, 2: 20, 3: 30, 4: 40}
        )
        groups = group_by_meaning(parsed)
        keys = sorted((g.slovene, g.english) for g in groups)
        assert keys == [("barva", "color"), ("barva", "paint")]

    def test_case_insensitive_grouping(self):
        a = _recognition_note(1, "Pes", "Dog")
        b = _production_note(2, "pes", "dog")
        parsed = parse_notes([a, b], card_ord_by_note={1: 0, 2: 0}, card_id_by_note={1: 10, 2: 20})
        groups = group_by_meaning(parsed)
        assert len(groups) == 1

    def test_more_than_one_recognition_per_group_raises(self):
        a = _recognition_note(1, "pes", "dog")
        b = _recognition_note(2, "pes", "dog")
        parsed = parse_notes([a, b], card_ord_by_note={1: 0, 2: 0}, card_id_by_note={1: 10, 2: 20})
        with pytest.raises(RuntimeError, match="(?i)(duplicate|multiple).*recognition|pes"):
            group_by_meaning(parsed)

    def test_more_than_one_production_per_group_raises(self):
        a = _production_note(1, "pes", "dog")
        b = _production_note(2, "pes", "dog")
        parsed = parse_notes([a, b], card_ord_by_note={1: 0, 2: 0}, card_id_by_note={1: 10, 2: 20})
        with pytest.raises(RuntimeError, match="(?i)(duplicate|multiple).*production|pes"):
            group_by_meaning(parsed)


class TestBuildPlan:
    def _plan(
        self, notes: list[AnkiNote], card_ord: dict[int, int] | None = None, card_id: dict[int, int] | None = None
    ) -> MergePlan:
        card_ord = card_ord or {n.id: 0 for n in notes}
        card_id = card_id or {n.id: n.id * 10 for n in notes}
        parsed = parse_notes(notes, card_ord_by_note=card_ord, card_id_by_note=card_id)
        groups = group_by_meaning(parsed)
        return build_plan(
            groups, unknowns=[p for p in parsed if p.direction == "unknown"], new_notetype_mid=999_000_001
        )

    def test_pair_keeps_recognition_reparents_production(self):
        plan = self._plan(
            [
                _recognition_note(1, "pes", "dog"),
                _production_note(2, "pes", "dog"),
            ]
        )
        assert 1 in plan.notes_to_update, "recognition is the keeper"
        assert plan.notes_to_delete == [2]
        assert plan.cards_to_reparent == {20: (1, 1)}

    def test_recognition_only_singleton(self):
        plan = self._plan([_recognition_note(5, "hiša", "house")])
        assert 5 in plan.notes_to_update
        assert plan.notes_to_delete == []
        # No reparenting — Anki auto-generates the Production card on next open
        assert plan.cards_to_reparent == {}

    def test_production_only_singleton_flips_ord(self):
        plan = self._plan([_production_note(6, "okno", "window")])
        assert 6 in plan.notes_to_update
        assert plan.notes_to_delete == []
        # Its own card must be re-assigned to ord=1
        assert plan.cards_to_reparent == {60: (6, 1)}

    def test_unknown_note_left_on_original_notetype(self):
        """Unknown-direction notes must NOT be rewritten — their HTML is unknown
        shape (pronunciation cards, prompt-style, etc.) and blind extraction
        would zero-out the flds. They stay on Basic; backfill_guids handles them."""
        plan = self._plan([_unknown_note(9, "zmeda", "confusion")])
        assert 9 not in plan.notes_to_update
        assert plan.cards_to_reparent == {}
        assert plan.notes_to_delete == []
        assert len(plan.singletons_unknown_direction) == 1
        assert plan.singletons_unknown_direction[0].note_id == 9

    def test_homonym_disambiguation_populates_disambig_key(self):
        """Homonym notes must have bare Slovene + DisambigKey, not a suffixed Slovene."""
        notes = [
            _recognition_note(1, "barva", "color"),
            _production_note(2, "barva", "color"),
            _recognition_note(3, "barva", "paint"),
            _production_note(4, "barva", "paint"),
        ]
        plan = self._plan(notes)
        slovene_texts = {fields.slovene for fields in plan.notes_to_update.values()}
        disambig_keys = {fields.disambig_key for fields in plan.notes_to_update.values()}
        assert slovene_texts == {"barva"}
        assert disambig_keys == {"color", "paint"}

    def test_homonym_produces_distinct_guids(self):
        notes = [
            _recognition_note(1, "barva", "color"),
            _production_note(2, "barva", "color"),
            _recognition_note(3, "barva", "paint"),
            _production_note(4, "barva", "paint"),
        ]
        plan = self._plan(notes)
        guids = {compute_guid(fields.slovene, "sl", fields.disambig_key) for fields in plan.notes_to_update.values()}
        assert len(guids) == 2

    def test_homonym_preserves_note_annotation(self):
        note_notes = [
            _recognition_note(1, "barva", "color", note="⚠ same word as paint"),
            _production_note(2, "barva", "color", note="⚠ same word as paint"),
            _recognition_note(3, "barva", "paint", note="⚠ same word as color"),
            _production_note(4, "barva", "paint", note="⚠ same word as color"),
        ]
        plan = self._plan(note_notes)
        annotations = {fields.note for fields in plan.notes_to_update.values()}
        assert annotations == {"⚠ same word as paint", "⚠ same word as color"}

    def test_singleton_keeps_empty_disambig_key(self):
        """A non-homonym singleton has disambig_key='' and bare slovene."""
        plan = self._plan([_recognition_note(1, "pes", "dog")])
        assert plan.notes_to_update[1].slovene == "pes"
        assert plan.notes_to_update[1].disambig_key == ""

    def test_homonyms_requiring_disambiguation_reported(self):
        notes = [
            _recognition_note(1, "barva", "color"),
            _recognition_note(2, "barva", "paint"),
        ]
        plan = self._plan(notes, card_ord={1: 0, 2: 0}, card_id={1: 10, 2: 20})
        assert ("barva", ["color", "paint"]) in plan.homonyms_requiring_disambiguation or (
            "barva",
            ["paint", "color"],
        ) in plan.homonyms_requiring_disambiguation

    def test_plan_mid_propagated(self):
        plan = self._plan([_recognition_note(1, "pes", "dog")])
        assert plan.new_notetype_mid == 999_000_001


class TestUnifiedFieldsFlds:
    def test_to_flds_emits_seven_fields(self):
        from app.anki.merge_dupes import UnifiedFields

        u = UnifiedFields(
            slovene="barva",
            english="color",
            audio="[sound:x.mp3]",
            image="<img>",
            grammar="n.",
            note="",
            disambig_key="color",
        )
        parts = u.to_flds().split("\x1f")
        assert len(parts) == 7
        assert parts[0] == "barva"
        assert parts[6] == "color"

    def test_to_flds_empty_disambig_key(self):
        from app.anki.merge_dupes import UnifiedFields

        u = UnifiedFields(slovene="pes", english="dog", audio="", image="", grammar="", note="")
        parts = u.to_flds().split("\x1f")
        assert len(parts) == 7
        assert parts[6] == ""
