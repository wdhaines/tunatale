"""Fake driver speaking the persistent line protocol for orchestrator tests.

Reads one JSON command per line from stdin, writes one JSON result per line to
*real* stdout (the same trick as sync_driver.py — anki's noise goes to stderr).

Used by test_anki_sync_driver_loop.py and test_anki_sync_orchestrator.py to
exercise the persistent driver subprocess without needing anki.
"""

from __future__ import annotations

import json
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_REAL_STDOUT = sys.stdout
sys.stdout = sys.stderr

_stdout_fd = _REAL_STDOUT.fileno()

# Count of commands processed in this process lifetime (for PID-reuse assertions).
_op_count = 0


def _emit(result: dict) -> None:
    data = (json.dumps(result) + "\n").encode()
    os.write(_stdout_fd, data)


def main() -> None:
    global _op_count
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            command = json.loads(line)
        except json.JSONDecodeError as e:
            _emit({"error": f"bad json: {e}"})
            continue

        op = command.get("op", "")
        _op_count += 1

        if op == "shutdown":
            _emit({"ok": True})
            break
        elif op == "login":
            _emit({"hkey": "fake-hkey", "endpoint": command.get("endpoint", "")})
        elif op == "sync":
            _emit({"required": 1, "server_message": "OK"})
        elif op == "echo":
            _emit({"echo": command.get("payload"), "count": _op_count})
        elif op == "slow":
            time.sleep(command.get("delay_s", 30))
            _emit({"ok": True})
        elif op == "error":
            _emit({"error": command.get("msg", "deliberate error")})
        elif op == "die":
            # Crash mid-command: leave a clue on stderr, exit without responding.
            # Pins that _run_driver's failure message carries the driver's stderr.
            print(command.get("last_words", "fake driver dying"), file=sys.stderr)
            sys.stderr.flush()
            sys.exit(1)
        elif op == "stderr_flood":
            # Write many lines to stderr to test the drain thread.
            for i in range(command.get("lines", 500)):
                print(f"stderr line {i}", file=sys.stderr)
            _emit({"ok": True})
        elif op == "media_pending":
            _emit({"pending": command.get("count", 0)})
        elif op == "create_collection":
            _emit({"ok": True})
        elif op == "full_download":
            _emit({"ok": True, "required": 3})
        elif op == "full_upload":
            _emit({"ok": True})
        elif op == "get_card":
            _emit(
                {"card_id": command.get("card_id"), "queue": 0, "type": 0, "ivl": 0, "due": 0, "reps": 0, "lapses": 0}
            )
        else:
            _emit({"error": f"unknown op: {op}"})


if __name__ == "__main__":
    main()
