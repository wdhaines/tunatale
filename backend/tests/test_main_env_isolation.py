"""Importing ``app.main`` must not copy ``.env`` into ``os.environ``.

``Settings`` reads ``.env`` itself and ignores case, so a key that ``.env``
spells in lowercase and a process variable spelled in uppercase are one field.
When ``app.main`` called ``load_dotenv()``, it put ``.env``'s lowercase key into
``os.environ`` beside the uppercase one, and the case-insensitive merge let the
FILE override the PROCESS — the reverse of the documented precedence.

Measured 2026-09-21 on the laptop live instance: ``switch.sh`` starts uvicorn
with ``LEMMATIZER_TYPE=table``, its ``backend/.env`` is the dev ``.env`` with
``lemmatizer_type=stanza``, so the server built Stanza, found it not installed
(that venv is torch-free by design), and lowercased every word — ``ble``,
``ringer`` shown as unknown and ``meg``/``deg`` as NEW.

Run in a subprocess because ``app.main`` is imported once per test worker and
``settings`` is a module singleton; the collision only happens at first import.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent

_PROBE = """
import json, os
import app.main  # noqa: F401 — the import is the thing under test
from app.config import settings
print(json.dumps({
    "setting": settings.lemmatizer_type,
    "env_keys": sorted(k for k in os.environ if k.lower() == "lemmatizer_type"),
}))
"""


def _run_probe(tmp_path: Path) -> dict:
    (tmp_path / ".env").write_text("lemmatizer_type=stanza\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k.lower() != "lemmatizer_type"}
    env["LEMMATIZER_TYPE"] = "table"
    env["PYTHONPATH"] = str(BACKEND)
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_process_env_beats_a_lowercase_dotenv_key_after_importing_main(tmp_path):
    result = _run_probe(tmp_path)
    assert result["setting"] == "table"


def test_importing_main_does_not_write_dotenv_into_os_environ(tmp_path):
    result = _run_probe(tmp_path)
    assert result["env_keys"] == ["LEMMATIZER_TYPE"]
