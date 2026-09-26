"""data-transfer.sh moves accounts and learner decks together (tunatale-98zf.4).

A learner's decks live at users/<id>/tunatale_<code>.db, named by the account id
in auth.db, so the two must move as one unit. The functions are extracted from
data-transfer.sh and run for real under the system bash (3.2 on macOS, which is
what the script runs on), as test_deploy_image_prune.py does for deploy.sh.

The ssh legs cannot run here. What they rely on is that the identity files are
ordinary SQLITE entries, so the existing snapshot, copy and row-count
verification cover them — which is what these tests pin.
"""

import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "data-transfer.sh"


def _functions() -> str:
    text = SCRIPT.read_text()
    parts = [line for line in text.splitlines() if line.startswith(("USER_DB_RE=", "field()"))]
    for name in ("local_user_dbs", "add_identity"):
        found = subprocess.run(
            ["sed", "-n", f"/^{name}()/,/^}}/p", str(SCRIPT)], capture_output=True, text=True, check=True
        ).stdout
        assert f"{name}()" in found, f"data-transfer.sh no longer defines {name}()"
        parts.append(found)
    assert any(p.startswith("USER_DB_RE=") for p in parts)
    return "\n".join(parts)


def _run(root: Path, body: str) -> subprocess.CompletedProcess:
    script = f'set -euo pipefail\ndie() {{ echo "data-transfer: $*" >&2; exit 1; }}\n{_functions()}\n{body}'
    return subprocess.run(
        ["/bin/bash", "-c", script],
        capture_output=True,
        text=True,
        env={"TT_LAPTOP_ROOT": str(root), "PATH": "/usr/bin:/bin"},
    )


def _deck(root: Path, rel: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")


def test_lists_every_learner_deck_and_nothing_else(tmp_path):
    for rel in [
        "users/2/tunatale_ceb.db",
        "users/10/tunatale_no.db",
        "users/2/tunatale_ceb.db-wal",
        "users/2/notes.txt",
    ]:
        _deck(tmp_path, rel)
    out = _run(tmp_path, "local_user_dbs").stdout.split()
    assert out == ["users/10/tunatale_no.db", "users/2/tunatale_ceb.db"]


def test_no_users_directory_lists_nothing(tmp_path):
    result = _run(tmp_path, "local_user_dbs")
    assert result.returncode == 0 and result.stdout == ""


def test_identity_becomes_ordinary_sqlite_entries(tmp_path):
    body = 'SQLITE=("tunatale_ceb.db|/x|tunatale_ceb.db")\nadd_identity "$(printf "users/2/tunatale_ceb.db\\n")"\nprintf "%s\\n" "${SQLITE[@]}"'
    result = _run(tmp_path, body)
    assert result.returncode == 0, result.stderr
    entries = [line for line in result.stdout.splitlines() if "|" in line]
    assert entries == [
        "tunatale_ceb.db|/x|tunatale_ceb.db",
        f"auth.db|{tmp_path}/auth.db|auth.db",
        f"users/2/tunatale_ceb.db|{tmp_path}/users/2/tunatale_ceb.db|users/2/tunatale_ceb.db",
    ]
    assert "auth.db + 1 learner deck(s)" in result.stdout


def test_accounts_move_even_with_no_learner_decks(tmp_path):
    """The pair is the unit: an empty users/ still carries auth.db."""
    result = _run(tmp_path, 'SQLITE=()\nadd_identity ""\nprintf "%s\\n" "${SQLITE[@]}"')
    assert f"auth.db|{tmp_path}/auth.db|auth.db" in result.stdout


@pytest.mark.parametrize(
    "bad", ["users/../tunatale_ceb.db", "users/abc/tunatale_ceb.db", "users/2/evil.db", "/etc/tunatale_x.db"]
)
def test_refuses_anything_that_is_not_a_learner_deck(tmp_path, bad):
    result = _run(tmp_path, f'SQLITE=()\nadd_identity "{bad}"')
    assert result.returncode == 1
    assert "refusing to move it" in result.stderr
