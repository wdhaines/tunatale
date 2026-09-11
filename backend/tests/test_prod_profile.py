"""CORS lockdown + the ``TT_ENV=prod`` startup guard.

Two separate problems, closed together because they share the settings surface.

**CORS is a live hole on the current Tailscale setup, not a pre-production
one.** The app shipped ``allow_origins=["*"]`` with ``allow_methods=["*"]`` and
``allow_credentials=True`` on an application with **no authentication at all**.
Any page loaded in a browser that can reach the dev server — ``localhost``, or
the MagicDNS hostname from a tailnet device — could read and write TunaTale data
cross-origin. No cookie was needed precisely because there is no auth to require
one.

**The prod guard** closes a different silent failure: ``llm_mode`` defaults to
``mock``, so a deployment that forgets ``LLM_MODE=live`` serves cassette replies
and looks perfectly healthy.

⚠️ **A green test here is NOT proof the hole is closed.** Starlette's
``CORSMiddleware`` decides what headers to *grant*; the browser is what refuses
to hand the response to the calling page. httpx/ASGITransport enforces nothing,
so these tests assert the grant — the presence or absence of
``access-control-allow-origin`` — and the browser half is verified by hand (see
the commit message). Asserting a 200 body here would prove nothing at all, which
is the trap this docstring exists to flag.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo, available_timezones

import httpx
import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import Settings, clock_runtime_problems, prod_profile_problems

# ── Settings surface ─────────────────────────────────────────────────────────


def _clean_settings(monkeypatch, tmp_path, **overrides) -> Settings:
    """A Settings built from defaults only — no ``.env``, no inherited environ.

    The dev ``.env`` sets TARGET_LANGUAGE/DATABASE_URLS and a real environ may
    carry LLM_MODE, so a naive ``Settings()`` here would go green or red
    depending on whose machine ran it.

    ``TZ`` is in the list for a sharper reason than the others: CI's
    ``backend-hostile-tz`` and ``backend-hostile-hour`` jobs EXPORT it, so
    without this the tz assertions below would read the runner's zone and mean
    something different in every job.
    """
    for var in (
        "TT_ENV",
        "CORS_ORIGINS",
        "CORS_ALLOW_ORIGIN_REGEX",
        "AUTH_ENABLED",
        "SESSION_SECRET",
        "LLM_MODE",
        "TZ",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)
    return Settings(_env_file=None, **overrides)


def _zone_agreeing_with_local(now: datetime | None = None) -> str:
    """Any IANA zone whose offset right now equals this process's own.

    Named rather than hardcoded because the zone differs per machine and per CI
    job; the local zone itself is always in tzdata, so a match always exists.

    ⚠️ **Call this in the same TZ environment the assertion runs in.** POSIX
    ``localtime`` re-reads ``TZ`` on every call, so deleting ``TZ`` *after*
    picking a zone changes the answer underneath you. That is not theoretical:
    ``./test.sh`` pins ``TZ=UTC``, so calling this before ``_clean_settings``
    yields a UTC-matching zone which then disagrees with the Mac's real zone —
    a green-looking helper producing a red test for the wrong reason.
    """
    now = now or datetime.now()
    local = now.astimezone().utcoffset()
    return next(z for z in sorted(available_timezones()) if now.replace(tzinfo=ZoneInfo(z)).utcoffset() == local)


def _zone_disagreeing_with_local(now: datetime | None = None) -> str:
    """A zone this process is definitely NOT in. The pair is 14 hours apart."""
    now = now or datetime.now()
    local = now.astimezone().utcoffset()
    return next(z for z in ("Etc/GMT+5", "Etc/GMT-9") if now.replace(tzinfo=ZoneInfo(z)).utcoffset() != local)


def test_cors_origins_default_is_not_a_wildcard(monkeypatch, tmp_path):
    """The default must never be ``*`` — that IS the hole this bead closes."""
    s = _clean_settings(monkeypatch, tmp_path)
    assert "*" not in s.cors_origins
    assert s.cors_origins == ["http://localhost:5173", "https://localhost:5173"]


def test_deployment_profile_defaults(monkeypatch, tmp_path):
    """``tt_env`` empty (dev), auth off, no session secret, no origin regex."""
    s = _clean_settings(monkeypatch, tmp_path)
    assert s.tt_env == ""
    assert s.auth_enabled is False
    assert s.session_secret == ""
    assert s.cors_allow_origin_regex == ""


def test_cors_origins_from_env_is_json(monkeypatch, tmp_path):
    """pydantic-settings parses a ``list[str]`` field from JSON, not CSV."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CORS_ORIGINS", '["https://tunatale.example.com"]')
    s = Settings(_env_file=None)
    assert s.cors_origins == ["https://tunatale.example.com"]


# ── prod_profile_problems ────────────────────────────────────────────────────


def test_prod_profile_clean_when_fully_configured(monkeypatch, tmp_path):
    s = _clean_settings(
        monkeypatch,
        tmp_path,
        tt_env="prod",
        llm_mode="live",
        auth_enabled=True,
        session_secret="a-real-secret",
        cors_origins=["https://tunatale.example.com"],
        trusted_proxy_header="X-Forwarded-For",
        tz="America/New_York",
    )
    assert prod_profile_problems(s) == []


@pytest.mark.parametrize(
    "overrides,fragment",
    [
        ({"llm_mode": "mock"}, "llm_mode"),
        ({"llm_mode": "patch"}, "llm_mode"),
        ({"auth_enabled": False}, "auth_enabled"),
        ({"session_secret": ""}, "session_secret"),
        ({"cors_origins": ["*"]}, "cors_origins"),
        ({"cors_allow_origin_regex": ".*"}, "cors_allow_origin_regex"),
        ({"cors_origins": []}, "cors_origins"),
        ({"trusted_proxy_header": ""}, "trusted_proxy_header"),
        ({"tz": ""}, "tz"),
        ({"tz": "Mars/Olympus_Mons"}, "tz"),
    ],
)
def test_prod_profile_flags_each_misconfiguration(monkeypatch, tmp_path, overrides, fragment):
    """Each condition is reported on its own, naming the setting to fix."""
    base = {
        "tt_env": "prod",
        "llm_mode": "live",
        "auth_enabled": True,
        "session_secret": "a-real-secret",
        "cors_origins": ["https://tunatale.example.com"],
        "trusted_proxy_header": "X-Forwarded-For",
        "tz": "America/New_York",
    }
    s = _clean_settings(monkeypatch, tmp_path, **{**base, **overrides})
    problems = prod_profile_problems(s)
    assert problems, f"expected {overrides} to be reported"
    assert any(fragment in p for p in problems), problems


def test_prod_profile_reports_every_problem_at_once(monkeypatch, tmp_path):
    """One boot, one list — not a whack-a-mole of restarts."""
    s = _clean_settings(monkeypatch, tmp_path, tt_env="prod")
    problems = prod_profile_problems(s)
    assert len(problems) == 4, problems  # llm_mode, auth_enabled, session_secret, tz


def test_prod_profile_ignores_wildcard_regex_only_when_scoped(monkeypatch, tmp_path):
    """A SCOPED origin regex is legitimate — only a catch-all is refused."""
    s = _clean_settings(
        monkeypatch,
        tmp_path,
        tt_env="prod",
        llm_mode="live",
        auth_enabled=True,
        session_secret="a-real-secret",
        cors_origins=[],
        cors_allow_origin_regex=r"^https://[a-z0-9-]+\.example\.com$",
        trusted_proxy_header="X-Forwarded-For",
        tz="America/New_York",
    )
    assert prod_profile_problems(s) == []


# ── The clock the PROCESS actually keeps ─────────────────────────────────────
#
# `prod_profile_problems` can only read the setting. Whether the process
# resolves that name to the right offset is a fact about the running image:
# `rollover.py::_local_now` calls bare `.astimezone()`, which asks libc, and
# libc silently falls back to UTC when the zone data is missing. That failure
# is invisible — TZ is set, the name is a real zone, and every clock is still
# wrong — so it gets its own check against the live process.


def test_clock_runtime_problems_silent_when_the_process_keeps_the_named_zone(monkeypatch, tmp_path):
    # The zone is chosen AFTER _clean_settings has removed TZ, so it is picked
    # in the same environment the assertion evaluates in. See the helper.
    s = _clean_settings(monkeypatch, tmp_path)
    s.tz = _zone_agreeing_with_local()
    assert clock_runtime_problems(s) == []


def test_clock_runtime_problems_catches_a_zone_the_process_is_not_keeping(monkeypatch, tmp_path):
    """TZ names a zone, the process resolves a different offset: zone data missing."""
    s = _clean_settings(monkeypatch, tmp_path)
    zone = _zone_disagreeing_with_local()
    s.tz = zone
    problems = clock_runtime_problems(s)
    assert problems, f"{zone} must disagree with this machine's own offset"
    assert zone in problems[0], problems


@pytest.mark.parametrize("tz", ["", "Mars/Olympus_Mons"])
def test_clock_runtime_problems_defers_to_the_profile_check(monkeypatch, tmp_path, tz):
    """Unset or unresolvable is `prod_profile_problems`' story, reported once."""
    s = _clean_settings(monkeypatch, tmp_path, tz=tz)
    assert clock_runtime_problems(s) == []


# ── The startup guard in main ────────────────────────────────────────────────


async def test_lifespan_raises_on_a_misconfigured_prod_boot(tmp_path, monkeypatch):
    """``TT_ENV=prod`` + a mock LLM must RAISE at startup, not warn and serve."""
    from app.config import settings
    from app.main import lifespan

    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setattr(settings, "database_urls", {})
    monkeypatch.setattr(settings, "tt_env", "prod")
    monkeypatch.setattr(settings, "llm_mode", "mock")

    test_app = FastAPI()
    with pytest.raises(RuntimeError, match="llm_mode"):
        async with lifespan(test_app):
            raise AssertionError("lifespan body must not run — the guard raises first")


async def test_lifespan_starts_when_the_prod_profile_is_satisfied(tmp_path, monkeypatch):
    """The guard must let a CORRECT prod boot through.

    Without this the raise-path test alone is satisfied by a guard that refuses
    every prod boot unconditionally — which would pass the suite and make the
    deployment impossible.
    """
    from app.config import settings
    from app.main import lifespan

    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setattr(settings, "database_urls", {})
    monkeypatch.setattr(settings, "tt_env", "prod")
    monkeypatch.setattr(settings, "llm_mode", "live")
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "session_secret", "a-real-secret")
    monkeypatch.setattr(settings, "cors_origins", ["https://tunatale.example.com"])
    monkeypatch.setattr(settings, "trusted_proxy_header", "X-Forwarded-For")
    monkeypatch.setattr(settings, "tz", _zone_agreeing_with_local())
    monkeypatch.setattr(settings, "pipeline_autostart", False)

    test_app = FastAPI()
    async with lifespan(test_app):
        assert test_app.state.srs_db is not None


async def test_lifespan_raises_when_the_process_is_not_keeping_the_configured_zone(tmp_path, monkeypatch):
    """A prod profile that is perfect on paper, on an image with no zone data.

    Everything else passes; only the live clock disagrees. Without this the
    box runs with every SRS day boundary in the wrong place and looks healthy.
    """
    from app.config import settings
    from app.main import lifespan

    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setattr(settings, "database_urls", {})
    monkeypatch.setattr(settings, "tt_env", "prod")
    monkeypatch.setattr(settings, "llm_mode", "live")
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "session_secret", "a-real-secret")
    monkeypatch.setattr(settings, "cors_origins", ["https://tunatale.example.com"])
    monkeypatch.setattr(settings, "trusted_proxy_header", "X-Forwarded-For")
    monkeypatch.setattr(settings, "tz", _zone_disagreeing_with_local())
    monkeypatch.setattr(settings, "pipeline_autostart", False)

    test_app = FastAPI()
    with pytest.raises(RuntimeError, match="TZ"):
        async with lifespan(test_app):
            raise AssertionError("lifespan body must not run — the guard raises first")


async def test_lifespan_starts_normally_when_tt_env_is_unset(tmp_path, monkeypatch):
    """Dev behaviour is untouched: no TT_ENV, no assertion, no new failure mode."""
    from app.config import settings
    from app.main import lifespan

    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setattr(settings, "database_urls", {})
    monkeypatch.setattr(settings, "tt_env", "")
    monkeypatch.setattr(settings, "llm_mode", "mock")
    monkeypatch.setattr(settings, "pipeline_autostart", False)

    test_app = FastAPI()
    async with lifespan(test_app):
        assert test_app.state.srs_db is not None


# ── CORS wiring ──────────────────────────────────────────────────────────────


def test_app_is_not_mounted_with_a_wildcard_origin():
    """The shipped app must not carry the wildcard, whatever settings say.

    This reads the real ``app.main.app`` middleware stack rather than a rebuilt
    one, because the regression being guarded is a literal ``["*"]`` typed back
    into ``add_middleware`` — which a test against a fresh app would never see.
    """
    from app.main import app

    cors = [m for m in app.user_middleware if m.cls is CORSMiddleware]
    assert len(cors) == 1, "expected exactly one CORSMiddleware"
    kwargs = cors[0].kwargs
    assert "*" not in kwargs["allow_origins"]
    assert "*" not in kwargs["allow_methods"]
    assert "*" not in kwargs["allow_headers"]


def test_cors_kwargs_track_settings(monkeypatch, tmp_path):
    """Origins come from settings, not from a constant in the middleware call."""
    from app.config import settings
    from app.main import cors_kwargs

    monkeypatch.setattr(settings, "cors_origins", ["https://example.test"])
    monkeypatch.setattr(settings, "cors_allow_origin_regex", r"^https://ok\.test$")

    kwargs = cors_kwargs()
    assert kwargs["allow_origins"] == ["https://example.test"]
    assert kwargs["allow_origin_regex"] == r"^https://ok\.test$"
    assert kwargs["allow_credentials"] is True
    assert set(kwargs["allow_methods"]) == {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}
    # X-TT-Language is how the frontend picks its language; Range is how audio
    # seeks. Dropping either turns a locked-down deploy into a broken one.
    assert "X-TT-Language" in kwargs["allow_headers"]
    assert "Range" in kwargs["allow_headers"]


def test_cors_kwargs_omit_an_empty_origin_regex(monkeypatch):
    """An empty regex must be dropped, not passed as ``""``.

    Starlette compiles whatever it is given: ``re.compile("")`` matches EVERY
    origin, so passing the empty default straight through would reinstate the
    wildcard while every other assertion here stayed green.
    """
    from app.config import settings
    from app.main import cors_kwargs

    monkeypatch.setattr(settings, "cors_allow_origin_regex", "")
    assert cors_kwargs().get("allow_origin_regex") is None


# ── Through the real middleware ──────────────────────────────────────────────


def _probe_app(**kwargs) -> FastAPI:
    """A minimal app wearing the REAL CORSMiddleware with the given config."""
    probe = FastAPI()
    probe.add_middleware(CORSMiddleware, **kwargs)

    @probe.get("/api/health")
    async def _health():
        return {"status": "ok"}

    return probe


async def _get(app_: FastAPI, origin: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app_)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.get("/api/health", headers={"Origin": origin})


async def test_listed_origin_is_granted():
    from app.main import cors_kwargs

    app_ = _probe_app(**{**cors_kwargs(), "allow_origins": ["https://good.test"], "allow_origin_regex": None})
    res = await _get(app_, "https://good.test")
    assert res.headers.get("access-control-allow-origin") == "https://good.test"
    assert res.headers.get("access-control-allow-credentials") == "true"


async def test_unlisted_origin_is_refused():
    """The evil-page case: no grant header, so the browser withholds the body."""
    from app.main import cors_kwargs

    app_ = _probe_app(**{**cors_kwargs(), "allow_origins": ["https://good.test"], "allow_origin_regex": None})
    res = await _get(app_, "https://evil.test")
    assert "access-control-allow-origin" not in res.headers


async def test_origin_regex_admits_the_tailnet_hostname():
    """MagicDNS names are per-tailnet, so they are configured by regex."""
    from app.main import cors_kwargs

    app_ = _probe_app(
        **{
            **cors_kwargs(),
            "allow_origins": [],
            "allow_origin_regex": r"^https://[a-z0-9-]+\.[a-z0-9-]+\.ts\.net:5173$",
        }
    )
    granted = await _get(app_, "https://mac.tail1234.ts.net:5173")
    assert granted.headers.get("access-control-allow-origin") == "https://mac.tail1234.ts.net:5173"

    # Control: the regex must not be a catch-all wearing a costume.
    refused = await _get(app_, "https://evil.test")
    assert "access-control-allow-origin" not in refused.headers


async def test_preflight_refuses_an_unlisted_origin():
    """A rejected preflight is what stops a cross-origin DELETE reaching a route."""
    from app.main import cors_kwargs

    app_ = _probe_app(**{**cors_kwargs(), "allow_origins": ["https://good.test"], "allow_origin_regex": None})
    transport = httpx.ASGITransport(app=app_)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        res = await client.options(
            "/api/health",
            headers={
                "Origin": "https://evil.test",
                "Access-Control-Request-Method": "DELETE",
            },
        )
    assert "access-control-allow-origin" not in res.headers
