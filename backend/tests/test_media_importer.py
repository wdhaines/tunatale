"""Tests for the media copy-from-Anki importer."""

import hashlib

from app.media.importer import copy_media_file, infer_kind


def _sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class TestCopyMediaFile:
    def test_copies_file_to_dest(self, tmp_path):
        src = tmp_path / "anki_media" / "sl_banka.mp3"
        src.parent.mkdir()
        src.write_bytes(b"fake audio data")
        dest_dir = tmp_path / "media"

        result = copy_media_file(src, dest_dir)

        assert result.dest_path.exists()
        assert result.dest_path.parent == dest_dir

    def test_dest_is_byte_identical_to_source(self, tmp_path):
        src = tmp_path / "anki_media" / "sl_banka.mp3"
        src.parent.mkdir()
        src.write_bytes(b"fake audio bytes 12345")
        dest_dir = tmp_path / "media"

        result = copy_media_file(src, dest_dir)

        assert src.read_bytes() == result.dest_path.read_bytes()

    def test_sha256_recorded_correctly(self, tmp_path):
        src = tmp_path / "anki_media" / "sl_banka.mp3"
        src.parent.mkdir()
        content = b"hello world"
        src.write_bytes(content)
        dest_dir = tmp_path / "media"

        result = copy_media_file(src, dest_dir)

        expected = hashlib.sha256(content).hexdigest()
        assert result.sha256 == expected

    def test_dest_dir_created_if_missing(self, tmp_path):
        src = tmp_path / "sl_test.mp3"
        src.write_bytes(b"data")
        dest_dir = tmp_path / "new" / "media"

        copy_media_file(src, dest_dir)

        assert dest_dir.exists()

    def test_returns_correct_kind_audio_forvo(self, tmp_path):
        src = tmp_path / "sl_banka.mp3"
        src.write_bytes(b"data")
        result = copy_media_file(src, tmp_path / "media")
        assert result.kind == "audio_forvo"

    def test_returns_correct_kind_audio_tts(self, tmp_path):
        src = tmp_path / "tts_banka.mp3"
        src.write_bytes(b"data")
        result = copy_media_file(src, tmp_path / "media")
        assert result.kind == "audio_tts"

    def test_returns_correct_kind_image(self, tmp_path):
        src = tmp_path / "banka.jpg"
        src.write_bytes(b"data")
        result = copy_media_file(src, tmp_path / "media")
        assert result.kind == "image"


class TestInferKind:
    def test_sl_prefix_is_audio_forvo(self):
        assert infer_kind("sl_banka.mp3") == "audio_forvo"

    def test_tts_prefix_is_audio_tts(self):
        assert infer_kind("tts_banka.mp3") == "audio_tts"

    def test_jpg_is_image(self):
        assert infer_kind("banka.jpg") == "image"

    def test_png_is_image(self):
        assert infer_kind("banka.png") == "image"

    def test_unknown_prefix_is_image(self):
        assert infer_kind("some_file.webm") == "image"
