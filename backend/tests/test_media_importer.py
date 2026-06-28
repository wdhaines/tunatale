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

    # Audio vs image is decided by EXTENSION, not just the prefix — Slovene names
    # audio `sl_*`/`tts_*`, but other decks (Norwegian) use `forvo-*`/`azure-*`,
    # which a prefix-only rule mislabelled as images (broken <img> on every card).
    def test_norwegian_forvo_mp3_is_audio_forvo(self):
        assert infer_kind("forvo-f39077c4-a88e7bd2.mp3") == "audio_forvo"

    def test_norwegian_azure_mp3_is_audio_tts(self):
        assert infer_kind("azure-14734ddc-3aa2919f.mp3") == "audio_tts"

    def test_generic_mp3_without_source_marker_is_audio_tts(self):
        assert infer_kind("clip.mp3") == "audio_tts"

    def test_ogg_extension_is_audio(self):
        assert infer_kind("sound.ogg") == "audio_tts"

    def test_audio_extension_overrides_image_default(self):
        # An audio file with no recognised prefix must NOT fall through to image.
        assert infer_kind("recording-123.m4a") == "audio_tts"

    def test_tts_sentence_is_its_own_kind(self):
        # Cloze Back sentence audio is queried by get_sentence_audio_filename;
        # it must not be folded into plain audio_tts.
        assert infer_kind("tts_sentence_7e8494b5d8627dd4.mp3") == "audio_tts_sentence"

    def test_unknown_extension_falls_back_to_sl_prefix(self):
        # No recognised extension → legacy prefix heuristic still classifies audio.
        assert infer_kind("sl_legacy_no_ext") == "audio_forvo"

    def test_unknown_extension_falls_back_to_tts_prefix(self):
        assert infer_kind("tts_legacy_no_ext") == "audio_tts"
