"""A lesson's drills in its card list, minted as drill cards (tunatale-5i1p.4).

Format: an item ``{"kind": "drill", "drill": <n>, "role": <voice role>}`` in the
story's ``cards`` block, ``n`` the drill's 1-based place in the story's
``drills``. A substitution, transformation or true/false drill makes one
rotating card; a question-and-answer drill makes one card per step.
``validate_story`` refuses an index the story lacks and a drill listed twice
(U6); the mint is idempotent (U7); the audio is the lesson's own takes from
its Drill section, nothing synthesized.

Brief: ``.beads-tasks/briefs/design-rotating-drill-card-2026-11.md`` § Build 1.
"""

from __future__ import annotations

import copy

import pytest

from app.audio.render_cost import section_take
from app.common.guid import compute_guid
from app.generation.section_builder import build_story_drill_section
from app.models.lesson import Lesson, SectionType
from app.models.srs_item import Direction, SRSState
from app.srs.database import SRSDatabase
from app.srs.drill_card import drill_card
from app.srs.lesson_card_mint import attach_lesson_card_audio, mint_lesson_cards
from app.srs.lesson_cards import COURSE_SOURCE, lesson_cards
from app.storage.lesson_io import validate_story
from tests.test_drill_card import DRILLS
from tests.test_lesson_card_list import _story as _sentence_story
from tests.test_lesson_take import NB_A, NB_B, _instrument_paths

ITEMS = [
    {"kind": "drill", "drill": 1, "role": "male-1"},  # substitution
    {"kind": "drill", "drill": 2, "role": "female-1"},  # transformation
    {"kind": "drill", "drill": 4, "role": "male-1"},  # question and answer: one card per step
]


def _cards() -> dict:
    return {"set": "test-01", "items": copy.deepcopy(ITEMS)}


def _story() -> dict:
    """A practice lesson: drills and no dialogue, as the Wolff practice files are."""
    return {"title": "Drills", "drills": copy.deepcopy(DRILLS), "cards": _cards()}


def _keys(db: SRSDatabase) -> list[str]:
    with db._get_conn() as conn:
        return [r["disambig_key"] for r in conn.execute("SELECT disambig_key FROM collocations ORDER BY id")]


EXPECTED_KEYS = [
    "sent:test-01:drill-1",
    "sent:test-01:drill-2",
    "sent:test-01:drill-4:1",
    "sent:test-01:drill-4:2",
    "sent:test-01:drill-4:3",
]


# --------------------------------------------------------------------------
# U6: validation
# --------------------------------------------------------------------------


class TestValidate:
    def test_accepts_drill_items(self, language_no):
        validate_story(_story(), language=language_no)

    def test_drill_items_sit_beside_sentence_items(self, language_no):
        story = _sentence_story()
        story["drills"] = copy.deepcopy(DRILLS)
        story["cards"]["items"].append({"kind": "drill", "drill": 3, "role": "female-1"})
        validate_story(story, language=language_no)

    def test_u6_an_index_the_story_lacks_is_refused_by_path(self):
        story = _story()
        story["cards"]["items"][1]["drill"] = 5
        with pytest.raises(ValueError, match=r"^cards\.items\[1\]\.drill 5 is not a drill of this story \(it has 4\)"):
            validate_story(story)

    def test_u6_a_story_with_no_drills_has_no_index(self):
        story = _sentence_story()
        story["cards"]["items"].append({"kind": "drill", "drill": 1, "role": "female-1"})
        with pytest.raises(ValueError, match=r"^cards\.items\[4\]\.drill 1 is not a drill of this story \(it has 0\)"):
            validate_story(story)

    def test_u6_a_drill_listed_twice_is_refused_by_path(self):
        story = _story()
        story["cards"]["items"].append({"kind": "drill", "drill": 2, "role": "male-1"})
        with pytest.raises(ValueError, match=r"^cards\.items\[3\]\.drill 2 is already listed"):
            validate_story(story)

    @pytest.mark.parametrize("bad", [0, -1, "1", 1.0, True, None])
    def test_drill_is_a_positive_int(self, bad):
        story = _story()
        story["cards"]["items"][0]["drill"] = bad
        with pytest.raises(ValueError, match=r"^cards\.items\[0\]\.drill must be a positive integer"):
            validate_story(story)

    def test_role_is_required(self):
        story = _story()
        del story["cards"]["items"][0]["role"]
        with pytest.raises(ValueError, match=r"^cards\.items\[0\]\.role must be a non-empty string"):
            validate_story(story)

    def test_unknown_role_is_refused_when_language_given(self, language_no):
        story = _story()
        story["cards"]["items"][2]["role"] = "robot-8"
        with pytest.raises(ValueError, match=r"^cards\.items\[2\]\.role 'robot-8' is not in the no voice map"):
            validate_story(story, language=language_no)

    def test_the_kind_message_names_drill(self):
        story = _story()
        story["cards"]["items"][0]["kind"] = "pattern"
        with pytest.raises(ValueError, match=r"^cards\.items\[0\]\.kind must be 'word', 'sentence' or 'drill'"):
            validate_story(story)


# --------------------------------------------------------------------------
# Building and minting
# --------------------------------------------------------------------------


class TestBuild:
    def test_drill_items_build_drill_cards_in_list_order(self):
        built = lesson_cards(_cards(), drills=DRILLS)
        assert [c.unit.disambig_key for c in built] == EXPECTED_KEYS
        assert {c.unit.source for c in built} == {COURSE_SOURCE}
        assert [c.role for c in built] == ["male-1", "female-1", "male-1", "male-1", "male-1"]
        assert all(drill_card(c.unit) is not None for c in built)

    def test_without_the_storys_drills_a_drill_item_is_an_error(self):
        with pytest.raises(IndexError):
            lesson_cards(_cards())


class TestMint:
    def test_mints_every_drill_card_new_with_no_anki_ids(self):
        db = SRSDatabase(":memory:")
        minted = mint_lesson_cards(db, "no", _cards(), drills=DRILLS)
        assert _keys(db) == EXPECTED_KEYS
        for cid, card in minted:
            _, item, _ = db.get_collocation_by_id(cid)
            assert item.anki_note_id is None
            assert list(item.directions) == [Direction.PRODUCTION]
            assert item.directions[Direction.PRODUCTION].state == SRSState.NEW
            assert db.get_collocation_id_by_guid(compute_guid(card.unit.text, "no", card.unit.disambig_key)) == cid

    def test_each_row_is_its_own_sibling_group(self):
        db = SRSDatabase(":memory:")
        minted = mint_lesson_cards(db, "no", _cards(), drills=DRILLS)
        groups = db.sibling_groups()
        assert [groups[cid] for cid, _ in minted] == EXPECTED_KEYS

    def test_u7_the_same_block_minted_twice_adds_nothing(self):
        db = SRSDatabase(":memory:")
        assert len(mint_lesson_cards(db, "no", _cards(), drills=DRILLS)) == 5  # control: the first mint adds
        assert mint_lesson_cards(db, "no", _cards(), drills=copy.deepcopy(DRILLS)) == []
        assert _keys(db) == EXPECTED_KEYS


# --------------------------------------------------------------------------
# Audio: the lesson's own takes, from the Drill section
# --------------------------------------------------------------------------


def _drill_lesson(cache) -> Lesson:
    """The drills as a rendered lesson says them: male-1 = NB_A, female-1 = NB_B (the no voice map)."""
    section = build_story_drill_section(
        DRILLS[:2], [], {"male-1": NB_A, "female-1": NB_B}, narrator_voice="en-US-JennyNeural", l2_code="no"
    )
    lesson = Lesson(title="Drills", language_code="no", sections=[section])
    for (text, voice, enunciated), path in _instrument_paths(lesson, cache).items():
        if not enunciated:
            path.write_bytes(f"{text}|{voice}".encode())
    return lesson


def _dirty(db: SRSDatabase, cid: int) -> str:
    return db.get_dirty_fields(db.get_collocation_by_id(cid)[1].guid)


@pytest.fixture
def dirs(tmp_path):
    cache, media = tmp_path / "cache", tmp_path / "media"
    cache.mkdir()
    media.mkdir()
    return cache, media


class TestAudio:
    def test_section_take_finds_a_drill_clip_natural_speed_never_says(self, dirs):
        cache, _ = dirs
        lesson = _drill_lesson(cache)
        path = section_take(lesson, "De er der.", SectionType.AFFIX_DRILL, cache_dir=cache)
        assert path is not None and path.read_bytes() == f"De er der.|{NB_A}".encode()
        # control: the same text through the Natural Speed section is not there
        assert section_take(lesson, "De er der.", SectionType.NATURAL_SPEED, cache_dir=cache) is None

    def test_every_clip_of_a_rotating_drill_is_attached_from_its_take(self, dirs, language_no):
        cache, media = dirs
        lesson = _drill_lesson(cache)
        story_cards = {"set": "test-01", "items": [{"kind": "drill", "drill": 1, "role": "male-1"}]}
        db = SRSDatabase(":memory:")
        minted = mint_lesson_cards(db, "no", story_cards, drills=DRILLS)

        attach_lesson_card_audio(db, minted, lesson, language_no.tts_voice_map, cache_dir=cache, media_dir=media)

        cid, card = minted[0]
        kinds = drill_card(card.unit).media_kinds()
        assert len(kinds) == 9  # model + 4 cues + 4 answers
        for kind, text in kinds:
            name = db.get_media_filename(cid, kind)
            assert name is not None, kind
            assert (media / name).read_bytes() == f"{text}|{NB_A}".encode()
        assert "audio" in _dirty(db, cid)

    def test_a_second_attach_adds_no_rows(self, dirs, language_no):
        cache, media = dirs
        lesson = _drill_lesson(cache)
        db = SRSDatabase(":memory:")
        minted = mint_lesson_cards(db, "no", _cards(), drills=DRILLS)
        attach_lesson_card_audio(db, minted, lesson, language_no.tts_voice_map, cache_dir=cache, media_dir=media)
        with db._get_conn() as conn:
            before = conn.execute("SELECT COUNT(*) FROM media").fetchone()[0]
        attach_lesson_card_audio(db, minted, lesson, language_no.tts_voice_map, cache_dir=cache, media_dir=media)
        with db._get_conn() as conn:
            assert conn.execute("SELECT COUNT(*) FROM media").fetchone()[0] == before
        assert before > 0  # control: the first attach added rows

    def test_a_take_the_cache_lacks_is_logged_and_left_out(self, dirs, language_no, caplog):
        cache, media = dirs
        lesson = _drill_lesson(cache)
        db = SRSDatabase(":memory:")
        # drill 4 (question and answer) is not in the rendered lesson, and its step 3 says
        # nothing the rendered drills say: no take for either of its clips
        minted = mint_lesson_cards(db, "no", _cards(), drills=DRILLS)
        attach_lesson_card_audio(db, minted, lesson, language_no.tts_voice_map, cache_dir=cache, media_dir=media)
        qa = [cid for cid, card in minted if card.unit.disambig_key == "sent:test-01:drill-4:3"][0]
        assert db.get_media_filename(qa, "drill_cue_2") is None
        assert db.get_media_filename(qa, "drill_answer_2") is None
        assert "audio" not in _dirty(db, qa)
        assert "DRILL_CARD_NO_TAKE key=sent:test-01:drill-4:3 kind=drill_cue_2" in caplog.text
