"""Anki sync driver subprocess.

Invoked via::

    uv run --with anki python -m app.anki.sync_driver

Reads one JSON command from stdin, writes one JSON result to stdout.
This is the ONLY module in ``app/`` that imports ``anki``.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# anki and its backend emit diagnostics to stdout (e.g. a "blocked main thread"
# watchdog stack trace during a network sync). That corrupts our single-JSON-line
# stdout contract, so reserve the real stdout for the result and route everything
# else — including anything printed at anki import time — to stderr.
_REAL_STDOUT = sys.stdout
sys.stdout = sys.stderr

_IMPORT_ERROR: str | None = None
try:
    from anki.collection import Collection
except ImportError as e:
    Collection = None  # type: ignore[assignment,misc]
    _IMPORT_ERROR = str(e)


def _emit(result: dict) -> None:
    """Write the one-and-only JSON result line to the real stdout."""
    print(json.dumps(result), file=_REAL_STDOUT)


def _serialize_sync_auth(auth: Any) -> dict:
    return {"hkey": auth.hkey, "endpoint": auth.endpoint}


def _serialize_sync_output(output: Any) -> dict:
    return {
        "required": output.required,
        "server_message": output.server_message,
    }


def _make_auth(auth_dict: dict) -> Any:
    """Build a SyncAuth protobuf from a dict."""
    from anki.sync_pb2 import SyncAuth

    return SyncAuth(hkey=auth_dict["hkey"], endpoint=auth_dict.get("endpoint", ""))


def _op_login(op: dict) -> dict:
    username = op["username"]
    password = op["password"]
    endpoint = op.get("endpoint", "")
    with tempfile.TemporaryDirectory() as td:
        col_path = str(Path(td) / "tmp_col.anki2")
        col = Collection(col_path)
        try:
            auth = col.sync_login(username, password, endpoint=endpoint or None)
            return _serialize_sync_auth(auth)
        finally:
            col.close()


def _op_sync(op: dict) -> dict:
    collection_path = op["collection_path"]
    auth = _make_auth(op["auth"])
    col = Collection(str(collection_path))
    try:
        output = col.sync_collection(auth, sync_media=False)
        return _serialize_sync_output(output)
    finally:
        col.close()


def _op_full_download(op: dict) -> dict:
    collection_path = op["collection_path"]
    auth = _make_auth(op["auth"])
    col = Collection(str(collection_path))
    try:
        col.full_upload_or_download(auth=auth, server_usn=None, upload=False)
        return {"ok": True}
    finally:
        col.close()


def _op_create_collection(op: dict) -> dict:
    """Create a minimal empty collection at the given path."""
    collection_path = Path(op["collection_path"])
    collection_path.parent.mkdir(parents=True, exist_ok=True)
    col = Collection(str(collection_path))
    try:
        col.close()
        return {"ok": True}
    except Exception as e:
        return {"error": str(e)}


_OPERATIONS: dict[str, Any] = {
    "login": _op_login,
    "sync": _op_sync,
    "create_collection": _op_create_collection,
    "full_download": _op_full_download,
}


def main() -> None:
    if _IMPORT_ERROR:
        _emit({"error": f"anki not available: {_IMPORT_ERROR}"})
        return

    try:
        command = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, OSError) as e:
        _emit({"error": f"Failed to read command: {e}"})
        return

    op_name = command.get("op", "")
    handler = _OPERATIONS.get(op_name)
    if handler is None:
        _emit({"error": f"Unknown operation: {op_name}"})
        return

    try:
        result = handler(command)
        _emit(result)
    except Exception as e:
        import traceback

        _emit({"error": str(e), "traceback": traceback.format_exc()})


if __name__ == "__main__":
    main()
