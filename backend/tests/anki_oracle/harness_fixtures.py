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

from app.plugins.anki_sync.sync_orchestrator import _anki_with_spec
from tests.anki_oracle.synthetic_collection import (
    BASIC_NOTETYPE_MID,
    SyntheticCollection,
)

ORACLE_SCRIPT = Path(__file__).with_name("oracle.py")

# `uv run --isolated --with anki` builds an ephemeral environment on first use.
# When many pytest-xdist workers spawn oracle subprocesses simultaneously, the
# losers of the env-build race can return empty/garbage stdout once before uv's
# cache is warm. Retry a few times so a transient race never turns into a flake.
# A *persistent* failure (anki genuinely unavailable) is a hard pytest.fail, NOT
# a skip: under --run-oracle the caller intends to run the parity gate, and a skip
# looks like a pass — which is exactly how this harness silently went dark from
# the 2026-06-02 Python 3.14 bump (stale protobuf 4.21.2) until it was caught.
# To intentionally run without the gate, omit --run-oracle.
_ORACLE_MAX_ATTEMPTS = 4
_ORACLE_RETRY_SLEEP_S = 0.5


def _oracle_unavailable(last_error: str) -> None:
    """Fail (never skip) when the oracle can't run under --run-oracle."""
    pytest.fail(
        f"--run-oracle was passed but the oracle harness did not produce a usable "
        f"result after {_ORACLE_MAX_ATTEMPTS} attempts — failing instead of skipping "
        f"so a broken anki subprocess can't masquerade as a pass.\n"
        f"Last error: {last_error}\n"
        f"To run the suite without the parity gate, omit --run-oracle "
        f"(e.g. `cd backend && uv run pytest`)."
    )


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

    Transient invalid-JSON output (e.g. an env-build race across parallel
    workers) is retried. A *persistent* failure — anki unavailable, uv missing,
    timeout, or an error result — calls ``pytest.fail()``, NOT ``pytest.skip()``:
    under --run-oracle the caller intends to run the parity gate, so an
    unrunnable oracle is a failure, never a silent skip.
    """
    last_error = ""
    for attempt in range(_ORACLE_MAX_ATTEMPTS):
        try:
            proc = subprocess.run(
                [
                    "uv",
                    "run",
                    # Isolated + project-free so we escape the project lock's stale
                    # protobuf 4.21.2 (no cp314 wheel; dragged in by the classla+anki
                    # extras), which makes `anki` unimportable under the project's 3.14
                    # interpreter. A clean resolve pulls a working protobuf. oracle.py
                    # is stdlib + anki only, so it runs fine without the project env.
                    "--isolated",
                    "--no-project",
                    "--python",
                    "3.14",
                    # Single-sourced from settings.anki_pkg_version (via _anki_with_spec,
                    # same as the peer-sync server) so the oracle validates parity against
                    # the exact Anki version we sync with — currently anki==25.9.5.
                    "--with",
                    _anki_with_spec(),
                    "python",
                    str(ORACLE_SCRIPT),
                    str(collection_path),
                ],
                input=json.dumps(operations),
                capture_output=True,
                text=True,
                # Generous: the first isolated `uv run --with anki` may build the
                # ephemeral env from a cold cache; retries cover a mid-build timeout.
                timeout=180,
                env={
                    **os.environ,
                    "QT_QPA_PLATFORM": "offscreen",
                },
            )
        except FileNotFoundError:
            pytest.fail("--run-oracle was passed but `uv` is not on PATH — cannot run the oracle harness.")
        except subprocess.TimeoutExpired:
            last_error = "oracle subprocess timed out (env build or op too slow)"
            if attempt < _ORACLE_MAX_ATTEMPTS - 1:
                time.sleep(_ORACLE_RETRY_SLEEP_S)
                continue
            _oracle_unavailable(last_error)

        try:
            result = json.loads(proc.stdout)
        except json.JSONDecodeError as e:
            # Empty/garbage stdout is a transient subprocess failure under
            # parallel load — retry before giving up.
            last_error = f"Oracle output not valid JSON: {e}\nstderr: {proc.stderr}"
            if attempt < _ORACLE_MAX_ATTEMPTS - 1:
                time.sleep(_ORACLE_RETRY_SLEEP_S)
                continue
            _oracle_unavailable(last_error)

        if "error" in result:
            _oracle_unavailable(f"oracle returned an error: {result['error']}")

        return OracleResult(result)

    # Unreachable: the loop either returns or fails (the final attempt calls
    # _oracle_unavailable, which raises).
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
