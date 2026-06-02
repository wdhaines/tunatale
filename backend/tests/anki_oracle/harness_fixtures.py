"""pytest fixtures and helpers for the Anki oracle test harness.

Usage in tests::

    @pytest.mark.oracle
    def test_something(anki_queue):
        assert len(anki_queue) > 0
        assert anki_queue[0]["card_id"] == 10010

The ``--run-oracle`` CLI flag must be passed to pytest (see ``conftest.py``),
otherwise all ``@pytest.mark.oracle`` tests are skipped.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from tests.anki_oracle.synthetic_collection import (
    BASIC_NOTETYPE_MID,
    SyntheticCollection,
)

ORACLE_SCRIPT = Path(__file__).with_name("oracle.py")

# `uv run --with anki` builds an ephemeral environment on first use. When many
# pytest-xdist workers spawn oracle subprocesses simultaneously, the losers of
# the env-build race can return empty/garbage stdout once before uv's cache is
# warm. Retry a few times so a transient race never turns into a silently
# skipped parity test (which would be lost coverage). A persistent failure
# (anki genuinely unavailable) still falls through to pytest.skip.
_ORACLE_MAX_ATTEMPTS = 4
_ORACLE_RETRY_SLEEP_S = 0.5


class OracleResult:
    """Structured access to oracle subprocess output."""

    def __init__(self, raw: dict[str, Any]) -> None:
        self._raw = raw

    def queue(self, op_index: int = 0) -> list[dict]:
        return self._key_for_op("get_queue", op_index).get("cards", [])

    def counts(self, op_index: int = 0) -> dict:
        return self._key_for_op("get_queue", op_index).get("counts", {})

    def card_ids(self, op_index: int = 0) -> list[int]:
        return [c["card_id"] for c in self.queue(op_index)]

    def raw(self) -> dict[str, Any]:
        return self._raw

    def _key_for_op(self, op_name: str, index: int) -> dict:
        candidates = [k for k in self._raw if k.startswith(f"{op_name}_")]
        return self._raw[candidates[index]]


def run_oracle(collection_path: Path, operations: list[dict]) -> OracleResult:
    """Run the oracle subprocess and return structured results.

    Calls ``pytest.skip()`` when the ``anki`` package is not available
    (determined by the oracle script's JSON error response or a subprocess
    failure). Transient invalid-JSON output (e.g. an env-build race across
    parallel workers) is retried before skipping.
    """
    last_error = ""
    for attempt in range(_ORACLE_MAX_ATTEMPTS):
        try:
            proc = subprocess.run(
                [
                    "uv",
                    "run",
                    "--with",
                    "anki",
                    "python",
                    str(ORACLE_SCRIPT),
                    str(collection_path),
                ],
                input=json.dumps(operations),
                capture_output=True,
                text=True,
                timeout=60,
                env={
                    **os.environ,
                    "QT_QPA_PLATFORM": "offscreen",
                },
            )
        except FileNotFoundError:
            pytest.skip("`uv` not found on PATH — cannot run oracle tests.")
        except subprocess.TimeoutExpired:
            pytest.skip("Oracle subprocess timed out after 60s.")

        try:
            result = json.loads(proc.stdout)
        except json.JSONDecodeError as e:
            # Empty/garbage stdout is a transient subprocess failure under
            # parallel load — retry before giving up.
            last_error = f"Oracle output not valid JSON: {e}\nstderr: {proc.stderr}"
            if attempt < _ORACLE_MAX_ATTEMPTS - 1:
                time.sleep(_ORACLE_RETRY_SLEEP_S)
                continue
            pytest.skip(last_error)

        if "error" in result:
            pytest.skip(f"Oracle not available: {result['error']}")

        return OracleResult(result)

    # Unreachable: the loop either returns, skips, or exhausts retries (which
    # also skips on the final attempt above).
    raise AssertionError("run_oracle retry loop exited without result")  # pragma: no cover


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def synthetic_collection(tmp_path: Path) -> SyntheticCollection:
    """Build an empty synthetic Anki collection with sane defaults.

    The collection has FSRS enabled, a "Default" deck (ID 1), and a Basic
    notetype (1 template, 2 fields).  Add notes / cards / revlogs in the
    test function, then call ``save()`` before passing the path to
    ``run_oracle()``.  The fixture auto-saves on teardown.
    """
    coll = SyntheticCollection(tmp_path / "collection.anki2")
    coll.set_deck("Default", 1)
    coll.enable_fsrs()
    coll.add_notetype(BASIC_NOTETYPE_MID, "Basic", ("Front", "Back"), template_count=1)
    coll.save()
    yield coll
    coll.save()


@pytest.fixture
def anki_queue(synthetic_collection: SyntheticCollection) -> list[dict]:
    """Return Anki's ground-truth queue for the synthetic collection."""
    synthetic_collection.save()
    return run_oracle(
        synthetic_collection.path,
        [{"op": "get_queue", "deck_id": 1, "fetch_limit": 50}],
    ).queue()
