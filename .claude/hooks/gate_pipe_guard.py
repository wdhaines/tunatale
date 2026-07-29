#!/usr/bin/env python3
"""Refuse to run ``./test.sh`` through a pipe (PreToolUse/Bash, settings.json).

Why this is a hook and not a note in AGENTS.md
----------------------------------------------
``./test.sh 2>&1 | tail -40`` is wrong twice over, and both are properties of
the shell rather than of the person typing:

1. A pipeline's exit status is the LAST command's. ``tail`` always succeeds, so
   ``$?`` reads 0 while the gate printed ``=== FAILED ===``. Nothing about the
   command looks wrong; the status is simply answering a different question.
2. ``tail -n`` / ``head -n`` discard the failure detail. The one thing you
   needed from a failed run is the part the pipe threw away.

Both were hit on 2026-07-29 by an agent that had, two rounds earlier, warned
another agent about exactly this. Remembering is not a mechanism.

The correct form redirects instead, keeping the whole log and the real status:

    ./test.sh > /tmp/gate.txt 2>&1; echo EXIT=$?

Scope: only commands that INVOKE test.sh. Searching for the string
(``grep test.sh …``, ``cat test.sh | head``) is untouched — the guard looks for
test.sh in command position, after a shell separator.
"""

import json
import re
import sys

# test.sh in COMMAND position: at the start, or after a separator (; & | && ||
# newline), optionally via an interpreter (bash/sh) and with any path prefix
# ("./", "$ROOT/", an absolute path). `cat test.sh` does not match: `cat` is the
# command and test.sh is its argument.
_INVOCATION = re.compile(
    r"""(?:^|[;&|\n]|&&|\|\|)\s*      # start of a command
        (?:(?:bash|sh|zsh)\s+)?       # optional interpreter
        (?:[\w./$@{}-]*/)?            # optional path prefix
        test\.sh\b""",
    re.VERBOSE,
)

FIX = "./test.sh > /tmp/gate.txt 2>&1; echo EXIT=$?"

REASON = (
    "Refusing to pipe ./test.sh.\n"
    "\n"
    "A pipeline's exit status is the LAST command's, so `$?` reports the pager's "
    "success, not the gate's — a failed run reads as 0. And `tail`/`head` discard "
    "the failure detail you piped in order to read.\n"
    "\n"
    f"Use:  {FIX}\n"
    "\n"
    "Then read the file. On failure test.sh also writes the full log to "
    ".git/tt-test-last.log and names it in the FAILED banner."
)


def _strip_quoted(command: str) -> str:
    """Blank out quoted spans so a `|` inside a string is not read as a pipe."""
    out = []
    quote = None
    for ch in command:
        if quote:
            out.append(" " if ch != quote else ch)
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            out.append(ch)
        else:
            out.append(ch)
    return "".join(out)


def pipes_the_gate(command: str) -> bool:
    """Whether *command* invokes test.sh and sends its output into a pipe."""
    bare = _strip_quoted(command)
    match = _INVOCATION.search(bare)
    if match is None:
        return False
    # A pipe belonging to THIS invocation: after it, before the next separator
    # that ends the command (`;`, `&&`, `||`, newline). `||` is not a pipe.
    rest = bare[match.end() :]
    rest = re.split(r";|&&|\|\||\n", rest)[0]
    return "|" in rest


def main():
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError, ValueError):
        return 0
    command = (data.get("tool_input") or {}).get("command", "")
    if not pipes_the_gate(command):
        return 0

    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": REASON,
                }
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
