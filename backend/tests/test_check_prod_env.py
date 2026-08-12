"""Validate a production env file BEFORE it is deployed.

The startup guard in ``app.main`` refuses to boot a misconfigured prod process,
which is the backstop. This checker is the same rules applied to an env file on
disk, so ``.env.prod.example`` cannot rot into a template that produces a box
which won't start.

The isolation matters more than it looks: a plain ``Settings(_env_file=path)``
still reads ``os.environ``, and environ WINS over the file. On a dev machine
with ``LLM_MODE=mock`` exported, that would fail a perfectly good prod file; on
one with ``LLM_MODE=live``, it would pass a file that never set it. The checker
evaluates the file alone, and ``test_env_is_evaluated_in_isolation`` pins both
directions.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from check_prod_env import check_env_file, do_check  # noqa: E402

GOOD = """
TT_ENV=prod
LLM_MODE=live
AUTH_ENABLED=true
SESSION_SECRET=not-a-real-secret
CORS_ORIGINS=["https://tunatale.example.com"]
"""


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / ".env.prod"
    path.write_text(body, encoding="utf-8")
    return path


def test_a_complete_prod_file_passes(tmp_path):
    exit_code, messages = check_env_file(_write(tmp_path, GOOD))
    assert (exit_code, messages) == (0, [])


def test_a_file_that_forgets_live_mode_fails(tmp_path):
    """The silent one: LLM_MODE unset means cassette replies from a healthy box."""
    exit_code, messages = check_env_file(_write(tmp_path, GOOD.replace("LLM_MODE=live", "")))
    assert exit_code == 1
    assert any("llm_mode" in m for m in messages), messages


def test_a_file_that_is_not_a_prod_profile_fails_loudly(tmp_path):
    """A file with no TT_ENV=prod must NOT pass vacuously.

    Every content rule keys off the prod profile, so a missing ``TT_ENV`` would
    otherwise produce an empty problem list — a clean negative indistinguishable
    from a valid file.
    """
    exit_code, messages = check_env_file(_write(tmp_path, GOOD.replace("TT_ENV=prod", "TT_ENV=dev")))
    assert exit_code == 1
    assert any("tt_env" in m for m in messages), messages


def test_wildcard_cors_is_refused(tmp_path):
    exit_code, messages = check_env_file(_write(tmp_path, GOOD.replace('["https://tunatale.example.com"]', '["*"]')))
    assert exit_code == 1
    assert any("cors_origins" in m for m in messages), messages


def test_env_is_evaluated_in_isolation(tmp_path, monkeypatch):
    """Neither direction of environ leakage may change the verdict."""
    monkeypatch.setenv("LLM_MODE", "mock")
    monkeypatch.setenv("TT_ENV", "")
    assert check_env_file(_write(tmp_path, GOOD)) == (0, [])

    monkeypatch.setenv("LLM_MODE", "live")
    monkeypatch.setenv("SESSION_SECRET", "leaked-from-environ")
    bad = GOOD.replace("LLM_MODE=live", "").replace("SESSION_SECRET=not-a-real-secret", "")
    exit_code, messages = check_env_file(_write(tmp_path, bad))
    assert exit_code == 1
    assert any("llm_mode" in m for m in messages), messages
    assert any("session_secret" in m for m in messages), messages


def test_environ_is_restored_afterwards(tmp_path, monkeypatch):
    """The isolation must not be a one-way trip — the process keeps its environ."""
    import os

    monkeypatch.setenv("LLM_MODE", "mock")
    check_env_file(_write(tmp_path, GOOD))
    assert os.environ["LLM_MODE"] == "mock"


def test_missing_file_is_an_error_not_a_pass(tmp_path):
    exit_code, messages = check_env_file(tmp_path / "nope.env")
    assert exit_code == 1
    assert any("not found" in m for m in messages), messages


def test_do_check_defaults_to_the_repo_prod_template():
    """``./test.sh`` runs this with no argument, so the default must be real."""
    assert do_check([]) == 0


def test_do_check_reports_and_returns_nonzero(tmp_path, capsys):
    do_check([str(_write(tmp_path, GOOD.replace("LLM_MODE=live", "")))])
    assert "llm_mode" in capsys.readouterr().out


def test_the_shipped_prod_template_is_valid():
    """The committed ``.env.prod.example`` is what a deploy copies."""
    template = Path(__file__).resolve().parents[1] / ".env.prod.example"
    assert template.exists(), "backend/.env.prod.example is missing"
    assert check_env_file(template) == (0, [])
