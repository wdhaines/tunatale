"""Health verdict for a running BP (opencode) delegation: HEALTHY / ALARM / WAITING.

    uv run python scripts/bp_watchdog.py <session_id> [--port N] [--ctx-limit N]
    uv run python scripts/bp_watchdog.py --self-test

Prints ONE verdict word plus counters, for polling from a shell loop:

    until [ "$(uv run python scripts/bp_watchdog.py "$SES" | cut -d' ' -f1)" != WAITING ]; do
      sleep 20
    done

WHY writes and not bytes. During a read-spiral the run's stdout keeps growing, so
an mtime/size watchdog reports "healthy" the whole way down. That happened on
2026-07-31: an openapi batch burned 119k of context across 35 tool calls without a
single edit, while its output file grew steadily and was reported as fine. A write
is the one signal a read-spiral cannot produce.

WHY the decision lives in Python. The first version returned three numbers and the
caller did ``set -- $R`` -- which does NOT word-split unquoted variables in zsh. Its
counters silently kept their defaults and every iteration printed a fake all-clear:
a verification that could not fail. Keep the branch here, emit one word.

WHY --self-test exists. Same incident. An alarm nobody has watched go off is not
known to be reachable, so the reachability check is executable rather than a
comment. Run it after ANY edit to ``verdict``.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

# 110k, not 85k: a legitimate run reaches 85k naturally -- one batch's first write
# landed at 84.8k, a single poll away from a false alarm. The failure this exists
# to catch sat at 119k with zero writes.
DEFAULT_CTX_LIMIT = 110_000
DEFAULT_PORT = 4096

WRITE_TOOLS = frozenset({"edit", "write", "patch"})


def verdict(writes: int, ctx: int, ctx_limit: int = DEFAULT_CTX_LIMIT) -> str:
    """HEALTHY once anything has been written; ALARM only for a context-heavy read-spiral."""
    if writes > 0:
        return "HEALTHY"
    return "ALARM" if ctx > ctx_limit else "WAITING"


def scan(messages: list[dict]) -> tuple[int, int, int]:
    """Return (tool_calls, write_calls, peak_context) for an opencode message list."""
    tools = writes = 0
    ctx = 0
    for message in messages:
        for part in message.get("parts") or []:
            if part.get("type") != "tool":
                continue
            tools += 1
            if (part.get("tool") or "") in WRITE_TOOLS:
                writes += 1
        tokens = (message.get("info") or {}).get("tokens") or {}
        if tokens:
            cache_read = (tokens.get("cache") or {}).get("read", 0)
            ctx = max(ctx, tokens.get("input", 0) + cache_read)
    return tools, writes, ctx


def fetch(session: str, port: int) -> list[dict]:
    url = f"http://127.0.0.1:{port}/session/{session}/message"
    with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310 - fixed localhost URL
        return json.load(response)


def self_test() -> int:
    """Prove the ALARM branch is reachable. An 85k default made it effectively dead."""
    cases = [
        (0, 119_335, "ALARM"),  # the real read-spiral failure
        (0, 84_775, "WAITING"),  # a healthy run's first write -- must NOT alarm
        (0, 30_000, "WAITING"),
        (3, 119_335, "HEALTHY"),  # writes always win
    ]
    failures = 0
    for writes, ctx, expected in cases:
        got = verdict(writes, ctx)
        ok = got == expected
        failures += not ok
        print(f"  writes={writes:<2} ctx={ctx:<7} -> {got:8} expected {expected:8} {'ok' if ok else 'BROKEN'}")
    print("self-test PASSED" if not failures else f"self-test FAILED ({failures})")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("session", nargs="?", help="opencode session id (see `opencode session list`)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--ctx-limit", type=int, default=DEFAULT_CTX_LIMIT)
    parser.add_argument("--self-test", action="store_true", help="check the ALARM branch is reachable")
    args = parser.parse_args()

    if args.self_test:
        return self_test()
    if not args.session:
        parser.error("session id required (or pass --self-test)")

    try:
        messages = fetch(args.session, args.port)
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        # No server yet, or the session does not exist -- not a failure, just not ready.
        print(f"WAITING probe-failed {type(exc).__name__}")
        return 0

    tools, writes, ctx = scan(messages)
    print(f"{verdict(writes, ctx, args.ctx_limit)} tools={tools} writes={writes} ctx={ctx}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
