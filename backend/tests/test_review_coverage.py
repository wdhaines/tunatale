"""The review-coverage meter (bd tunatale-fgeq.4).

"We asked the model" is not "it happened". The prompt now carries the learner's
decaying vocabulary, and omission is an ALREADY-OBSERVED failure mode on this
exact call — a `./test.sh` run on 2026-08-29 logged
`LLM omitted 3 word(s) from dialogue_glosses (sl)`. Without a meter the feature
cannot be shown to work at all.

⚠️ WHAT CI CAN AND CANNOT PROVE. These tests prove the METER is correct. They
cannot prove the MODEL complied — a cassette replays a fixed recorded response,
so any "the story used the review words" assertion at this tier is measuring the
fixture. Reading the meter takes a live generation, and that number belongs in a
commit message, not in a test.

⚠️ THE MATCH IS TOKEN- AND LEMMA-BASED, NOT SUBSTRING. Slovene and Norwegian
inflect, so a bare substring check would both miss real uses (the requested lemma
appearing as an inflected surface) and manufacture false ones (a short word
sitting inside a longer, unrelated one). `test_a_word_inside_a_longer_word_does_not_count`
is the discriminator between this implementation and the lazy one.
"""

from app.generation.review_coverage import review_word_usage

# Shaped exactly like the map `build_lesson_from_story` already builds from the
# generated dialogue: surface (lowercased) -> in-context lemma.
SURFACE_LEMMA = {
    "danes": "danes",
    "kavo": "kava",
    "prosim": "prositi",
    "hvala": "hvala",
    "lepa": "lep",
}
DIALOGUE = "Danes prosim kavo. Hvala lepa, dober dan."


def _used(*words):
    return review_word_usage(list(words), SURFACE_LEMMA, DIALOGUE)[0]


def _unused(*words):
    return review_word_usage(list(words), SURFACE_LEMMA, DIALOGUE)[1]


class TestSingleWords:
    def test_an_exact_surface_counts(self):
        assert _used("prosim") == ["prosim"]

    def test_an_inflected_surface_counts_via_its_lemma(self):
        """`kava` was requested; the story wrote `kavo`. That IS a use — and it
        is the common case in an inflecting language, not an edge case."""
        assert _used("kava") == ["kava"]

    def test_an_absent_word_does_not_count(self):
        assert _unused("nasvidenje") == ["nasvidenje"]

    def test_a_word_inside_a_longer_word_does_not_count(self):
        """THE DISCRIMINATOR. `dan` sits inside `danes`, and a substring check
        would report it as used. It is a different word and the learner did not
        hear it."""
        assert _unused("dan") == ["dan"]

    def test_matching_ignores_case(self):
        """`Danes` opens the dialogue capitalised; the request is lowercase."""
        assert _used("danes") == ["danes"]


class TestMultiWordCollocations:
    """A collocation cannot be looked up in a token map, so these fall back to a
    phrase search over the dialogue. That under-counts an inflected collocation
    and the fallback is documented as such — under-counting is the SAFE
    direction for a meter whose output is an advisory number."""

    def test_a_present_collocation_counts(self):
        assert _used("dober dan") == ["dober dan"]

    def test_an_absent_collocation_does_not_count(self):
        assert _unused("dober vecer") == ["dober vecer"]


class TestTheSplit:
    def test_both_lists_come_back_and_order_is_preserved(self):
        used, unused = review_word_usage(["nasvidenje", "kava", "zbogom", "prosim"], SURFACE_LEMMA, DIALOGUE)
        assert used == ["kava", "prosim"]
        assert unused == ["nasvidenje", "zbogom"]

    def test_nothing_requested_is_not_a_miss(self):
        """An empty request must read as 0/0, never as a total failure — most
        lessons early in a curriculum have no due vocabulary at all."""
        assert review_word_usage([], SURFACE_LEMMA, DIALOGUE) == ([], [])

    def test_an_empty_lesson_misses_everything(self):
        assert review_word_usage(["kava"], {}, "") == ([], ["kava"])


# ── The meter, wired into the real generate path ───────────────────────────

import json  # noqa: E402
from datetime import UTC, datetime, timedelta  # noqa: E402
from unittest.mock import AsyncMock, MagicMock  # noqa: E402

import pytest  # noqa: E402

from app.common.guid import compute_guid  # noqa: E402
from app.generation.story import StoryGenerator  # noqa: E402
from app.models.curriculum import CurriculumDay  # noqa: E402
from app.models.srs_item import Direction, DirectionState, SRSState  # noqa: E402
from app.models.strategy import ContentStrategy  # noqa: E402
from app.models.syntactic_unit import SyntacticUnit  # noqa: E402
from app.srs.database import SRSDatabase  # noqa: E402

# `prosim` appears VERBATIM in the mock story below, `nasvidenje` does not.
# Both are exact surfaces on purpose: the lemma path is pinned by the pure tests
# above with a hand-built map, so this test does not also depend on how well the
# real lemmatiser handles a given word. One test, one claim.
PRESENT_WORD = "prosim"
ABSENT_WORD = "nasvidenje"

_STORY = json.dumps(
    {
        "title": "Kava",
        "key_phrases": [{"phrase": "dober dan", "translation": "good day"}],
        "scenes": [
            {
                "label": "Kavarna",
                "lines": [
                    {
                        "speaker": "female-1",
                        "text": "Dober dan, prosim kavo.",
                        "translation": "Good day, a coffee please.",
                    },
                    {"speaker": "male-1", "text": "Takoj, hvala.", "translation": "Right away, thanks."},
                ],
            }
        ],
    }
)


def _seed_overdue(db: SRSDatabase, text: str) -> None:
    unit = SyntacticUnit(text=text, translation=f"gloss of {text}", word_count=1, difficulty=1, source="test")
    db.add_collocation(unit, language_code="sl")
    db.update_direction(
        compute_guid(text, "sl", ""),
        Direction.RECOGNITION,
        DirectionState(
            direction=Direction.RECOGNITION,
            state=SRSState.REVIEW,
            due_at=datetime.now(UTC) - timedelta(days=20),
            stability=2.0,
            last_review=datetime.now(UTC) - timedelta(days=20, hours=3),
            reps=4,
        ),
    )


async def _generate(language, db):
    client = MagicMock()
    client.complete = AsyncMock(return_value=_STORY)
    return await StoryGenerator(llm_client=client).generate(
        curriculum_day=CurriculumDay(
            day=1,
            title="Kava",
            focus="cafe",
            collocations=["dober dan"],
            learning_objective="order",
            story_guidance="cafe scene",
        ),
        language=language,
        strategy=ContentStrategy.WIDER,
        srs_db=db,
    )


class TestTheMeterIsWiredIn:
    @pytest.fixture
    def db(self):
        with SRSDatabase(":memory:") as database:
            _seed_overdue(database, PRESENT_WORD)
            _seed_overdue(database, ABSENT_WORD)
            yield database

    async def test_the_lesson_records_what_was_asked_and_what_landed(self, language, db):
        lesson = await _generate(language, db)
        meta = lesson.generation_metadata

        assert set(meta["review_requested"]) == {PRESENT_WORD, ABSENT_WORD}
        assert meta["review_used"] == [PRESENT_WORD], (
            "the meter must read the GENERATED dialogue, not echo the request back"
        )

    async def test_the_ratio_is_logged(self, language, db, caplog):
        """The number has to reach a human. A meter nobody can read is the same
        as no meter — and the log is the only channel the auto path has."""
        with caplog.at_level("INFO", logger="app.generation.story"):
            await _generate(language, db)
        assert "Review words used 1/2" in caplog.text
        assert ABSENT_WORD in caplog.text

    async def test_nothing_requested_logs_nothing_and_records_empty(self, language, caplog):
        """Most lessons early in a curriculum have no due vocabulary. That must
        read as 'not applicable', not as a 0/0 failure line on every generation."""
        with SRSDatabase(":memory:") as empty, caplog.at_level("INFO", logger="app.generation.story"):
            lesson = await _generate(language, empty)
        assert lesson.generation_metadata["review_requested"] == []
        assert lesson.generation_metadata["review_used"] == []
        assert "Review words used" not in caplog.text


# ── The meter reaches the lesson response (bd tunatale-37xv) ───────────────


class TestTheLessonResponseCarriesTheMeter:
    """`GET /api/story/{id}` returned no generation metadata at all, so the only
    way to read the meter was the server log or a db poke — which makes the
    feature's actual outcome invisible to the person it is for.

    ⚠️ TWO COUNTS AND TWO LISTS, NOT THE WHOLE BLOB. `generation_metadata` also
    carries token_glosses, verb_base_glosses, sentence_translations and the full
    Story-JSON source; dumping it would bloat every lesson fetch on the reading
    path to avoid one decision.
    """

    @pytest.fixture
    def db(self):
        with SRSDatabase(":memory:") as database:
            _seed_overdue(database, PRESENT_WORD)
            _seed_overdue(database, ABSENT_WORD)
            yield database

    async def test_the_response_reports_what_was_asked_and_what_landed(self, language, db):
        from app.api._serializers import serialize_lesson

        lesson = await _generate(language, db)
        payload = serialize_lesson("l1", lesson)

        assert set(payload["review_requested"]) == {PRESENT_WORD, ABSENT_WORD}
        assert payload["review_used"] == [PRESENT_WORD]

    def test_a_lesson_with_no_review_data_reports_empty_lists(self, language):
        """A hand-authored import, or any lesson generated before this existed.
        Empty means UNMEASURABLE and must not read as a failed generation — so it
        is still present and still a list, never null and never absent."""
        from app.api._serializers import serialize_lesson
        from app.models.lesson import Lesson

        payload = serialize_lesson("l1", Lesson(title="T", language_code="sl", sections=[]))
        assert payload["review_requested"] == []
        assert payload["review_used"] == []

    async def test_the_heavy_metadata_stays_out_of_the_response(self, language, db):
        from app.api._serializers import serialize_lesson

        lesson = await _generate(language, db)
        payload = serialize_lesson("l1", lesson)
        for heavy in ("token_glosses", "sentence_translations", "story", "generation_metadata"):
            assert heavy not in payload, f"{heavy} would bloat every lesson fetch on the reading path"
