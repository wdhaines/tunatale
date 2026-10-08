"""Oracle for scripts/rebuild_lessons_from_story.py.

The script rebuilds a stored lesson from its own Story JSON so that a change to a
language's ``tts_voice_map`` reaches lessons that already exist — a blob pins a
resolved ``voice_id`` per phrase, so ``tunatale-rag.4``'s new ``male-2`` voice
(and ``female-2``'s, weeks earlier) would otherwise never be heard.

The claims under test are the ones that would fail SILENTLY:
  - a dry run plans and mutates NOTHING;
  - audio is rendered BEFORE the blob is persisted, so a dying run retries;
  - only the LATEST lesson per day is touched;
  - a lesson with no stored Story JSON is skipped, never reconstructed;
  - the rebuilt lesson is the one handed to the renderer, and every section it
    has is re-rendered;
  - the renderer is given the language's SSML locale, without which a
    Multilingual voice guesses the language of the line it is handed.

``reassemble_lesson_audio`` is stubbed throughout: it shells out to ffmpeg and
synthesizes audio, and none of the claims above are about either.
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from app.generation.story import build_lesson_from_story
from app.languages import get_language, get_tts_locale
from app.models.curriculum import Curriculum, CurriculumDay
from app.models.language import Language
from app.storage.store import ContentStore

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import rebuild_lessons_from_story as rebuild_mod  # noqa: E402

_LANG = "no"


def _story() -> dict:
    """Two speakers per gender, so a collapsed voice map is visible in the blob."""
    return {
        "title": "Ordering Coffee",
        "key_phrases": [{"phrase": "god dag", "translation": "good day"}],
        "scenes": [
            {
                "label": "At the Café",
                "lines": [
                    {"speaker": "female-1", "text": "God dag!", "translation": "Good day!"},
                    {"speaker": "female-2", "text": "Takk skal du ha.", "translation": "Thank you."},
                    {"speaker": "male-1", "text": "En kaffe, takk.", "translation": "A coffee please."},
                    {"speaker": "male-2", "text": "Hvor mye koster det?", "translation": "How much is it?"},
                ],
            }
        ],
        # Every surface word, so the generator has no reason to warn about an
        # incomplete gloss map and the test output stays clean.
        "dialogue_glosses": [
            {"word": w, "translation": t}
            for w, t in (
                ("god", "good"),
                ("dag", "day"),
                ("takk", "thanks"),
                ("skal", "shall"),
                ("du", "you"),
                ("ha", "have"),
                ("en", "a"),
                ("kaffe", "coffee"),
                ("hvor", "how"),
                ("mye", "much"),
                ("koster", "costs"),
                ("det", "it"),
            )
        ],
        "morphology_focus": [],
    }


def _collapsed_language() -> Language:
    """The language as it was BEFORE the roles were un-collapsed.

    Seeding with this is what makes the test's stored lesson look like the real
    ones on disk: role-2 speaking in role-1's voice, baked into the blob.
    """
    current = get_language(_LANG)
    voices = dict(current.tts_voice_map)
    voices["female-2"] = voices["female-1"]
    voices["male-2"] = voices["male-1"]
    return Language(
        code=current.code,
        name=current.name,
        native_name=current.native_name,
        script=current.script,
        tts_locale=current.tts_locale,
        tts_voice_map=voices,
    )


def _seed(tmp_path: Path, *, story: dict | None = None, days: tuple[int, ...] = (1,)) -> ContentStore:
    """A real file DB, not ``:memory:`` — the script opens its own ContentStore,
    and sqlite ``:memory:`` is one database per connection, so the seed and the
    script would be looking at different worlds."""
    store = ContentStore(str(tmp_path / "content.sqlite"))
    store.save_curriculum(
        "cur-1",
        Curriculum(
            id="cur-1",
            topic="Coffee",
            language_code=_LANG,
            cefr_level="A1",
            days=[CurriculumDay(day=d, title="T", focus="f", collocations=[], learning_objective="o") for d in days],
        ),
    )
    for day in days:
        lesson = build_lesson_from_story(story if story is not None else _story(), language=_collapsed_language())
        store.save_lesson(f"lesson-day-{day}", "cur-1", day, lesson)
    return store


def _stub_renderer(monkeypatch, captured: dict | None = None):
    """Neutralise everything the script builds but this test does not exercise."""
    monkeypatch.setattr(rebuild_mod, "get_tts_service", lambda **kw: object())
    real_builder = rebuild_mod.build_lesson_renderer

    def factory(tts, codes, settings, **kwargs):
        # The REAL builder, so a claim about what the renderer is given is a
        # claim about the renderer this script builds; the script itself gets a
        # methodless stub, so reaching it would raise.
        if captured is not None:
            captured["renderer"] = real_builder(tts, codes, settings, **kwargs)
        return object()

    monkeypatch.setattr(rebuild_mod, "build_lesson_renderer", factory)


def _run(store: ContentStore, monkeypatch, capsys, *args: str, captured: dict | None = None) -> tuple[int, str]:
    _stub_renderer(monkeypatch, captured)
    monkeypatch.setattr(
        sys,
        "argv",
        ["rebuild_lessons_from_story", "--db", str(store._path), "--language", _LANG, *args],
    )
    rc = asyncio.run(rebuild_mod.main())
    return rc, capsys.readouterr().out


def _blobs(store: ContentStore) -> str:
    return json.dumps(
        {lid: store.get_lesson(lid).to_json() for (lid, _c, _d, _l) in store.list_lessons()},
        sort_keys=True,
    )


def _voices(store: ContentStore, lesson_id: str) -> dict[str, str]:
    """role -> voice_id, from the stored blob."""
    lesson = store.get_lesson(lesson_id)
    return {p.role: p.voice_id for s in lesson.sections for p in s.phrases if p.role}


class TestDryRunMutatesNothing:
    def test_it_plans_the_rebuild(self, tmp_path, monkeypatch, capsys):
        store = _seed(tmp_path)

        rc, out = _run(store, monkeypatch, capsys, "--dry-run")

        assert rc == 0
        assert "would rebuild" in out
        assert "would rebuild=1" in out

    def test_it_touches_no_blob(self, tmp_path, monkeypatch, capsys):
        store = _seed(tmp_path)
        before = _blobs(store)

        _run(store, monkeypatch, capsys, "--dry-run")

        assert _blobs(store) == before

    def test_it_never_renders(self, tmp_path, monkeypatch, capsys):
        """The renderer is a stub object with no methods; reaching it would raise."""
        store = _seed(tmp_path)

        def explode(**kw):
            raise AssertionError("a dry run must never render")

        monkeypatch.setattr(rebuild_mod, "reassemble_lesson_audio", explode)

        rc, _out = _run(store, monkeypatch, capsys, "--dry-run")

        assert rc == 0


class TestGo:
    @staticmethod
    def _record(monkeypatch, store: ContentStore, calls: list[dict]):
        async def fake(**kwargs):
            # Snapshot the STORED blob at render time — the ordering claim.
            calls.append({**kwargs, "stored_at_render": _blobs(store)})
            return {}

        monkeypatch.setattr(rebuild_mod, "reassemble_lesson_audio", fake)

    def test_the_blob_gets_the_current_voice_map(self, tmp_path, monkeypatch, capsys):
        store = _seed(tmp_path)
        assert _voices(store, "lesson-day-1")["male-2"] == get_language(_LANG).tts_voice_map["male-1"]
        self._record(monkeypatch, store, [])

        rc, out = _run(store, monkeypatch, capsys, "--go")

        assert rc == 0, out
        current = get_language(_LANG).tts_voice_map
        after = _voices(store, "lesson-day-1")
        assert after["male-2"] == current["male-2"]
        assert after["female-2"] == current["female-2"]

    def test_it_renders_before_it_persists(self, tmp_path, monkeypatch, capsys):
        """THE resumability invariant. If the blob were written first, a render
        that dies (the provider throttles) would leave a lesson claiming a voice
        its audio never speaks, and it would look current to every later run."""
        store = _seed(tmp_path)
        before = _blobs(store)
        calls: list[dict] = []
        self._record(monkeypatch, store, calls)

        _run(store, monkeypatch, capsys, "--go")

        assert calls[0]["stored_at_render"] == before
        assert _blobs(store) != before

    def test_the_renderer_is_handed_the_rebuilt_lesson(self, tmp_path, monkeypatch, capsys):
        """Not the stored one — the render speaks the Phrase objects it is given,
        so handing it the old lesson would produce the old voices while the blob
        claimed the new ones."""
        store = _seed(tmp_path)
        calls: list[dict] = []
        self._record(monkeypatch, store, calls)

        _run(store, monkeypatch, capsys, "--go")

        rendered = {p.role: p.voice_id for s in calls[0]["lesson"].sections for p in s.phrases if p.role}
        assert rendered["male-2"] == get_language(_LANG).tts_voice_map["male-2"]

    def test_every_section_of_the_lesson_is_re_rendered(self, tmp_path, monkeypatch, capsys):
        """A voice change lands in every section its speaker appears in, so a
        subset would leave one lesson half in each voice."""
        store = _seed(tmp_path)
        calls: list[dict] = []
        self._record(monkeypatch, store, calls)

        _run(store, monkeypatch, capsys, "--go")

        lesson = calls[0]["lesson"]
        assert set(calls[0]["section_types"]) == {s.section_type for s in lesson.sections}

    def test_the_renderer_gets_the_languages_ssml_locale(self, tmp_path, monkeypatch, capsys):
        """Without it a Multilingual voice is handed the line with no language
        declared and guesses — measured getting it wrong (tunatale-rag.4). This
        script puts such a voice into the lessons, so it must wire the seam."""
        store = _seed(tmp_path)
        captured: dict = {}
        self._record(monkeypatch, store, [])

        _run(store, monkeypatch, capsys, "--go", captured=captured)

        # English rides along: the lessons' English lines declare en-US too.
        assert captured["renderer"]._tts_locales == {_LANG: get_tts_locale(_LANG), "en": "en-US"}

    def test_a_second_run_reports_already_current(self, tmp_path, monkeypatch, capsys):
        """Idempotence, on the honest signal: the rebuilt blob is byte-identical.
        Usable only because the rebuild is deterministic — verified on the real
        corpus by building all nine lessons twice."""
        store = _seed(tmp_path)
        self._record(monkeypatch, store, [])
        _run(store, monkeypatch, capsys, "--go")

        rc, out = _run(store, monkeypatch, capsys, "--go")

        assert rc == 0
        assert "already current" in out
        assert "rebuilt=0  skipped=1" in out


class TestFailureIsResumable:
    def test_a_failed_render_leaves_the_blob_and_exits_1(self, tmp_path, monkeypatch, capsys):
        store = _seed(tmp_path)
        before = _blobs(store)

        async def boom(**kwargs):
            raise RuntimeError("throttled")

        monkeypatch.setattr(rebuild_mod, "reassemble_lesson_audio", boom)

        rc, out = _run(store, monkeypatch, capsys, "--go")

        assert rc == 1
        assert "FAILED: RuntimeError: throttled" in out
        assert "Re-run to retry" in out
        assert _blobs(store) == before


class TestSelection:
    def test_only_the_latest_lesson_per_day_is_rebuilt(self, tmp_path, monkeypatch, capsys):
        """The real corpus has two lessons on day 8. Walking every row would
        spend renders on a blob the UI has already superseded."""
        store = _seed(tmp_path)
        superseded = _blobs(store)
        newer = build_lesson_from_story(_story(), language=_collapsed_language())
        store.save_lesson("lesson-day-1-v2", "cur-1", 1, newer)
        calls: list[dict] = []
        TestGo._record(monkeypatch, store, calls)

        rc, out = _run(store, monkeypatch, capsys, "--go")

        assert rc == 0, out
        assert [c["lesson_id"] for c in calls] == ["lesson-day-1-v2"]
        assert store.get_lesson("lesson-day-1").to_json() == json.loads(superseded)["lesson-day-1"]

    def test_the_day_filter_restricts_the_run(self, tmp_path, monkeypatch, capsys):
        store = _seed(tmp_path, days=(1, 2))
        calls: list[dict] = []
        TestGo._record(monkeypatch, store, calls)

        rc, out = _run(store, monkeypatch, capsys, "--go", "--day", "2")

        assert rc == 0, out
        assert [c["lesson_id"] for c in calls] == ["lesson-day-2"]

    def test_a_lesson_of_another_language_is_left_alone(self, tmp_path, monkeypatch, capsys):
        store = _seed(tmp_path)
        lesson = store.get_lesson("lesson-day-1")
        lesson.language_code = "sl"
        store.update_lesson_data("lesson-day-1", lesson)
        before = _blobs(store)

        rc, out = _run(store, monkeypatch, capsys, "--go")

        assert rc == 0
        assert "rebuilt=0  skipped=0" in out
        assert _blobs(store) == before


class TestStorylessLessonIsSkipped:
    def test_it_is_skipped_and_never_reconstructed(self, tmp_path, monkeypatch, capsys):
        """lesson_io._reconstruct_story is lossy. Guessing the source of real
        content in order to overwrite that content is not a trade this script
        is allowed to make."""
        store = _seed(tmp_path)
        lesson = store.get_lesson("lesson-day-1")
        lesson.generation_metadata.pop("story")
        store.update_lesson_data("lesson-day-1", lesson)
        before = _blobs(store)

        rc, out = _run(store, monkeypatch, capsys, "--go")

        assert rc == 0
        assert "NO stored Story JSON" in out
        assert _blobs(store) == before


class TestRebuildAndDescribe:
    def test_rebuild_returns_none_without_a_story(self, tmp_path):
        store = _seed(tmp_path)
        lesson = store.get_lesson("lesson-day-1")
        lesson.generation_metadata.pop("story")

        assert rebuild_mod.rebuild(store, lesson, 1, "cur-1", get_language(_LANG)) is None

    def test_rebuild_resolves_todays_voice_map(self, tmp_path):
        store = _seed(tmp_path)
        stored = store.get_lesson("lesson-day-1")

        fresh = rebuild_mod.rebuild(store, stored, 1, "cur-1", get_language(_LANG))

        roles = {p.role: p.voice_id for s in fresh.sections for p in s.phrases if p.role}
        assert roles["male-2"] == get_language(_LANG).tts_voice_map["male-2"]

    def test_rebuild_survives_a_missing_curriculum(self, tmp_path):
        """An orphaned lesson still rebuilds; the review request is simply
        unmeasurable, which is not the same as a failed generation."""
        store = _seed(tmp_path)
        stored = store.get_lesson("lesson-day-1")

        assert rebuild_mod.rebuild(store, stored, 1, "no-such-curriculum", get_language(_LANG)) is not None

    def test_describe_counts_voice_and_text_changes(self, tmp_path):
        store = _seed(tmp_path)
        stored = store.get_lesson("lesson-day-1")
        fresh = rebuild_mod.rebuild(store, stored, 1, "cur-1", get_language(_LANG))

        summary = rebuild_mod.describe(stored, fresh)

        assert summary.endswith("0 text")
        assert summary.split(" voice")[0].isdigit()
        assert int(summary.split(" voice")[0]) > 0

    def test_describe_reports_a_structural_difference_instead_of_counting(self, tmp_path):
        """Zipping phrase lists of different lengths would raise; and a lesson
        whose shape moved is not a voice swap and wants human eyes."""
        store = _seed(tmp_path)
        stored = store.get_lesson("lesson-day-1")
        fresh = rebuild_mod.rebuild(store, stored, 1, "cur-1", get_language(_LANG))
        fresh.sections[0].phrases.pop()

        assert "STRUCTURE DIFFERS" in rebuild_mod.describe(stored, fresh)


class TestArgs:
    @pytest.mark.parametrize("args", [(), ("--dry-run", "--go")], ids=["neither", "both"])
    def test_exactly_one_mode_is_required(self, tmp_path, monkeypatch, capsys, args):
        store = _seed(tmp_path)

        with pytest.raises(SystemExit):
            _run(store, monkeypatch, capsys, *args)


# ── --voices-only (tunatale-ucpg) ────────────────────────────────────────

_GUY = "en-US-GuyNeural"


def _pre_ucpg_language() -> Language:
    """The language as the stored lessons were built: Guy narrating everything."""
    current = get_language(_LANG)
    return Language(
        code=current.code,
        name=current.name,
        native_name=current.native_name,
        script=current.script,
        tts_locale=current.tts_locale,
        tts_voice_map={**current.tts_voice_map, "narrator": _GUY},
    )


def _seed_drifted(tmp_path: Path) -> ContentStore:
    """A Guy-narrated lesson whose KEY_PHRASES text today's rules no longer produce.

    That is the real corpus: on 2026-09-29 the live day-11 rebuild changed 67
    key-phrase texts (170 -> 172 phrases) and none in the other six sections.
    The drift is simulated the way it really arrives: the drill's phrase COUNT
    changes (one stored buildup chunk more than today's builder emits), which
    an L2 respelling alone would not do — voices_only tolerates that one.
    """
    store = ContentStore(str(tmp_path / "content.sqlite"))
    store.save_curriculum(
        "cur-1",
        Curriculum(
            id="cur-1",
            topic="Coffee",
            language_code=_LANG,
            cefr_level="A1",
            days=[CurriculumDay(day=1, title="T", focus="f", collocations=[], learning_objective="o")],
        ),
    )
    lesson = build_lesson_from_story(_story(), language=_pre_ucpg_language())
    kp = next(s for s in lesson.sections if s.section_type.value == "key_phrases")
    l2 = next(p for p in kp.phrases if p.language_code == _LANG)
    kp.phrases.insert(kp.phrases.index(l2), replace(l2, text=l2.text + "?"))
    store.save_lesson("lesson-day-1", "cur-1", 1, lesson)
    return store


def _section(store: ContentStore, value: str):
    return next(s for s in store.get_lesson("lesson-day-1").sections if s.section_type.value == value)


class TestVoicesOnly:
    _record = staticmethod(TestGo._record)

    def test_a_drifted_section_keeps_its_stored_text(self, tmp_path, monkeypatch, capsys):
        """The whole point: a listening test must hear the SAME lesson in new voices."""
        store = _seed_drifted(tmp_path)
        before = [p.text for p in _section(store, "key_phrases").phrases]
        self._record(monkeypatch, store, [])

        rc, out = _run(store, monkeypatch, capsys, "--go", "--voices-only")

        assert rc == 0, out
        assert [p.text for p in _section(store, "key_phrases").phrases] == before

    def test_a_drifted_section_still_moves_its_narrator(self, tmp_path, monkeypatch, capsys):
        store = _seed_drifted(tmp_path)
        self._record(monkeypatch, store, [])

        _run(store, monkeypatch, capsys, "--go", "--voices-only")

        english = {p.voice_id for p in _section(store, "key_phrases").phrases if p.language_code == "en"}
        assert english == {"en-US-DavisMultilingualNeural"}

    def test_an_unchanged_section_takes_the_rebuilt_voices(self, tmp_path, monkeypatch, capsys):
        """Translations move to the speaker's English voice; the title stays the narrator's."""
        store = _seed_drifted(tmp_path)
        self._record(monkeypatch, store, [])

        _run(store, monkeypatch, capsys, "--go", "--voices-only")

        by_text = {p.text: p.voice_id for p in _section(store, "translated").phrases if p.language_code == "en"}
        en_cast = get_language(_LANG).tts_en_voice_map
        assert by_text["Thank you."] == en_cast["female-2"]
        assert by_text["A coffee please."] == en_cast["male-1"]
        assert by_text["At the Café"] == "en-US-DavisMultilingualNeural"

    def test_the_lesson_narrator_moves_so_the_title_does_too(self, tmp_path, monkeypatch, capsys):
        store = _seed_drifted(tmp_path)
        self._record(monkeypatch, store, [])

        _run(store, monkeypatch, capsys, "--go", "--voices-only")

        assert store.get_lesson("lesson-day-1").narrator_voice == "en-US-DavisMultilingualNeural"

    def test_the_renderer_is_handed_the_voices_only_lesson(self, tmp_path, monkeypatch, capsys):
        store = _seed_drifted(tmp_path)
        calls: list[dict] = []
        self._record(monkeypatch, store, calls)

        _run(store, monkeypatch, capsys, "--go", "--voices-only")

        rendered = calls[0]["lesson"]
        assert rendered.to_json() == store.get_lesson("lesson-day-1").to_json()

    def test_the_plan_names_the_kept_section(self, tmp_path, monkeypatch, capsys):
        store = _seed_drifted(tmp_path)

        rc, out = _run(store, monkeypatch, capsys, "--dry-run", "--voices-only")

        assert rc == 0
        assert "kept stored text: key_phrases" in out

    def test_a_second_run_is_already_current(self, tmp_path, monkeypatch, capsys):
        store = _seed_drifted(tmp_path)
        self._record(monkeypatch, store, [])
        _run(store, monkeypatch, capsys, "--go", "--voices-only")

        _rc, out = _run(store, monkeypatch, capsys, "--go", "--voices-only")

        assert "already current" in out

    def test_without_the_flag_the_drifted_text_is_rebuilt(self, tmp_path, monkeypatch, capsys):
        """Control: the default mode is still a full rebuild, drift and all."""
        store = _seed_drifted(tmp_path)
        before = [p.text for p in _section(store, "key_phrases").phrases]
        self._record(monkeypatch, store, [])

        _run(store, monkeypatch, capsys, "--go")

        assert [p.text for p in _section(store, "key_phrases").phrases] != before


def _seed_l2_drift(tmp_path: Path) -> ContentStore:
    """A dialogue section whose NORWEGIAN text today's builders would respell.

    The live laptop venv does exactly this: its enunciated pass re-splits
    'forsvare' as 'for, svare' (2026-09-29), in all three slow sections, while
    every role and every English line is unchanged. Simulated on one slow L2 line.
    """
    store = _seed_drifted(tmp_path)
    lesson = store.get_lesson("lesson-day-1")
    slow = next(s for s in lesson.sections if s.section_type.value == "slow_translated")
    l2 = next(p for p in slow.phrases if p.language_code == _LANG)
    l2.text = "for, " + l2.text
    store.update_lesson_data("lesson-day-1", lesson)
    return store


class TestVoicesOnlyL2Drift:
    _record = staticmethod(TestGo._record)

    def test_a_section_with_drifted_l2_text_still_gets_the_english_cast(self, tmp_path, monkeypatch, capsys):
        """A voice is a function of ROLE, never of the L2 wording, so L2 drift must not block it."""
        store = _seed_l2_drift(tmp_path)
        self._record(monkeypatch, store, [])

        _run(store, monkeypatch, capsys, "--go", "--voices-only")

        by_text = {p.text: p.voice_id for p in _section(store, "slow_translated").phrases if p.language_code == "en"}
        assert by_text["Thank you."] == get_language(_LANG).tts_en_voice_map["female-2"]

    def test_the_drifted_l2_text_is_kept(self, tmp_path, monkeypatch, capsys):
        store = _seed_l2_drift(tmp_path)
        before = [p.text for p in _section(store, "slow_translated").phrases]
        self._record(monkeypatch, store, [])

        _run(store, monkeypatch, capsys, "--go", "--voices-only")

        assert [p.text for p in _section(store, "slow_translated").phrases] == before

    def test_the_plan_does_not_call_it_kept(self, tmp_path, monkeypatch, capsys):
        store = _seed_l2_drift(tmp_path)

        _rc, out = _run(store, monkeypatch, capsys, "--dry-run", "--voices-only")

        assert "kept stored text: key_phrases)" in out


class TestTheClosingDrill:
    """A rebuilt lesson ends on the affix drill a published one ends on (tunatale-ve4p.7).

    The drill is not in the Story JSON; it is added when a lesson is published
    into its day. A rebuild that forgot it would silently drop the drill, and
    the contrast cards the listen reads off it, from a lesson that had both.
    """

    @staticmethod
    def _store_with(story: dict) -> tuple[ContentStore, object]:
        language = get_language("ceb")
        store = ContentStore(":memory:")
        day = CurriculumDay(day=1, title="A wake", focus="f", collocations=["haya"], learning_objective="o")
        store.save_curriculum(
            "funeral", Curriculum(id="funeral", topic="A funeral", language_code="ceb", cefr_level="A1", days=[day])
        )
        lesson = build_lesson_from_story(story, language=language)
        store.save_lesson("wake-1", "funeral", 1, lesson)
        return store, language

    @staticmethod
    def _wake(lines: list[tuple[str, str]]) -> dict:
        return {
            "title": "An Evening Wake",
            "key_phrases": [{"phrase": "maayong gabii", "translation": "good evening"}],
            "scenes": [
                {
                    "label": "At the wake",
                    "lines": [{"speaker": "male-1", "text": text, "translation": english} for text, english in lines],
                }
            ],
            "dialogue_glosses": [{"word": "kape", "translation": "coffee"}],
        }

    def test_a_story_that_carries_its_days_pattern_is_rebuilt_with_the_drill(self):
        store, language = self._store_with(
            self._wake([("Moadto ko sa haya.", "I am going to the wake."), ("Mouban ko ugma.", "I will come along.")])
        )
        rebuilt = rebuild_mod.rebuild(store, store.get_lesson("wake-1"), 1, "funeral", language)
        assert rebuilt.generation_metadata["affix_drill"] == {
            "pattern": "mo-mi",
            "roots": ["adto", "uban"],
            "new_roots": [],
        }
        assert rebuilt.sections[-1].section_type.value == "affix_drill"

    def test_a_story_that_does_not_is_rebuilt_without_one(self):
        store, language = self._store_with(self._wake([("Maayong gabii.", "Good evening.")]))
        rebuilt = rebuild_mod.rebuild(store, store.get_lesson("wake-1"), 1, "funeral", language)
        assert "affix_drill" not in rebuilt.generation_metadata
