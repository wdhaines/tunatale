"""Pytest configuration for TunaTale test suite."""

import os
from pathlib import Path

import pytest

_CASSETTES_DIR = Path(__file__).parent / "cassettes"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--llm-mode",
        choices=["mock", "live", "record", "patch"],
        default="mock",
        help="LLM mode for cassette fixtures: mock (replay), live, record, or patch.",
    )


@pytest.fixture
def llm_mode(request: pytest.FixtureRequest) -> str:
    return request.config.getoption("--llm-mode")  # type: ignore[return-value]


@pytest.fixture
async def cassette_llm(request: pytest.FixtureRequest, llm_mode: str):
    """Yield a CassetteLLMClient configured for the current --llm-mode."""
    from app.llm.cassette import CassetteLLMClient

    cls_name = request.node.cls.__name__ if request.node.cls else "_noclass"
    test_name = request.node.name
    cassette_path = _CASSETTES_DIR / f"{cls_name}__{test_name}.json"

    if llm_mode == "mock":
        if not cassette_path.exists():
            pytest.skip(f"No cassette at {cassette_path} — run with --llm-mode=record first.")
        client = CassetteLLMClient(mode="mock", cassette_path=cassette_path)
        yield client
        return

    if llm_mode == "patch" and not cassette_path.exists():
        pytest.skip(f"No cassette at {cassette_path} — run with --llm-mode=record first.")

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        pytest.skip("GROQ_API_KEY not set — cannot run in live/record/patch mode.")

    from app.llm.client import LLMClient

    real_client = LLMClient(groq_api_key=api_key)
    client = CassetteLLMClient(mode=llm_mode, cassette_path=cassette_path, real_client=real_client)
    yield client
    client.save()
