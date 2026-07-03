"""Tests for application configuration."""

from pathlib import Path

from app.config import Settings


def test_settings_defaults(monkeypatch, tmp_path):
    for var in ("GROQ_API_KEY", "DATABASE_URL", "LLM_MODE", "LLM_MODEL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)
    s = Settings(_env_file=None)
    assert s.groq_api_key == ""
    assert s.database_url == "sqlite:///./tunatale_sl.db"
    assert s.llm_mode == "mock"
    assert s.llm_model == "openai/gpt-oss-120b"


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key-123")
    monkeypatch.setenv("LLM_MODE", "live")
    s = Settings()
    assert s.groq_api_key == "test-key-123"
    assert s.llm_mode == "live"


def test_target_language_default(monkeypatch, tmp_path):
    """target_language defaults to 'sl'."""
    for var in ("GROQ_API_KEY", "DATABASE_URL", "LLM_MODE", "TARGET_LANGUAGE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)
    s = Settings(_env_file=None)
    assert s.target_language == "sl"


def test_target_language_from_env(monkeypatch):
    monkeypatch.setenv("TARGET_LANGUAGE", "no")
    s = Settings()
    assert s.target_language == "no"


def test_anki_settings_defaults(monkeypatch, tmp_path):
    """New Anki-related settings have expected defaults."""
    for var in (
        "GROQ_API_KEY",
        "DATABASE_URL",
        "LLM_MODE",
        "LLM_MODEL",
        "ANKI_COLLECTION_PATH",
        "ANKI_MEDIA_PATH",
        "ANKI_DECK_NAME",
        "ANKI_BACKUP_DIR",
        "MEDIA_DIR",
        "ANKI_FALLBACK_LOG",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)
    s = Settings(_env_file=None)
    assert s.anki_collection_path == Path("~/Library/Application Support/Anki2/Will/collection.anki2").expanduser()
    assert s.anki_media_path == Path("~/Library/Application Support/Anki2/Will/collection.media").expanduser()
    assert s.anki_deck_name == "1. Slovene"
    assert s.anki_backup_dir == Path("~/.tunatale/anki-backups").expanduser()
    assert s.media_dir == Path("./media")
    assert s.anki_fallback_log == Path("~/.tunatale/logs/anki-fallback.log").expanduser()


def test_anki_model_and_pixabay_defaults(monkeypatch, tmp_path):
    """Anki model name and media pipeline settings have expected defaults."""
    for var in (
        "ANKI_MODEL_NAME",
        "PIXABAY_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)
    s = Settings(_env_file=None)
    assert s.anki_model_name == ""
    assert s.pixabay_api_key == ""


def test_anki_model_and_pixabay_from_env(monkeypatch):
    """Anki model name and media pipeline settings load from env vars."""
    monkeypatch.setenv("ANKI_MODEL_NAME", "Slovene Vocabulary")
    monkeypatch.setenv("PIXABAY_API_KEY", "pixabay-key-xyz")
    s = Settings()
    assert s.anki_model_name == "Slovene Vocabulary"
    assert s.pixabay_api_key == "pixabay-key-xyz"
