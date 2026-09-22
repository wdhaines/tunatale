"""deploy.sh prunes superseded TunaTale images (tunatale-al6).

Measured 2026-09-22: 19 images on the box, ~1 GB reclaimable, growing with every
deploy — the only unbounded disk growth that was not user data. Keep the tag just
deployed and the last healthy one (the rollback target); a rollback to anything
older re-pulls from ghcr, so nothing irrecoverable is removed.

The filter is a shell function extracted from deploy.sh and run for real, so the
test exercises the script's own text rather than a Python re-implementation.
"""

import subprocess
from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[2] / "deploy.sh"

NEW = "9" * 40
PREV = "8" * 40
OLD = "7" * 40

LISTING = "\n".join(
    [
        f"ghcr.io/wdhaines/tunatale-api:{NEW}",
        f"ghcr.io/wdhaines/tunatale-web:{NEW}",
        f"ghcr.io/wdhaines/tunatale-api:{PREV}",
        f"ghcr.io/wdhaines/tunatale-web:{PREV}",
        f"ghcr.io/wdhaines/tunatale-api:{OLD}",
        f"ghcr.io/wdhaines/tunatale-web:{OLD}",
        "caddy:2-alpine",
        "ghcr.io/someoneelse/tunatale-api:" + OLD,
    ]
)


def stale(tag: str, previous: str) -> list[str]:
    fn = subprocess.run(
        ["sed", "-n", "/^stale_images()/,/^}/p", str(DEPLOY)], capture_output=True, text=True, check=True
    ).stdout
    assert "stale_images()" in fn, "deploy.sh no longer defines stale_images()"
    out = subprocess.run(
        ["bash", "-c", f'{fn}\nstale_images wdhaines "$1" "$2"', "_", tag, previous],
        input=LISTING,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return out.split()


def test_keeps_the_new_and_the_rollback_tag_and_prunes_the_rest():
    assert stale(NEW, PREV) == [f"ghcr.io/wdhaines/tunatale-api:{OLD}", f"ghcr.io/wdhaines/tunatale-web:{OLD}"]


def test_never_touches_other_repositories():
    got = stale(NEW, PREV)
    assert not any(img.startswith(("caddy", "ghcr.io/someoneelse")) for img in got), got


def test_first_deploy_with_no_previous_keeps_only_the_new_tag():
    got = stale(NEW, "")
    assert f"ghcr.io/wdhaines/tunatale-api:{NEW}" not in got
    assert f"ghcr.io/wdhaines/tunatale-api:{PREV}" in got
    assert len(got) == 4
