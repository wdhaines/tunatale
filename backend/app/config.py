"""Application configuration via Pydantic Settings."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    groq_api_key: str = ""
    # Per-language DB (one-DB-per-language isolation). Default is the Slovene DB;
    # switch languages by flipping target_language AND database_url together
    # (e.g. sqlite:///./tunatale_no.db for Norwegian).
    database_url: str = "sqlite:///./tunatale_sl.db"
    # Phase 5 — simultaneous multi-language. When non-empty, the app opens one
    # connection per entry (``{"sl": "sqlite:///./tunatale_sl.db", "no": "…_no.db"}``)
    # and resolves the active one per request from the X-TT-Language header. Empty
    # (the default) = single-language: one connection from ``database_url`` bound to
    # ``target_language``. ``target_language`` is the default when no header is sent.
    database_urls: dict[str, str] = {}
    llm_mode: str = "mock"  # mock | live | record | patch
    # gpt-oss-120b replaces llama-3.3-70b-versatile (deprecated by Groq 2026-06-30).
    # It is a reasoning model — main.py pins reasoning_effort=low via
    # reasoning_params_for_model() so it emits content instead of burning the whole
    # budget on reasoning. Free-tier TPM is 8000; WIDER story gen fits, DEEPER (bigger
    # prompt) can approach the ceiling.
    llm_model: str = "openai/gpt-oss-120b"

    target_language: str = "sl"

    anki_collection_path: Path = Path("~/Library/Application Support/Anki2/Will/collection.anki2").expanduser()
    anki_media_path: Path = Path("~/Library/Application Support/Anki2/Will/collection.media").expanduser()
    anki_deck_name: str = "1. Slovene"
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

    anki_model_name: str = ""
    pixabay_api_key: str = ""
    # lowercase (default) | classla (Slovene) | stanza (Norwegian + other Stanza
    # langs, wired to target_language). One engine per process — see get_lemmatizer.
    lemmatizer_type: str = "lowercase"

    anki_new_per_day_default: int = 20
    anki_reviews_per_day_default: int = 200

    # Lesson audio delivery format. Opus is ~10-20× smaller than WAV for speech,
    # cutting mobile-data use when streaming lessons to a phone. Set to "wav" to
    # restore uncompressed delivery. Codec must be a key of transcode.CODEC_EXT.
    audio_delivery_codec: str = "opus"  # opus | aac | mp3 | wav
    audio_delivery_bitrate: str = "28k"


settings = Settings()


# Anki rolls the study day over at this *local* hour (default 4 AM), not at
# midnight — a grade timestamped between local midnight and the rollover belongs
# to the PRIOR Anki day. The rollover arithmetic is single-sourced in
# `app.anki.rollover` (local-day domain: `local_today_rollover`,
# `anki_day_bounds_utc`, `anki_today`; due_at convention: `due_at_rollover_utc`);
# `app.anki.protobuf_wire` owns the separate col-day index domain
# (`compute_anki_day_index`, `review_due_at_for_col_day`). Both derive from this
# constant. Promote to a Settings field if it ever needs to be config-driven
# (Anki stores it per-collection).
ANKI_ROLLOVER_HOUR = 4
