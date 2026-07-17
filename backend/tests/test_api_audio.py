"""Tests for audio + phrase + cloze TTS endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.curriculum import Curriculum
from app.models.lesson import Lesson, Phrase, Section, SectionType
from app.models.srs_item import Direction, SRSState
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401


def _make_mock_lesson_with_sections() -> Lesson:
    return Lesson(
        title="Day 1: Ordering Coffee",
        language_code="sl",
        sections=[
            Section(
                section_type=SectionType.KEY_PHRASES,
                phrases=[Phrase(text="dober dan", voice_id="sl-SI-PetraNeural", language_code="sl")],
            ),
            Section(
                section_type=SectionType.NATURAL_SPEED,
                phrases=[Phrase(text="hvala", voice_id="sl-SI-PetraNeural", language_code="sl")],
            ),
        ],
    )


def _fake_render(lesson, full_path, section_paths=None):
    """Fake renderer.render: writes minimal audio bytes and returns mock cues."""
    full_path.write_bytes(b"audio")
    if section_paths:
        for sp in section_paths:
            sp.write_bytes(b"section audio")
    from app.audio.cues import Cue

    cues = [
        Cue(
            index=0,
            start_ms=0,
            end_ms=1000,
            section_index=None,
            section_type=None,
            phrase_index=0,
            role="narrator",
            language_code="en",
            text=lesson.title,
            ref={"kind": "narration"},
        )
    ]
    idx = 1
    for si, section in enumerate(lesson.sections):
        for pi, phrase in enumerate(section.phrases):
            cues.append(
                Cue(
                    index=idx,
                    start_ms=idx * 1000,
                    end_ms=(idx + 1) * 1000,
                    section_index=si,
                    section_type=section.section_type.value,
                    phrase_index=pi,
                    role=phrase.role,
                    language_code=phrase.language_code,
                    text=phrase.text,
                    ref={"kind": "line", "target_index": 0},
                )
            )
            idx += 1
    return cues


class TestAudioEndpoints:
    """Tests for audio render and retrieval endpoints."""

    async def test_render_audio_returns_404_for_missing_lesson(self):
        from app.storage.store import ContentStore

        app.state.content_store = ContentStore(":memory:")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/audio/render", json={"lesson_id": "nonexistent"})
        assert response.status_code == 404

    async def test_audio_render_returns_202(self, tmp_path):
        from app.storage.store import ContentStore

        mock_renderer = AsyncMock()
        mock_renderer.render = AsyncMock(side_effect=_fake_render)

        mock_lesson = _make_mock_lesson_with_sections()
        store = ContentStore(":memory:")
        lesson_id = "test-lesson-id"
        store.save_lesson(lesson_id, "some-curriculum-id", 1, mock_lesson)

        app.state.renderer = mock_renderer
        app.state.audio_dir = tmp_path
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/audio/render", json={"lesson_id": lesson_id})

        assert response.status_code == 202
        data = response.json()
        assert "audio_id" in data
        assert store.get_audio_file_row(data["audio_id"]) is not None

    async def test_render_returns_sections_in_response(self, tmp_path):
        """POST /api/audio/render response includes a sections array."""
        from app.storage.store import ContentStore

        mock_renderer = AsyncMock()
        mock_renderer.render = AsyncMock(side_effect=_fake_render)

        mock_lesson = _make_mock_lesson_with_sections()
        store = ContentStore(":memory:")
        lesson_id = "lesson-sections-test"
        store.save_lesson(lesson_id, "some-curriculum-id", 1, mock_lesson)

        app.state.renderer = mock_renderer
        app.state.audio_dir = tmp_path
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/audio/render", json={"lesson_id": lesson_id})

        assert response.status_code == 202
        data = response.json()
        assert "sections" in data
        assert len(data["sections"]) == len(mock_lesson.sections)

        sec = data["sections"][0]
        assert "audio_id" in sec
        assert sec["section_index"] == 0
        assert sec["section_type"] == "key_phrases"
        assert sec["title"] == "Key Phrases"

    async def test_render_replaces_existing_rows(self, tmp_path):
        """Re-rendering a lesson replaces stale rows so count is exactly len(sections)+1."""
        from app.storage.store import ContentStore

        mock_renderer = AsyncMock()
        mock_renderer.render = AsyncMock(side_effect=_fake_render)

        mock_lesson = _make_mock_lesson_with_sections()
        store = ContentStore(":memory:")
        lesson_id = "lesson-replace-test"
        store.save_lesson(lesson_id, "some-curriculum-id", 1, mock_lesson)

        app.state.renderer = mock_renderer
        app.state.audio_dir = tmp_path
        app.state.content_store = store

        expected_count = len(mock_lesson.sections) + 1  # sections + full row

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # First render
            resp1 = await client.post("/api/audio/render", json={"lesson_id": lesson_id})
            assert resp1.status_code == 202
            after_first = store.list_audio_files_for_lesson(lesson_id)
            assert len(after_first) == expected_count, (
                f"Expected {expected_count} rows after first render, got {len(after_first)}"
            )

            # Second render — should replace, not append
            resp2 = await client.post("/api/audio/render", json={"lesson_id": lesson_id})
            assert resp2.status_code == 202
            after_second = store.list_audio_files_for_lesson(lesson_id)
            assert len(after_second) == expected_count, (
                f"Expected {expected_count} rows after re-render, got {len(after_second)}"
            )

            # Audio IDs should be different (new cohort)
            assert resp1.json()["audio_id"] != resp2.json()["audio_id"]

    async def test_failed_rerender_preserves_existing_rows(self, tmp_path):
        """A render that raises must not leave the lesson without audio rows.

        Guards backlog 14: the old code deleted rows *before* rendering, so a
        render failure 404'd the lesson even though the old files were still on
        disk. Now rows are deleted only after a successful render.
        """
        from app.storage.store import ContentStore

        mock_renderer = AsyncMock()
        mock_renderer.render = AsyncMock(side_effect=_fake_render)

        mock_lesson = _make_mock_lesson_with_sections()
        store = ContentStore(":memory:")
        lesson_id = "lesson-failed-rerender"
        store.save_lesson(lesson_id, "some-curriculum-id", 1, mock_lesson)

        app.state.renderer = mock_renderer
        app.state.audio_dir = tmp_path
        app.state.content_store = store

        expected_count = len(mock_lesson.sections) + 1

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp1 = await client.post("/api/audio/render", json={"lesson_id": lesson_id})
            assert resp1.status_code == 202
            assert len(store.list_audio_files_for_lesson(lesson_id)) == expected_count

            # Second render fails mid-flight.
            mock_renderer.render = AsyncMock(side_effect=RuntimeError("edge-tts blew up"))
            with pytest.raises(RuntimeError):
                await client.post("/api/audio/render", json={"lesson_id": lesson_id})

            # The old rows must survive — the lesson still has its audio.
            after_fail = store.list_audio_files_for_lesson(lesson_id)
            assert len(after_fail) == expected_count, "failed render wiped the existing audio rows"

    async def test_successful_rerender_unlinks_old_files(self, tmp_path):
        """A successful re-render removes the previous cohort's files from disk.

        Guards backlog 14 part 2: every render mints new UUID paths, so without
        an explicit unlink the old files leaked forever.
        """
        from pathlib import Path

        from app.storage.store import ContentStore

        mock_renderer = AsyncMock()
        mock_renderer.render = AsyncMock(side_effect=_fake_render)

        mock_lesson = _make_mock_lesson_with_sections()
        store = ContentStore(":memory:")
        lesson_id = "lesson-unlink-old"
        store.save_lesson(lesson_id, "some-curriculum-id", 1, mock_lesson)

        app.state.renderer = mock_renderer
        app.state.audio_dir = tmp_path
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/audio/render", json={"lesson_id": lesson_id})
            old_paths = [r["file_path"] for r in store.list_audio_files_for_lesson(lesson_id)]
            assert old_paths and all(Path(p).exists() for p in old_paths)

            await client.post("/api/audio/render", json={"lesson_id": lesson_id})
            new_paths = [r["file_path"] for r in store.list_audio_files_for_lesson(lesson_id)]

            assert all(not Path(p).exists() for p in old_paths), "old cohort files were not unlinked"
            assert all(Path(p).exists() for p in new_paths), "new cohort files should be on disk"
            assert set(old_paths).isdisjoint(new_paths)

    async def test_render_returns_cues_in_post_response(self, tmp_path):
        """POST /api/audio/render includes cues in the response body."""
        from app.storage.store import ContentStore

        mock_renderer = AsyncMock()
        mock_renderer.render = AsyncMock(side_effect=_fake_render)

        mock_lesson = _make_mock_lesson_with_sections()
        store = ContentStore(":memory:")
        lesson_id = "lesson-cues-post"
        store.save_lesson(lesson_id, "some-curriculum-id", 1, mock_lesson)

        app.state.renderer = mock_renderer
        app.state.audio_dir = tmp_path
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/audio/render", json={"lesson_id": lesson_id})

        assert response.status_code == 202
        data = response.json()
        assert "cues" in data
        assert len(data["cues"]) > 0
        first = data["cues"][0]
        assert "start_ms" in first
        assert "end_ms" in first
        assert "index" in first
        assert "text" in first

    async def test_render_persists_cues_in_store(self, tmp_path):
        """After render, cues are persisted on the full-lesson audio row."""
        from app.storage.store import ContentStore

        mock_renderer = AsyncMock()
        mock_renderer.render = AsyncMock(side_effect=_fake_render)

        mock_lesson = _make_mock_lesson_with_sections()
        store = ContentStore(":memory:")
        lesson_id = "lesson-cues-persist"
        store.save_lesson(lesson_id, "some-curriculum-id", 1, mock_lesson)

        app.state.renderer = mock_renderer
        app.state.audio_dir = tmp_path
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/audio/render", json={"lesson_id": lesson_id})

        assert response.status_code == 202
        data = response.json()
        full_row = store.get_audio_file_row(data["audio_id"])
        assert full_row is not None
        assert full_row["cues_json"] is not None

    async def test_get_lesson_audio_endpoint(self, tmp_path):
        """GET /api/audio/lesson/{lesson_id} returns existing audio files list."""
        from app.storage.store import ContentStore

        mock_renderer = AsyncMock()
        mock_renderer.render = AsyncMock(side_effect=_fake_render)

        mock_lesson = _make_mock_lesson_with_sections()
        store = ContentStore(":memory:")
        lesson_id = "lesson-lookup-test"
        store.save_lesson(lesson_id, "some-curriculum-id", 1, mock_lesson)

        app.state.renderer = mock_renderer
        app.state.audio_dir = tmp_path
        app.state.content_store = store

        # First render
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/audio/render", json={"lesson_id": lesson_id})
            response = await client.get(f"/api/audio/lesson/{lesson_id}")

        assert response.status_code == 200
        data = response.json()
        assert "audio_id" in data
        assert "sections" in data
        assert len(data["sections"]) == len(mock_lesson.sections)

    async def test_get_lesson_audio_includes_cues(self, tmp_path):
        """GET /api/audio/lesson/{id} includes cues in the response."""
        from app.storage.store import ContentStore

        mock_renderer = AsyncMock()
        mock_renderer.render = AsyncMock(side_effect=_fake_render)

        mock_lesson = _make_mock_lesson_with_sections()
        store = ContentStore(":memory:")
        lesson_id = "lesson-cues-get"
        store.save_lesson(lesson_id, "some-curriculum-id", 1, mock_lesson)

        app.state.renderer = mock_renderer
        app.state.audio_dir = tmp_path
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/audio/render", json={"lesson_id": lesson_id})
            response = await client.get(f"/api/audio/lesson/{lesson_id}")

        assert response.status_code == 200
        data = response.json()
        assert "cues" in data
        assert len(data["cues"]) > 0

        # A7: each section carries its own rebased cue list (not just the full
        # track). Without this the frontend's per-variant subtitle sync is dead.
        for s in data["sections"]:
            assert "cues" in s
        natural = next(s for s in data["sections"] if s["section_type"] == "natural_speed")
        assert natural["cues"] is not None
        assert natural["cues"][0]["start_ms"] == 0  # rebased to its own zero

    async def test_get_lesson_audio_scrubs_slow_section_cue_text(self, tmp_path):
        """A7 + A6: a slow section's cues expose natural text through the API,
        never the ellipsis-broken text that drives TTS."""
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="T",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[Phrase(text="hvala", voice_id="v", language_code="sl")],
                ),
                Section(
                    section_type=SectionType.SLOW_SPEED,
                    phrases=[Phrase(text="hva ... la", voice_id="v", language_code="sl")],
                ),
            ],
        )
        store = ContentStore(":memory:")
        lesson_id = "lesson-slow-scrub"
        store.save_lesson(lesson_id, "cur", 1, lesson)

        mock_renderer = AsyncMock()
        mock_renderer.render = AsyncMock(side_effect=_fake_render)
        app.state.renderer = mock_renderer
        app.state.audio_dir = tmp_path
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/audio/render", json={"lesson_id": lesson_id})
            response = await client.get(f"/api/audio/lesson/{lesson_id}")

        data = response.json()
        slow = next(s for s in data["sections"] if s["section_type"] == "slow_speed")
        line_texts = [
            c["text"] for c in slow["cues"] if c["language_code"] == "sl" and (c["ref"] or {}).get("kind") == "line"
        ]
        assert line_texts == ["hvala"]
        assert all(" ... " not in t for t in line_texts)

    async def test_get_lesson_audio_returns_null_cues_for_old_lesson(self, tmp_path):
        """GET /api/audio/lesson/{id} returns cues:null for lessons without manifest."""
        from app.storage.store import ContentStore

        store = ContentStore(":memory:")
        lesson_id = "old-no-cues"

        # Insert a full-lesson row with cues_json=NULL (simulating pre-manifest lesson)
        store.save_audio_file("old-full-id", lesson_id, "/tmp/old.wav")
        store.save_audio_file(
            "old-sec-id", lesson_id, "/tmp/old-sec.wav", section_index=0, section_type="natural_speed"
        )

        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(f"/api/audio/lesson/{lesson_id}")

        assert response.status_code == 200
        data = response.json()
        assert "cues" in data
        assert data["cues"] is None

    async def test_get_lesson_audio_returns_404_when_not_rendered(self):
        """GET /api/audio/lesson/{lesson_id} returns 404 when no audio exists."""
        from app.storage.store import ContentStore

        app.state.content_store = ContentStore(":memory:")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/audio/lesson/never-rendered-id")
        assert response.status_code == 404

    async def test_get_audio_sets_content_disposition(self, tmp_path):
        """GET /api/audio/{audio_id} sets Content-Disposition with sanitized filename."""
        from app.storage.store import ContentStore

        mock_renderer = AsyncMock()
        mock_renderer.render = AsyncMock(side_effect=_fake_render)

        mock_lesson = _make_mock_lesson_with_sections()
        store = ContentStore(":memory:")
        curriculum = Curriculum(id="c-dl", topic="ordering coffee", language_code="sl", cefr_level="A2")
        store.save_curriculum("c-dl", curriculum)
        lesson_id = "lesson-download-test"
        store.save_lesson(lesson_id, "c-dl", 1, mock_lesson)

        app.state.renderer = mock_renderer
        app.state.audio_dir = tmp_path
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            render_resp = await client.post("/api/audio/render", json={"lesson_id": lesson_id})
            data = render_resp.json()

            # Check full lesson download filename
            full_audio_id = data["audio_id"]
            response = await client.get(f"/api/audio/{full_audio_id}")

        assert response.status_code == 200
        cd = response.headers.get("content-disposition", "")
        assert "attachment" in cd
        # Default delivery codec is opus, so the rendered file is served as .opus.
        assert ".opus" in cd
        assert "ordering_coffee" in cd.lower()

    async def test_get_audio_serves_ogg_media_type_for_opus_file(self, tmp_path):
        """A stored .opus file is served as audio/ogg (media type inferred from suffix)."""
        from app.storage.store import ContentStore

        store = ContentStore(":memory:")
        opus = tmp_path / "audio.opus"
        opus.write_bytes(b"OggS-fake-opus")
        store.save_audio_file("opus-audio", "ghost-lesson", str(opus))
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/audio/opus-audio")

        assert response.status_code == 200
        assert response.headers["content-type"] == "audio/ogg"
        assert ".opus" in response.headers.get("content-disposition", "")

    async def test_get_audio_serves_wav_media_type_for_wav_file(self, tmp_path):
        """A pre-existing .wav file still serves as audio/wav (back-compat)."""
        from app.storage.store import ContentStore

        store = ContentStore(":memory:")
        wav = tmp_path / "audio.wav"
        wav.write_bytes(b"RIFF-fake-wav")
        store.save_audio_file("wav-audio", "ghost-lesson", str(wav))
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/audio/wav-audio")

        assert response.status_code == 200
        assert response.headers["content-type"] == "audio/wav"

    async def test_get_audio_section_content_disposition(self, tmp_path):
        """GET /api/audio/{section_audio_id} includes topic, day, and section type in filename."""
        from app.storage.store import ContentStore

        mock_renderer = AsyncMock()
        mock_renderer.render = AsyncMock(side_effect=_fake_render)

        mock_lesson = _make_mock_lesson_with_sections()
        store = ContentStore(":memory:")
        curriculum = Curriculum(id="c-sec-dl", topic="ordering coffee", language_code="sl", cefr_level="A2")
        store.save_curriculum("c-sec-dl", curriculum)
        lesson_id = "lesson-sec-dl-test"
        store.save_lesson(lesson_id, "c-sec-dl", 1, mock_lesson)

        app.state.renderer = mock_renderer
        app.state.audio_dir = tmp_path
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            render_resp = await client.post("/api/audio/render", json={"lesson_id": lesson_id})
            sec_audio_id = render_resp.json()["sections"][0]["audio_id"]
            response = await client.get(f"/api/audio/{sec_audio_id}")

        assert response.status_code == 200
        cd = response.headers.get("content-disposition", "")
        assert "attachment" in cd
        assert "Key_Phrases" in cd
        assert "ordering_coffee" in cd.lower()
        assert "Day01" in cd

    async def test_audio_get_returns_404_when_missing(self):
        from app.storage.store import ContentStore

        app.state.content_store = ContentStore(":memory:")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/audio/nonexistent-id")
        assert response.status_code == 404

    async def test_get_lesson_audio_returns_404_when_no_full_row(self):
        """GET /audio/lesson/{id} returns 404 when only section rows exist (no full-lesson row)."""
        from app.storage.store import ContentStore

        store = ContentStore(":memory:")
        # Save a section-only row (section_index=0, no full row)
        store.save_audio_file("sec-1", "lesson-x", "/tmp/sec.wav", section_index=0, section_type="key_phrases")
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/audio/lesson/lesson-x")

        assert response.status_code == 404
        assert "Full lesson audio" in response.json()["detail"]

    async def test_get_lesson_audio_falls_back_for_unknown_section_type(self):
        """GET /audio/lesson/{id} gracefully uses raw string when section_type is unrecognized."""
        from app.storage.store import ContentStore

        store = ContentStore(":memory:")
        # Full row
        store.save_audio_file("full-1", "lesson-y", "/tmp/full.wav")
        # Section row with unknown section_type
        store.save_audio_file("sec-2", "lesson-y", "/tmp/sec.wav", section_index=0, section_type="unknown_custom")
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/audio/lesson/lesson-y")

        assert response.status_code == 200
        data = response.json()
        # The title should fall back to the raw section_type string
        assert data["sections"][0]["title"] == "unknown_custom"

    async def test_get_audio_returns_404_when_file_missing_on_disk(self, tmp_path):
        """GET /api/audio/{audio_id} returns 404 when DB row exists but file is absent."""
        from app.storage.store import ContentStore

        store = ContentStore(":memory:")
        nonexistent_path = str(tmp_path / "does_not_exist.wav")
        store.save_audio_file("audio-gone", "lesson-z", nonexistent_path)
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/audio/audio-gone")

        assert response.status_code == 404
        assert "missing" in response.json()["detail"]

    # ── ZIP download endpoint tests ───────────────────────────────────────

    async def test_zip_download_returns_zip_with_sections(self, tmp_path):
        """GET /api/audio/lesson/{id}/zip returns a ZIP containing all section WAVs."""
        import io
        import zipfile

        from app.storage.store import ContentStore

        mock_renderer = AsyncMock()
        mock_renderer.render = AsyncMock(side_effect=_fake_render)

        mock_lesson = _make_mock_lesson_with_sections()
        store = ContentStore(":memory:")
        curriculum = Curriculum(id="c1", topic="ordering coffee", language_code="sl", cefr_level="A2")
        store.save_curriculum("c1", curriculum)
        store.save_lesson("lesson-zip-1", "c1", 1, mock_lesson)

        app.state.renderer = mock_renderer
        app.state.audio_dir = tmp_path
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/audio/render", json={"lesson_id": "lesson-zip-1"})
            response = await client.get("/api/audio/lesson/lesson-zip-1/zip")

        assert response.status_code == 200
        assert response.headers["content-type"] == "application/zip"
        z = zipfile.ZipFile(io.BytesIO(response.content))
        names = z.namelist()
        # full lesson + one per section
        assert len(names) == len(mock_lesson.sections) + 1

    async def test_zip_download_filenames_include_topic_and_day(self, tmp_path):
        """ZIP filenames include sanitized curriculum topic and zero-padded day."""
        import io
        import zipfile

        from app.storage.store import ContentStore

        mock_renderer = AsyncMock()
        mock_renderer.render = AsyncMock(side_effect=_fake_render)

        mock_lesson = _make_mock_lesson_with_sections()
        store = ContentStore(":memory:")
        curriculum = Curriculum(id="c2", topic="ordering coffee", language_code="sl", cefr_level="A2")
        store.save_curriculum("c2", curriculum)
        store.save_lesson("lesson-zip-2", "c2", 3, mock_lesson)

        app.state.renderer = mock_renderer
        app.state.audio_dir = tmp_path
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/audio/render", json={"lesson_id": "lesson-zip-2"})
            response = await client.get("/api/audio/lesson/lesson-zip-2/zip")

        assert response.status_code == 200
        z = zipfile.ZipFile(io.BytesIO(response.content))
        names = z.namelist()
        for name in names:
            assert "ordering_coffee" in name.lower()
            assert "Day03" in name
        # full file sorts first (00), then sections (01, 02…); opus is the default codec
        assert names[0].endswith("_00_Full.opus")
        assert names[1].endswith("_01_Key_Phrases.opus")
        assert names[2].endswith("_02_Natural_Speed.opus")

    async def test_zip_download_content_disposition_header(self, tmp_path):
        """ZIP Content-Disposition includes topic and day in the filename."""
        from app.storage.store import ContentStore

        mock_renderer = AsyncMock()
        mock_renderer.render = AsyncMock(side_effect=_fake_render)

        mock_lesson = _make_mock_lesson_with_sections()
        store = ContentStore(":memory:")
        curriculum = Curriculum(id="c3", topic="ordering coffee", language_code="sl", cefr_level="A2")
        store.save_curriculum("c3", curriculum)
        store.save_lesson("lesson-zip-3", "c3", 2, mock_lesson)

        app.state.renderer = mock_renderer
        app.state.audio_dir = tmp_path
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/audio/render", json={"lesson_id": "lesson-zip-3"})
            response = await client.get("/api/audio/lesson/lesson-zip-3/zip")

        assert response.status_code == 200
        cd = response.headers.get("content-disposition", "")
        assert "attachment" in cd
        assert ".zip" in cd
        assert "ordering_coffee" in cd.lower()
        assert "Day02" in cd

    async def test_zip_download_returns_404_when_no_audio(self):
        """GET /api/audio/lesson/{id}/zip returns 404 when no audio exists."""
        from app.storage.store import ContentStore

        app.state.content_store = ContentStore(":memory:")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/audio/lesson/no-audio/zip")
        assert response.status_code == 404

    async def test_zip_download_returns_404_when_no_sections(self):
        """GET /api/audio/lesson/{id}/zip returns 404 when only a full-lesson row exists."""
        from app.storage.store import ContentStore

        store = ContentStore(":memory:")
        store.save_audio_file("full-only", "lesson-no-sec", "/tmp/full.wav")
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/audio/lesson/lesson-no-sec/zip")
        assert response.status_code == 404
        assert "section" in response.json()["detail"].lower()

    async def test_zip_download_falls_back_when_curriculum_missing(self, tmp_path):
        """ZIP endpoint uses lesson title as fallback when curriculum is not found."""
        import io
        import zipfile

        from app.storage.store import ContentStore

        mock_renderer = AsyncMock()
        mock_renderer.render = AsyncMock(side_effect=_fake_render)

        mock_lesson = _make_mock_lesson_with_sections()
        store = ContentStore(":memory:")
        # Save lesson with a curriculum_id that has no corresponding curriculum row
        store.save_lesson("lesson-zip-fallback", "missing-c", 5, mock_lesson)

        app.state.renderer = mock_renderer
        app.state.audio_dir = tmp_path
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/audio/render", json={"lesson_id": "lesson-zip-fallback"})
            response = await client.get("/api/audio/lesson/lesson-zip-fallback/zip")

        assert response.status_code == 200
        z = zipfile.ZipFile(io.BytesIO(response.content))
        names = z.namelist()
        # full lesson + sections
        assert len(names) == len(mock_lesson.sections) + 1

    async def test_zip_download_returns_404_when_section_file_missing_on_disk(self, tmp_path):
        """ZIP endpoint returns 404 when a section file is absent from disk."""
        from app.storage.store import ContentStore

        store = ContentStore(":memory:")
        store.save_audio_file("full-x", "lesson-missing-file", str(tmp_path / "full.wav"))
        store.save_audio_file(
            "sec-x", "lesson-missing-file", str(tmp_path / "missing.wav"), section_index=0, section_type="key_phrases"
        )
        # Write the full file but NOT the section file
        (tmp_path / "full.wav").write_bytes(b"audio")
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/audio/lesson/lesson-missing-file/zip")
        assert response.status_code == 404
        assert "missing" in response.json()["detail"].lower()

    async def test_zip_download_falls_back_to_defaults_when_lesson_row_missing(self, tmp_path):
        """ZIP uses fallback topic/day when no lesson row exists in the DB."""
        import io
        import zipfile

        from app.storage.store import ContentStore

        store = ContentStore(":memory:")
        # Save only audio rows — no lesson row in lessons table
        sec_path = tmp_path / "sec.wav"
        sec_path.write_bytes(b"audio")
        store.save_audio_file(
            "sec-no-lesson", "lesson-ghost", str(sec_path), section_index=0, section_type="key_phrases"
        )
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/audio/lesson/lesson-ghost/zip")

        assert response.status_code == 200
        z = zipfile.ZipFile(io.BytesIO(response.content))
        assert len(z.namelist()) == 1

    async def test_build_section_filename_falls_back_for_unknown_section_type(self, tmp_path):
        """_build_section_filename uses the raw string for unrecognized section types."""
        from app.api.audio import _build_section_filename

        name = _build_section_filename("topic", 1, 0, "custom_unknown")
        assert "custom_unknown" in name
        assert name.endswith(".wav")

    async def test_get_audio_falls_back_when_no_lesson_row(self, tmp_path):
        """GET /api/audio/{id} uses fallback name when lesson row is absent."""
        from app.storage.store import ContentStore

        store = ContentStore(":memory:")
        wav = tmp_path / "audio.wav"
        wav.write_bytes(b"data")
        # Save audio row but no lesson row
        store.save_audio_file("audio-no-lesson", "ghost-lesson", str(wav))
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/audio/audio-no-lesson")

        assert response.status_code == 200
        cd = response.headers.get("content-disposition", "")
        assert "audio" in cd
        assert ".wav" in cd

    async def test_get_audio_uses_lesson_title_when_curriculum_missing(self, tmp_path):
        """GET /api/audio/{id} falls back to lesson title when curriculum row is absent."""
        from app.storage.store import ContentStore

        mock_renderer = AsyncMock()
        mock_renderer.render = AsyncMock(side_effect=_fake_render)

        mock_lesson = _make_mock_lesson_with_sections()
        store = ContentStore(":memory:")
        # Save lesson with no matching curriculum
        store.save_lesson("lesson-no-c", "nonexistent-curriculum", 2, mock_lesson)

        app.state.renderer = mock_renderer
        app.state.audio_dir = tmp_path
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            render_resp = await client.post("/api/audio/render", json={"lesson_id": "lesson-no-c"})
            sec_id = render_resp.json()["sections"][0]["audio_id"]
            response = await client.get(f"/api/audio/{sec_id}")

        assert response.status_code == 200
        cd = response.headers.get("content-disposition", "")
        assert ".opus" in cd
        # Lesson title is "Day 1: Ordering Coffee" → sanitized
        assert "Day_1" in cd or "Ordering_Coffee" in cd


class TestCreatePhraseIntegration:
    """Integration tests for multi-word phrase creation via POST /api/srs/items."""

    async def test_create_multiword_item_returns_201(self):
        """POST /api/srs/items with word_count=2 creates a SyntacticUnit with lemma=None."""
        from app.srs.database import SRSDatabase

        db = SRSDatabase(":memory:")
        app.state.srs_db = db

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/srs/items",
                json={"text": "centru mesta", "language_code": "sl", "word_count": 2, "translation": ""},
            )

        assert response.status_code == 201
        data = response.json()
        assert data["text"] == "centru mesta"
        # Multi-word items have no lemma
        assert data.get("translation") == ""

    async def test_create_multiword_item_duplicate_returns_409(self):
        """Second POST with same text returns 409 Conflict."""
        from app.srs.database import SRSDatabase

        db = SRSDatabase(":memory:")
        app.state.srs_db = db

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post(
                "/api/srs/items",
                json={"text": "centru mesta", "language_code": "sl", "word_count": 2, "translation": ""},
            )
            response = await client.post(
                "/api/srs/items",
                json={"text": "centru mesta", "language_code": "sl", "word_count": 2, "translation": ""},
            )

        assert response.status_code == 409

    async def test_create_phrase_then_transcript_shows_collocation_span(self):
        """After creating 'centru mesta', transcript tokens for that phrase
        share a collocation_span_id and collocation_lemma='centru mesta'."""
        from app.models.lesson import Lesson, Phrase, Section, SectionType
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[
                        Phrase(
                            text="v centru mesta",
                            voice_id="female-1",
                            language_code="sl",
                            role="female-1",
                        )
                    ],
                )
            ],
            key_phrases=[],
        )

        db = SRSDatabase(":memory:")
        store = ContentStore(":memory:")
        store.save_lesson("lesson-phrase", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # Create the phrase
            create_resp = await client.post(
                "/api/srs/items",
                json={"text": "centru mesta", "language_code": "sl", "word_count": 2, "translation": "city centre"},
            )
            assert create_resp.status_code == 201

            # Fetch transcript — match_spans should pick up the new collocation
            transcript_resp = await client.get("/api/srs/lesson/lesson-phrase/transcript")
            assert transcript_resp.status_code == 200

        data = transcript_resp.json()
        words = data["dialogue_lines"][0]["words"]

        # Find centru and mesta tokens
        centru = next((w for w in words if w["surface"] == "centru"), None)
        mesta = next((w for w in words if w["surface"] == "mesta"), None)

        assert centru is not None, "centru token not found"
        assert mesta is not None, "mesta token not found"
        assert centru["collocation_span_id"] is not None
        assert mesta["collocation_span_id"] is not None
        assert centru["collocation_span_id"] == mesta["collocation_span_id"]
        assert centru["collocation_lemma"] == "centru mesta"


class TestClozeTTSIntegration:
    """Tests for cloze TTS audio generation via /listen and /review-queue."""

    async def test_listen_creates_media_for_new_cloze(self, monkeypatch):
        """New cloze from /listen gets both audio_tts_sentence and audio_tts media rows."""
        import app.audio.cloze_tts as cloze_tts_mod

        async def _fake_tts(text, voice="sl-SI-PetraNeural"):
            return b"fake-mp3"

        monkeypatch.setattr(cloze_tts_mod, "generate_tts_audio", _fake_tts)

        from app.models.lesson import Lesson, Phrase, Section, SectionType
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[
                        Phrase(text="Kje je banka?", voice_id="female-1", language_code="sl", role="female-1"),
                    ],
                )
            ],
            key_phrases=[],
        )

        db = SRSDatabase(":memory:")
        store = ContentStore(":memory:")
        store.save_lesson("lesson-ct", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-ct"})
        assert response.status_code == 200

        for lemma in ("kje", "je"):
            coll = db.get_collocation_by_lemma_with_id(lemma)
            assert coll is not None, f"{lemma} should exist"
            coll_id, _ = coll
            sent_fn = db.get_sentence_audio_filename(coll_id)
            word_fn = db.get_audio_filename(coll_id)
            assert sent_fn is not None, f"{lemma} missing sentence audio filename"
            assert word_fn is not None, f"{lemma} missing word audio filename"
            assert sent_fn.startswith("tts_sentence_")
            assert word_fn.startswith("tts_")

    async def test_review_queue_returns_word_audio_url_for_cloze(self):
        """Cloze cards in the review queue have word_audio_url set; vocab cards do not."""
        from datetime import date

        from app.models.srs_item import DirectionState
        from app.models.syntactic_unit import SyntacticUnit
        from app.srs.database import SRSDatabase

        db = SRSDatabase(":memory:")
        app.state.srs_db = db

        # Cloze collocation
        cloze_unit = SyntacticUnit(
            text="je",
            translation="is",
            word_count=1,
            difficulty=1,
            source="llm",
            lemma="je",
            card_type="cloze",
            source_sentence="Kje je banka?",
        )
        cloze_dir = {
            Direction.PRODUCTION: DirectionState(
                Direction.PRODUCTION,
                date.today(),
                state=SRSState.NEW,
            )
        }
        cloze_id = db.upsert_by_guid(cloze_unit, "sl", cloze_dir)

        db.add_media(cloze_id, "audio_tts_sentence", "tts_sentence_abc.mp3", "/tmp/s.mp3", "", "s1", 100)
        db.add_media(cloze_id, "audio_tts", "tts_je.mp3", "/tmp/w.mp3", "", "w1", 100)

        # Vocab collocation
        vocab_unit = SyntacticUnit(
            text="banka",
            translation="bank",
            word_count=1,
            difficulty=1,
            source="llm",
            lemma="banka",
        )
        dirs = {
            Direction.RECOGNITION: DirectionState(Direction.RECOGNITION, date.today(), state=SRSState.NEW),
            Direction.PRODUCTION: DirectionState(Direction.PRODUCTION, date.today(), state=SRSState.NEW),
        }
        vocab_id = db.upsert_by_guid(vocab_unit, "sl", dirs)
        db.add_media(vocab_id, "audio_tts", "tts_banka.mp3", "/tmp/w.mp3", "", "w2", 100)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/srs/review-queue", params={"session_start": "1"})
        assert response.status_code == 200

        items = response.json()["queue"]
        cloze_items = [i for i in items if i.get("card_type") == "cloze"]
        vocab_items = [i for i in items if i.get("card_type") == "vocab"]

        assert len(cloze_items) >= 1
        assert len(vocab_items) >= 1

        for ci in cloze_items:
            assert ci.get("word_audio_url") is not None, f"cloze {ci['text']} missing word_audio_url"
            assert ci.get("audio_url") is not None, f"cloze {ci['text']} missing audio_url"

        for vi in vocab_items:
            assert vi.get("word_audio_url") is None, f"vocab {vi['text']} should not have word_audio_url"
            assert vi.get("audio_url") is not None, f"vocab {vi['text']} missing audio_url"

    async def test_listen_tolerates_synthesizer_error_new_cloze(self, monkeypatch):
        """New function-word cloze card is created even if TTS fails."""
        import app.api.srs as srs_mod

        async def _broken_synth(db, collocation_id, sentence, word, *, voice=None):
            raise RuntimeError("TTS failed")

        monkeypatch.setattr(srs_mod, "synthesize_cloze_audios", _broken_synth)

        from app.models.lesson import Lesson, Phrase, Section, SectionType
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[
                        Phrase(
                            text="Kje je banka v Ljubljani?", voice_id="female-1", language_code="sl", role="female-1"
                        ),
                    ],
                )
            ],
            key_phrases=[],
            generation_metadata={
                "token_glosses": {
                    "kje": "where",
                    "je": "is",
                    "banka": "bank",
                    "v": "in",
                    "ljubljana": "Ljubljana",
                },
                "sentence_translations": {
                    "Kje je banka v Ljubljani?": "Where is the bank in Ljubljana?",
                },
            },
        )

        db = SRSDatabase(":memory:")

        store = ContentStore(":memory:")
        store.save_lesson("lesson-ct2", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-ct2"})
        assert response.status_code == 200

        # Function-word cloze card should still exist
        coll = db.get_collocation_by_lemma("kje")
        assert coll is not None

    async def test_listen_threads_language_voice_into_cloze_synth(self, monkeypatch):
        """Backlog #28: cloze audio is synthesized in the lesson language's voice,
        not the hardcoded Slovene default (guards the srs.py call-site wiring)."""
        import app.api.srs as srs_mod

        captured: list[str | None] = []

        async def _capture(db, collocation_id, sentence, word, *, voice=None):
            captured.append(voice)

        monkeypatch.setattr(srs_mod, "synthesize_cloze_audios", _capture)

        from app.models.lesson import Lesson, Phrase, Section, SectionType
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[Phrase(text="Kje je banka?", voice_id="female-1", language_code="sl", role="female-1")],
                )
            ],
            key_phrases=[],
        )
        db = SRSDatabase(":memory:")
        store = ContentStore(":memory:")
        store.save_lesson("lesson-ctv", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-ctv"})
        assert response.status_code == 200
        # Every synth call for a Slovene lesson uses the Slovene voice.
        assert captured and all(v == "sl-SI-PetraNeural" for v in captured)

    async def test_listen_tolerates_synthesizer_error_existing_cloze(self, monkeypatch):
        """Existing cloze card audio backfill failure doesn't crash the endpoint."""
        import app.api.srs as srs_mod

        calls = [0]

        async def _succeed_once_then_fail(db, collocation_id, sentence, word, *, voice=None):
            calls[0] += 1
            if calls[0] > 1:
                raise RuntimeError("TTS failed on second call")
            return None

        monkeypatch.setattr(srs_mod, "synthesize_cloze_audios", _succeed_once_then_fail)

        from app.models.lesson import Lesson, Phrase, Section, SectionType
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[
                        Phrase(text="Kje je banka?", voice_id="female-1", language_code="sl", role="female-1"),
                    ],
                )
            ],
            key_phrases=[],
        )

        db = SRSDatabase(":memory:")
        store = ContentStore(":memory:")
        store.save_lesson("lesson-ct3", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        # First listen creates cloze cards and succeeds at TTS
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-ct3"})
        assert response.status_code == 200
        assert calls[0] >= 1  # at least one TTS call succeeded

        # Second listen should hit the existing cloze backfill path with failure
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-ct3"})
        assert response.status_code == 200

        # Cloze card should still exist
        coll = db.get_collocation_by_lemma("kje")
        assert coll is not None
