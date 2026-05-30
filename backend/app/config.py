"""Application configuration via Pydantic Settings."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    groq_api_key: str = ""
    database_url: str = "sqlite:///./tunatale.db"
    llm_mode: str = "mock"  # mock | live | record | patch
    llm_model: str = "llama-3.3-70b-versatile"

    anki_collection_path: Path = Path("~/Library/Application Support/Anki2/Will/collection.anki2").expanduser()
    anki_media_path: Path = Path("~/Library/Application Support/Anki2/Will/collection.media").expanduser()
    anki_deck_name: str = "0. Slovene"
    anki_backup_dir: Path = Path("~/.tunatale/anki-backups").expanduser()
    # Retention cap for the safe_open backup directory. safe_open writes a full
    # ~16 MB collection snapshot on every call; without a cap the directory grows
    # without bound. Keep the N most recent snapshots (~16 MB each); <= 0 disables.
    anki_backup_keep: int = 30
    media_dir: Path = Path("./media")
    anki_fallback_log: Path = Path("~/.tunatale/logs/anki-fallback.log").expanduser()

    anki_connect_url: str = "http://127.0.0.1:8765"
    anki_model_name: str = ""
    forvo_api_key: str = ""
    pixabay_api_key: str = ""
    anki_new_per_day_default: int = 20
    anki_reviews_per_day_default: int = 200


settings = Settings()
