"""The Caddyfile's security-relevant properties, pinned as text (tunatale-1oq).

Nothing in `./test.sh` or CI runs Caddy, so these are the only guard the file
has. The runtime claim — that Caddy accepts the file and serves a trusted
certificate — is verified on the box with `caddy validate` and a real request,
and recorded in docs/deployment.md; this module does not pretend to make it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

CADDYFILE = Path(__file__).resolve().parents[2] / "Caddyfile"


@pytest.fixture(scope="module")
def text() -> str:
    return CADDYFILE.read_text(encoding="utf-8")


def test_the_site_address_is_the_env_var_with_a_plain_http_default(text):
    """`{$SITE_ADDRESS::80}`: the box sets the domain and gets automatic HTTPS; a
    laptop sets nothing and gets the plain :80 it always had. A hardcoded domain
    would make every local `docker compose up` try to obtain a certificate."""
    first_block = next(line for line in text.splitlines() if line.rstrip().endswith("{"))
    assert first_block.strip() == "{$SITE_ADDRESS::80} {", first_block


def test_the_api_is_matched_before_the_spa_fallback(text):
    """The classic misorder: if the SPA's `try_files ... /index.html` handles
    /api/* first, an unknown API path returns 200 and the HTML shell instead of
    the API's 404, and every client error reads as success."""
    assert text.index("handle /api/*") < text.index("try_files")


@pytest.mark.parametrize(
    "header",
    [
        "Strict-Transport-Security",
        "Content-Security-Policy",
        "X-Content-Type-Options",
        "Referrer-Policy",
        "Permissions-Policy",
    ],
)
def test_security_headers_are_set(text, header):
    assert re.search(rf"^\s*{re.escape(header)}\s", text, re.MULTILINE), f"{header} is not set"


def test_the_csp_admits_only_this_origin(text):
    """No third-party origin anywhere: the frontend loads nothing cross-origin
    (measured 2026-09-18), so `connect-src 'self'` is what stops a successful
    injection from shipping data anywhere, and `frame-ancestors 'none'` stops
    the app being framed for clickjacking."""
    csp = re.search(r'Content-Security-Policy\s+"([^"]+)"', text).group(1)
    for directive in ("default-src 'self'", "connect-src 'self'", "frame-ancestors 'none'", "object-src 'none'"):
        assert directive in csp, f"missing {directive!r} in {csp!r}"
    assert "http" not in csp, f"a scheme or host crept into the CSP: {csp!r}"


def test_request_bodies_are_capped_above_the_upload_limit(text):
    """The API rejects image uploads over 10 MB itself (srs_images.py
    _MAX_UPLOAD_BYTES); the proxy cap sits just above it so a legitimate upload
    still reaches the API's own, more informative, error."""
    match = re.search(r"max_size\s+(\d+)MB", text)
    assert match, "no request_body max_size"
    assert 10 < int(match.group(1)) <= 20, match.group(0)
