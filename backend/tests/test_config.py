"""Tests for application configuration."""

from app.config import Settings


def test_settings_defaults():
    s = Settings()
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
