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

import json
from pathlib import Path

import pytest
import yaml

from app.languages import _CONFIGS, discover

COMPOSE = Path(__file__).resolve().parents[2] / "docker-compose.yml"
PROD_ENV = Path(__file__).resolve().parents[1] / ".env.prod.example"


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


@pytest.mark.parametrize("name", ["init", "api", "web"])
def test_every_service_pulls_a_pinned_tag(services, name):
    """The box runs a tagged image, never `git pull` of main.

    `:?` and not `:-latest`: a default would let a mistyped deploy ship
    "whatever latest is", which is precisely the question a tagged deploy
    exists to answer. Compose refuses instead.
    """
    image = services[name]["image"]
    assert image.startswith("ghcr.io/"), image
    assert "${TT_TAG:?" in image, f"{name} must fail closed when TT_TAG is unset, got {image}"
    assert ":-" not in image, f"{name} has a default tag: {image}"


def test_the_health_start_period_covers_a_cold_reboot(services):
    """A too-short window makes a working box report `unhealthy` after a reboot.

    Measured on the e2-micro 2026-09-11: ~18s to bind from a warm deploy, ~70s
    after a full VM reboot. Failures during `start_period` are free; after it,
    three at `interval` apart flip the container. At 15s the box served HTTP 200
    while reporting unhealthy — and that is the signal deploy.sh and any uptime
    monitor act on.
    """
    health = services["api"]["healthcheck"]
    assert health["start_period"].endswith("s")
    assert int(health["start_period"].removesuffix("s")) >= 90, health["start_period"]


def test_init_and_api_are_the_same_image(services):
    """init IS the api image with another entrypoint — they cannot diverge."""
    assert services["init"]["image"] == services["api"]["image"]


def test_every_mutable_path_lands_on_the_one_volume(services):
    """HOME=/data is what relocates the ~/.tunatale paths; the rest are explicit."""
    env = services["api"]["environment"]
    assert env["HOME"] == "/data"
    assert env["MEDIA_DIR"].startswith("/data/")
    assert env["AUDIO_DIR"].startswith("/data/")
    assert all(url.startswith("sqlite:////data/") for url in yaml.safe_load(env["DATABASE_URLS"]).values())


# ── TLS (tunatale-1oq) ──────────────────────────────────────────────────────


def test_web_publishes_https_on_tcp_and_udp(services):
    """443/tcp for HTTPS and 443/udp for HTTP/3; 80 stays for the ACME HTTP
    challenge and the redirect Caddy serves there."""
    ports = {str(p) for p in services["web"]["ports"]}
    assert {"80:80", "443:443", "443:443/udp"} <= ports, ports


def test_web_keeps_its_certificates_across_redeploys(services):
    """Caddy stores issued certificates under /data and its autosaved config under
    /config. Without named volumes every deploy recreates the container empty and
    re-requests a certificate, and Let's Encrypt's duplicate-certificate limit
    (5 per week) turns a busy deploy day into a site with no TLS at all."""
    mounts = {m.split(":")[1]: m.split(":")[0] for m in services["web"]["volumes"]}
    assert set(mounts) >= {"/data", "/config"}, mounts
    top = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["volumes"]
    for target in ("/data", "/config"):
        assert mounts[target] in top, f"{mounts[target]} is not a declared named volume"
    # The API's `data` volume is a DIFFERENT volume: sharing it would put the
    # certificates beside the SQLite files and inside the app's backups.
    assert mounts["/data"] != "data"


def test_the_site_address_comes_from_an_optional_box_only_file(services):
    """The domain is a property of the box, not of the repo. deploy.sh rewrites
    `.env` on every deploy, so it cannot live there, and a laptop has no such file
    — `required: false` keeps `docker compose up` working locally, where the
    Caddyfile's `:80` default applies."""
    env_files = services["web"]["env_file"]
    assert {"path": "web.env", "required": False} in env_files, env_files
    assert "itcanbeeasilydone" not in COMPOSE.read_text(encoding="utf-8")


def test_every_database_the_api_opens_is_on_the_data_volume(services):
    """The accounts DB defaulted to ./auth.db, which resolves inside the
    container (/app) — so on 2026-09-18 the first redeploy after creating an
    account deleted it, and login answered "invalid credentials". Every SQLite
    file must be under the `data` volume's mount point."""
    env = services["api"]["environment"]
    urls = [env["DATABASE_URL"], env["AUTH_DATABASE_URL"], *yaml.safe_load(env["DATABASE_URLS"]).values()]
    for url in urls:
        assert url.startswith("sqlite:////data/"), url


def test_every_language_with_a_deck_has_a_prod_database(services):
    """DATABASE_URLS lists exactly the languages that have an Anki deck.

    A language registered in the registry but absent from DATABASE_URLS is
    silently outside the deployed box's backups and migrations, and the two
    files checked here are where the api service reads the mapping from.
    Deriving the expected set from the registry keeps this test from going
    stale when a fourth language lands.
    """
    discover()
    with_deck = {code for code, cfg in _CONFIGS.items() if cfg.deck_name}

    compose_keys = set(yaml.safe_load(services["api"]["environment"]["DATABASE_URLS"]))
    assert compose_keys == with_deck

    prod_line = next(
        line for line in PROD_ENV.read_text(encoding="utf-8").splitlines() if line.startswith("DATABASE_URLS=")
    )
    prod_keys = set(json.loads(prod_line.split("=", 1)[1]))
    assert prod_keys == with_deck
