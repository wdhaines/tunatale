"""Application configuration via Pydantic Settings."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    groq_api_key: str = ""
    database_url: str = "sqlite:///./tunatale.db"
    llm_mode: str = "mock"  # mock | live | record | patch
    llm_model: str = "llama-3.3-70b-versatile"


settings = Settings()
