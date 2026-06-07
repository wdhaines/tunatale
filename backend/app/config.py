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
    # Durable per-sync soak log: every non-dry sync (CLI or API) appends a
    # SYNC_SOAK heartbeat + one RECOMPUTE_DIVERGENCE line per divergence.
    sync_log: Path = Path("~/.tunatale/logs/sync.log").expanduser()

    # Peer-sync (anki subprocess) config — see sync_orchestrator.py.
    tt_collection_path: Path = Path("~/.tunatale/tt_collection.anki2").expanduser()
    sync_enabled: bool = False
    sync_endpoint: str = ""  # "" → AnkiWeb default; else self-host URL
    sync_username: str = ""
    # AnkiWeb password. Prefer the macOS Keychain (see sync_keychain_service); this
    # env/.env value is an override fallback and should normally stay EMPTY (plaintext).
    sync_password: str = ""
    # macOS Keychain generic-password service the AnkiWeb password is stored under
    # (account = sync_username). Store it with:
    #   security add-generic-password -s tunatale-ankiweb -a <username> -w
    sync_keychain_service: str = "tunatale-ankiweb"
    # Optional pin for the sync subprocess (`uv run --with anki==X`). Empty → latest
    # anki. Set to match your desktop Anki's sync-protocol version if a mismatch appears.
    anki_pkg_version: str = ""
    # Interpreter for the anki driver subprocess. It runs isolated + project-free
    # (--no-project), which escapes the project lock's stale protobuf 4.21.2 (dragged in
    # by the classla+anki extras; no cp314 wheel) — a clean resolve pulls a current
    # protobuf that imports fine on 3.14. Pin to an older Python here only if a future
    # anki/protobuf breaks on the latest.
    anki_subprocess_python: str = "3.14"

    anki_connect_url: str = "http://127.0.0.1:8765"
    anki_model_name: str = ""
    forvo_api_key: str = ""
    pixabay_api_key: str = ""
    lemmatizer_type: str = "lowercase"  # lowercase | classla

    anki_new_per_day_default: int = 20
    anki_reviews_per_day_default: int = 200


settings = Settings()
