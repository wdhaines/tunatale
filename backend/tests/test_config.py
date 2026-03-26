"""Tests for application configuration."""

from app.config import Settings


def test_settings_defaults(monkeypatch, tmp_path):
    for var in ("GROQ_API_KEY", "DATABASE_URL", "LLM_MODE", "LLM_MODEL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)
    s = Settings(_env_file=None)
    assert s.groq_api_key == ""
    assert s.database_url == "sqlite:///./tunatale.db"
    assert s.llm_mode == "mock"
    assert s.llm_model == "llama-3.3-70b-versatile"


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key-123")
    monkeypatch.setenv("LLM_MODE", "live")
    s = Settings()
    assert s.groq_api_key == "test-key-123"
    assert s.llm_mode == "live"
