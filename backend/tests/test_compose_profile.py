"""The compose file's deployment-shaped properties, pinned.

`docker-compose.yml` is not exercised by any gate: `./test.sh` never invokes
Docker and CI builds the image elsewhere (`tunatale-pse`). So the properties
that make the deployed box survive on its own have no guard at all unless they
are asserted as text — which is what this module does, and all it does. It
makes no claim that the stack RUNS; only the box can say that.

The restart policy is here because its absence is invisible until the worst
moment: the host installs security updates unattended and reboots for kernel
ones (`docs/deployment.md` § Provisioning, step 5), and a container with no
policy simply never comes back. Docker's side of that was verified on the real
box — a probe container with `unless-stopped` survived a reboot — so what is
left to protect is this file.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

COMPOSE = Path(__file__).resolve().parents[2] / "docker-compose.yml"


@pytest.fixture(scope="module")
def services() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]


@pytest.mark.parametrize("name", ["api", "web"])
def test_long_running_services_restart_themselves(services, name):
    """Without this the first unattended kernel reboot ends with the app down."""
    assert services[name].get("restart") == "unless-stopped", (
        f"{name} has no restart policy: it would not come back after a reboot"
    )


def test_the_one_shot_init_job_does_not_restart(services):
    """`init` prepares the volume and exits; a restart policy would loop it.

    The control for the test above — which a blanket "every service restarts"
    rule would pass while breaking the stack.
    """
    assert "restart" not in services["init"]
    assert services["api"]["depends_on"]["init"]["condition"] == "service_completed_successfully"


def test_every_mutable_path_lands_on_the_one_volume(services):
    """HOME=/data is what relocates the ~/.tunatale paths; the rest are explicit."""
    env = services["api"]["environment"]
    assert env["HOME"] == "/data"
    assert env["MEDIA_DIR"].startswith("/data/")
    assert env["AUDIO_DIR"].startswith("/data/")
    assert all(url.startswith("sqlite:////data/") for url in yaml.safe_load(env["DATABASE_URLS"]).values())
