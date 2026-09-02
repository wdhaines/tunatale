"""Oracle for scripts/rename_section_titles.py — the tunatale-v3ri backfill.

O9: dry-run must be a no-op. The failure this exists to catch is a dry-run that
silently mutates: it MUST NOT touch a single file byte or DB row, while still
printing the plan of lessons it WOULD touch.

The script's own re-render machinery (renderer, TTS, slicers) is stubbed — a
dry-run never reaches it, and keeping it real would tie the test to network or
lazy model loading that has nothing to do with O9's claim.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from app.models.lesson import Lesson, Phrase, Section, SectionType
from app.storage.store import ContentStore

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import pytest
import rename_section_titles as rename_mod  # noqa: E402

from tests.test_reassemble_lesson import _populate_store  # noqa: E402

# Shells out to a real ffmpeg binary. CI's two hostile-timezone jobs deselect
# these with -m "not ffmpeg" so they need no ffmpeg install; see
# pyproject.toml [tool.pytest.ini_options] markers.
pytestmark = pytest.mark.ffmpeg

# ── helpers ──────────────────────────────────────────────────────────────────


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _lesson_with_renamed_sections(stale_titles: bool) -> Lesson:
    """A lesson whose renamed sections carry either the OLD (pre-rename) or NEW
    title phrase text, mirrored into the stored section cues by
    _populate_store."""
    translated_title = "Translated" if stale_titles else "English After"
    slow_title = "Slow Speed" if stale_titles else "Enunciated"
    return Lesson(
        title="Inside the Cabin",
        language_code="sl",
        sections=[
            Section(
                section_type=SectionType.TRANSLATED,
                phrases=[
                    Phrase(text=translated_title, voice_id="n", language_code="en", role="narrator"),
                    Phrase(text="Dober dan", voice_id="v", language_code="sl", role="f1"),
                    Phrase(text="Good day", voice_id="n", language_code="en", role="narrator"),
                ],
            ),
            Section(
                section_type=SectionType.SLOW_SPEED,
                phrases=[
                    Phrase(text=slow_title, voice_id="n", language_code="en", role="narrator"),
                    Phrase(text="Dober dan", voice_id="v", language_code="sl", role="f1"),
                ],
            ),
        ],
        key_phrases=[],
    )


def _seed_store(tmp_path: Path, stale_titles: bool) -> tuple[ContentStore, Path]:
    # A real file DB, NOT ":memory:": the script opens its OWN ContentStore, and
    # sqlite ":memory:" is one database per connection — the seed and the script
    # would look at different worlds.
    store = ContentStore(str(tmp_path / "content.sqlite"))
    lesson = _lesson_with_renamed_sections(stale_titles)
    store.save_lesson(lesson.title, "cur-1", 1, lesson)
    audio_dir = tmp_path / "audio"
    _populate_store(store, lesson, audio_dir, [2.0, 5.0])
    return store, audio_dir


class _StubRendererFactory:
    def __init__(self, fail_message: str) -> None:
        self.fail_message = fail_message

    def __call__(self, **kwargs):
        class _StubRenderer:
            pause_calculator = object()

            def __init__(self, owner) -> None:
                self._owner = owner

            async def render(self, *a, **kw):
                raise AssertionError(self._owner.fail_message)

            async def render_section(self, *a, **kw):
                raise AssertionError(self._owner.fail_message)

        return _StubRenderer(self)


def _run_dry_run(store: ContentStore, audio_dir: Path, monkeypatch, capsys) -> str:
    import asyncio

    monkeypatch.setattr(rename_mod, "get_tts_service", lambda **kw: object())
    monkeypatch.setattr(
        rename_mod,
        "LessonRenderer",
        _StubRendererFactory("dry-run must never reach the renderer"),
    )
    monkeypatch.setattr(rename_mod, "build_slicers", lambda codes, tts, settings: [])
    monkeypatch.setattr(rename_mod, "get_preprocessor", lambda code: object())
    monkeypatch.setattr(rename_mod, "get_phoneme_planner", lambda code: None)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "rename_section_titles",
            "--dry-run",
            "--db",
            str(store._path),
            "--audio-dir",
            str(audio_dir),
            "--language",
            "sl",
        ],
    )
    rc = asyncio.run(rename_mod.main())
    return f"rc={rc}\n{capsys.readouterr().out}"


def _snapshot(store: ContentStore, audio_dir: Path) -> tuple[list[dict], dict[str, str], str]:
    lesson_ids = [lesson_id for (lesson_id, _c, _d, _l) in store.list_lessons()]
    rows = [r for lesson_id in lesson_ids for r in store.list_audio_files_for_lesson(lesson_id)]
    rows.sort(key=lambda r: r["id"])
    files = {str(p): _sha256(p) for p in audio_dir.iterdir() if p.is_file()}
    blobs = json.dumps(
        {lesson_id: store.get_lesson(lesson_id).to_json() for lesson_id in lesson_ids},
        sort_keys=True,
    )
    return rows, files, blobs


class TestRenameDryRunMutatesNothing:
    def test_o9_dry_run_stale_lesson_plans_but_touches_nothing(self, tmp_path: Path, monkeypatch, capsys) -> None:
        store, audio_dir = _seed_store(tmp_path, stale_titles=True)
        before_rows, before_files, before_blobs = _snapshot(store, audio_dir)

        report = _run_dry_run(store, audio_dir, monkeypatch, capsys)
        assert "Inside the Cabin" in report, report
        assert "would convert=1" in report, report
        assert "re-render 2 renamed section(s)" in report, report

        after_rows, after_files, after_blobs = _snapshot(store, audio_dir)
        assert before_rows == after_rows, "dry-run must not change a single DB row"
        assert before_files == after_files, (
            "dry-run must not change a single file byte — sha256 of every seeded file identical"
        )
        assert before_blobs == after_blobs, "dry-run must not change lesson blobs"

    def test_o9_dry_run_skips_already_current_lessons(self, tmp_path: Path, monkeypatch, capsys) -> None:
        store, audio_dir = _seed_store(tmp_path, stale_titles=False)
        before_rows, before_files, before_blobs = _snapshot(store, audio_dir)

        report = _run_dry_run(store, audio_dir, monkeypatch, capsys)
        assert "already current; skipped" in report, report
        assert "would convert=0" in report, report

        after_rows, after_files, after_blobs = _snapshot(store, audio_dir)
        assert before_rows == after_rows
        assert before_files == after_files
        assert before_blobs == after_blobs


def _run_go(store: ContentStore, audio_dir: Path, monkeypatch, capsys) -> str:
    """Drive the script's --go path with a renderer fake that writes real audio.

    Unlike _run_dry_run this must REACH the renderer, so the stub that raises is
    replaced by test_reassemble_lesson's fake — the same one that would blow up
    if the script ever called renderer.render() instead of render_section().
    """
    import asyncio

    from tests.test_reassemble_lesson import _CountingTTS, _make_fake_renderer

    fake = _make_fake_renderer()
    monkeypatch.setattr(rename_mod, "get_tts_service", lambda **kw: _CountingTTS())
    monkeypatch.setattr(rename_mod, "LessonRenderer", lambda **kw: fake)
    monkeypatch.setattr(rename_mod, "build_slicers", lambda codes, tts, settings: [])
    monkeypatch.setattr(rename_mod, "get_preprocessor", lambda code: object())
    monkeypatch.setattr(rename_mod, "get_phoneme_planner", lambda code: None)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "rename_section_titles",
            "--go",
            "--db",
            str(store._path),
            "--audio-dir",
            str(audio_dir),
            "--language",
            "sl",
        ],
    )
    rc = asyncio.run(rename_mod.main())
    return f"rc={rc}\n{capsys.readouterr().out}"


class TestRenameGoActuallyRetitles:
    """⚠️ THE OUTCOME ORACLE, added after the first real run did nothing.

    On 2026-08-28 the backfill reported `converted=9 failed=0`, re-rendered and
    replaced 45 files, and changed NOTHING: the title is a Phrase INSIDE the
    stored lesson JSON, and SECTION_TITLES is consumed at generation time, so
    re-rendering faithfully reproduced the OLD title. The dry-run afterwards
    still reported all 9 stale.

    The O9 tests could not catch it because the "already current" fixture seeded
    the NEW title into the lesson phrase AND the mirrored cue together, so the
    missing retitle step was invisible. These assert the END STATE instead.
    """

    def test_go_rewrites_the_stored_lesson_title_phrase(self, tmp_path: Path, monkeypatch, capsys) -> None:
        store, audio_dir = _seed_store(tmp_path, stale_titles=True)
        report = _run_go(store, audio_dir, monkeypatch, capsys)
        assert "converted=1" in report, report

        lesson = store.get_lesson("Inside the Cabin")
        titles = {sec.section_type: sec.phrases[0].text for sec in lesson.sections}
        assert titles[SectionType.TRANSLATED] == "English After", titles
        assert titles[SectionType.SLOW_SPEED] == "Enunciated", titles

    def test_go_rewrites_the_stored_title_cue_text(self, tmp_path: Path, monkeypatch, capsys) -> None:
        store, audio_dir = _seed_store(tmp_path, stale_titles=True)
        _run_go(store, audio_dir, monkeypatch, capsys)

        seen = {}
        for row in store.list_audio_files_for_lesson("Inside the Cabin"):
            if row["section_index"] is None or not row["cues_json"]:
                continue
            title_cue = next((c for c in json.loads(row["cues_json"]) if c["phrase_index"] == 0), None)
            seen[row["section_type"]] = title_cue and title_cue.get("text")
        assert seen == {"translated": "English After", "slow_speed": "Enunciated"}, seen

    def test_go_then_dry_run_reports_already_current(self, tmp_path: Path, monkeypatch, capsys) -> None:
        """The resume signal must CLOSE. A --go that leaves the next --dry-run
        still reporting the lesson stale is the exact signature of the 2026-08-28
        no-op run."""
        store, audio_dir = _seed_store(tmp_path, stale_titles=True)
        _run_go(store, audio_dir, monkeypatch, capsys)

        report = _run_dry_run(store, audio_dir, monkeypatch, capsys)
        assert "already current; skipped" in report, report
        assert "would convert=0" in report, report
